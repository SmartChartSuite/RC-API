"""End-to-end integration test for the API's main operation: POST /batchjob.

Exercises the full request -> CQL + LLM pipeline -> result Bundle path through
real HTTP endpoints (via FastAPI's TestClient), mocking only the true external
network boundaries with respx:
    - HAPI FHIR (Questionnaire search/get, Library/$evaluate)
    - External FHIR server (Patient, DocumentReference)
    - LiteLLM proxy (chat completions)

All mocked response bodies under tests/fixtures/batchjob_integration/ are real
captures from the project's dev environment: the Questionnaire
SETNETSyphilisInfantFollowUp pared down to the IfuSyphilisComplications CQL
library and the 2025_08/syphilis/ig_exm_skin prompt, run against Patient/625747
(an infant with congenital syphilitic meningitis on the structured side and two
clinical notes -- one with an abnormal skin finding, one normal -- on the
unstructured side). The LLM prompt content itself is loaded for real from
./prompts/ on disk, and each captured LiteLLM completion is the real answer that
note produced.

Because Starlette's TestClient runs the ASGI app in-process, the batch job's
BackgroundTask (run_batch_job) completes synchronously before client.post()
returns -- no polling loop is needed to observe the finished result.

DB state is isolated to a throwaway SQLite file per test (see _isolated_db)
so this test never touches the shared dev DB file.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from main import app
from src.routers import batchjob
from src.services import cql_executor, fhir_context, fhir_proxy, job_state, llm_executor, prompt_loader
from src.services import job_orchestrator
from src.util import auth

FIXTURES = Path(__file__).parent / "fixtures" / "batchjob_integration"

HAPI_BASE = "https://hapi.test/fhir"
EXTERNAL_BASE = "https://external.test/fhir"
LITELLM_BASE = "https://litellm.test"

PATIENT_ID = "625747"
JOB_PACKAGE = "SETNETSyphilisInfantFollowUp"

# The ig_exm_skin prompt runs once per document. Each patient note produces a
# distinct real LLM answer, so the LiteLLM mock is keyed on a substring unique to
# each document's text (llm_executor sends the raw note as the user message).
LLM_COMPLETION_BY_DOC_MARKER = {
    "Sucking blister on left wrist": "llm_completion_625776.json",  # doc 625776 -> Abnormal
    "brought in for this well child visit": "llm_completion_625777.json",  # doc 625777 -> Normal
}


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _llm_side_effect(request: httpx.Request) -> httpx.Response:
    """Return the captured completion matching the document in the request body."""
    body = json.loads(request.content)
    user_message = body["messages"][-1]["content"]
    for marker, fixture in LLM_COMPLETION_BY_DOC_MARKER.items():
        if marker in user_message:
            return httpx.Response(200, json=_load_fixture(fixture))
    raise AssertionError("Unexpected LiteLLM request: no known document marker matched")


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point job_state at a throwaway SQLite file for this test only."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'batchjob_integration.sqlite'}")
    job_state.Base.metadata.create_all(engine)
    monkeypatch.setattr(job_state, "db_engine", engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _configure_services(monkeypatch):
    """Pin every settings-derived global our pipeline touches to a known value.

    Note: importing litellm anywhere (even transitively) triggers its own
    dotenv.load_dotenv(override=False) call, which silently fills in any
    currently-unset env var (e.g. OAUTH2_JWKS_URL, HAPI_FHIR_CQL_EXECUTION_URL,
    LANGFUSE_*) from this repo's real dev .env file. Rather than depend on
    import order to avoid that leak, everything relevant is pinned explicitly
    here so this test is deterministic regardless of what else has been
    imported in the process.
    """
    monkeypatch.setattr(auth, "oauth2_enabled", False)
    monkeypatch.setattr(prompt_loader, "use_langfuse", False)

    monkeypatch.setattr(batchjob, "config_errors", {})
    monkeypatch.setattr(batchjob, "external_fhir_server_url", EXTERNAL_BASE)

    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", HAPI_BASE)
    monkeypatch.setattr(cql_executor, "hapi_fhir_cql_execution_url", HAPI_BASE)
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", EXTERNAL_BASE)
    monkeypatch.setattr(job_orchestrator, "external_fhir_server_url", EXTERNAL_BASE)

    monkeypatch.setattr(job_orchestrator, "use_llm", True)
    monkeypatch.setattr(llm_executor, "use_llm", True)
    monkeypatch.setattr(llm_executor, "litellm_model", "openai/test-model")
    monkeypatch.setattr(llm_executor, "litellm_api_base", LITELLM_BASE)
    monkeypatch.setattr(llm_executor, "litellm_api_key", "test-key")

    # litellm defaults to its own aiohttp-backed transport, which bypasses
    # respx (respx only patches httpx's native transport classes). Force
    # plain httpx so the mocked LiteLLM route below actually gets hit.
    monkeypatch.setattr(llm_executor.litellm, "disable_aiohttp_transport", True)


def _find_entry(entries: list[dict], predicate) -> dict:
    matches = [e for e in entries if predicate(e["resource"])]
    assert len(matches) == 1, f"expected exactly one matching entry, found {len(matches)}"
    return matches[0]


def _find_entries(entries: list[dict], predicate) -> list[dict]:
    return [e for e in entries if predicate(e["resource"])]


def _is_observation(resource: dict) -> bool:
    return resource["resourceType"] == "Observation" and resource.get("id") != "status-observation"


def test_post_batch_job_end_to_end_builds_expected_result_bundle():
    """POST /batchjob -> full CQL+LLM pipeline -> GET result Bundle.

    Everything mocked is a real capture from the dev environment for
    Patient/625747: the pared-down Questionnaire, the CQL $evaluate response
    (congenital syphilitic meningitis present twice; leptomeningitis and
    nephrotic syndrome absent), two DocumentReferences, and the two real
    LiteLLM completions those notes produced (one Abnormal, one Normal skin
    finding). Assertions target the meaningful, deterministic parts of the
    output (counts, codes, statuses, values) while ignoring server-generated IDs.
    """
    questionnaire = _load_fixture("questionnaire.json")
    cql_response = _load_fixture("cql_evaluate_response.json")
    patient = _load_fixture("patient.json")
    document_references = _load_fixture("document_references.json")

    questionnaire_search_bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 1,
        "entry": [{"fullUrl": f"{HAPI_BASE}/Questionnaire/{questionnaire['id']}", "resource": questionnaire}],
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{HAPI_BASE}/Questionnaire", params={"name": JOB_PACKAGE}).mock(return_value=httpx.Response(200, json=questionnaire_search_bundle))
        mock.get(f"{HAPI_BASE}/Questionnaire/{questionnaire['id']}").mock(return_value=httpx.Response(200, json=questionnaire))
        mock.post(f"{HAPI_BASE}/Library/IfuSyphilisComplications/$evaluate").mock(return_value=httpx.Response(200, json=cql_response))
        mock.get(f"{EXTERNAL_BASE}/DocumentReference", params={"subject": f"Patient/{PATIENT_ID}", "_count": "500"}).mock(return_value=httpx.Response(200, json=document_references))
        mock.get(f"{EXTERNAL_BASE}/Patient/{PATIENT_ID}").mock(return_value=httpx.Response(200, json=patient))
        # One LLM call per document; the side effect returns each note's real answer.
        mock.post(f"{LITELLM_BASE}/chat/completions").mock(side_effect=_llm_side_effect)

        client = TestClient(app)

        post_response = client.post(
            "/batchjob",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "patientId", "valueString": PATIENT_ID},
                    {"name": "jobPackage", "valueString": JOB_PACKAGE},
                ],
            },
        )

        assert post_response.status_code == 200, post_response.text
        accepted = post_response.json()
        accepted_params = {p["name"]: p for p in accepted["parameter"]}
        assert accepted_params["batchJobStatus"]["valueString"] == "pending"
        batch_id = accepted_params["batchId"]["valueString"]

        # BackgroundTasks (including run_batch_job) run synchronously within
        # TestClient's in-process ASGI call, so the pipeline has already
        # finished by the time post_response comes back.
        status_response = client.get(f"/batchjob/{batch_id}/status")
        assert status_response.status_code == 200, status_response.text
        status_params = {p["name"]: p for p in status_response.json()["parameter"]}
        assert status_params["batchJobStatus"]["valueString"] == "complete"
        assert status_params["questionnaireResponseStatus"]["valueString"] == "in-progress"

        result_response = client.get(f"/batchjob/{batch_id}")
        assert result_response.status_code == 200, result_response.text

    bundle = result_response.json()
    entries = bundle["entry"]

    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    # status(1) + patient(1) + [2 is_chmeng obs + 2 supporting Conditions +
    # 2 is_chmeng_dt obs] + [2 LLM obs] + [2 DocumentReferences] = 12
    assert bundle["total"] == 12
    assert len(entries) == 12

    # 1. Status Observation -- overall job succeeded (no task errors)
    status_entry = _find_entry(entries, lambda r: r.get("id") == "status-observation")
    assert status_entry["resource"]["status"] == "complete"
    assert status_entry["resource"]["valueCodeableConcept"]["coding"][0]["code"] == "complete"

    # 2. Patient resource included verbatim
    patient_entry = _find_entry(entries, lambda r: r["resourceType"] == "Patient")
    assert patient_entry["resource"]["id"] == PATIENT_ID
    assert patient_entry["resource"]["name"][0]["family"] == "McBaby"

    # 3. Structured (CQL) side: congenital syphilitic meningitis is present as
    # two Conditions; leptomeningitis and nephrotic syndrome are absent.
    chmeng_obs = _find_entries(entries, lambda r: _is_observation(r) and r["code"]["coding"][0]["code"] == "is_chmeng")
    assert len(chmeng_obs) == 2
    chmeng_focus = {o["resource"]["focus"][0]["reference"] for o in chmeng_obs}
    assert chmeng_focus == {"Condition/625766", "Condition/627670"}

    # Supporting Condition resources are included so those focus references resolve.
    condition_entries = _find_entries(entries, lambda r: r["resourceType"] == "Condition")
    assert {c["resource"]["id"] for c in condition_entries} == {"625766", "627670"}
    for cond in condition_entries:
        icd = [c["code"] for c in cond["resource"]["code"]["coding"] if "icd-10-cm" in c.get("system", "")]
        assert icd == ["A52.13"]

    # The paired date element yields one valueString Observation per onset date.
    chmeng_dt_obs = _find_entries(entries, lambda r: _is_observation(r) and r["code"]["coding"][0]["code"] == "is_chmeng_dt")
    assert {o["resource"]["valueString"] for o in chmeng_dt_obs} == {"2151-04-01", "2151-06-01"}

    # Absent complications produce no Observations at all.
    for absent in ("is_lepto", "is_lepto_dt", "is_nephsyn", "is_nephsyn_dt"):
        assert _find_entries(entries, lambda r, code=absent: _is_observation(r) and r["code"]["coding"][0]["code"] == code) == []

    # 4. Unstructured (LLM) side: one Observation per document for ig_exm_skin,
    # each carrying the real captured answer parsed into components. The two
    # notes yield an Abnormal and a Normal finding respectively.
    skin_obs = _find_entries(entries, lambda r: _is_observation(r) and r["code"]["coding"][0]["code"] == "ig_exm_skin")
    assert len(skin_obs) == 2
    by_doc = {o["resource"]["focus"][0]["reference"]: o["resource"] for o in skin_obs}
    assert set(by_doc) == {"DocumentReference/625776", "DocumentReference/625777"}
    for obs in by_doc.values():
        assert obs["code"]["coding"][0]["display"] == "Skin/integument"

    def _result_value(obs: dict) -> str:
        components = {c["code"]["coding"][0]["code"]: c["valueString"] for c in obs["component"]}
        return components["resultValue"]

    abnormal_obs = by_doc["DocumentReference/625776"]
    normal_obs = by_doc["DocumentReference/625777"]
    assert _result_value(abnormal_obs) == "Abnormal"
    assert _result_value(normal_obs) == "Normal"
    assert abnormal_obs["effectiveDateTime"] == "2150-01-01T15:27:08+00:00"

    # 5. Both source DocumentReferences are included so the Observations' focus
    # references resolve.
    docref_entries = _find_entries(entries, lambda r: r["resourceType"] == "DocumentReference")
    assert {d["resource"]["id"] for d in docref_entries} == {"625776", "625777"}
