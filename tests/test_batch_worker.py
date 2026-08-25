import asyncio

from src.services import batch_worker
from src.services.job_state import ClaimedBatchJob


def _claim() -> ClaimedBatchJob:
    return ClaimedBatchJob(
        batch_id="batch-1",
        patient_id="patient-1",
        job_package="Registry",
        questionnaire_id="questionnaire-1",
        job_package_version="2026.1",
        requested_jobs=["TaskA"],
        worker_id="worker-1",
        attempt_count=2,
        result_bundle={"resourceType": "Bundle", "id": "bundle-1"},
    )


async def test_worker_executes_claim_with_persisted_context(monkeypatch):
    worker = batch_worker.EmbeddedBatchWorker("worker-1")
    claim = _claim()
    calls = []

    monkeypatch.setattr(batch_worker, "recover_expired_batch_job_leases", lambda delay: ([], []))
    monkeypatch.setattr(batch_worker, "claim_next_batch_job", lambda worker_id, lease: claim)

    async def _run_batch_job(*args, **kwargs):
        calls.append((args, kwargs))
        worker._stop_event.set()

    monkeypatch.setattr(batch_worker, "run_batch_job", _run_batch_job)

    await worker._run()

    assert calls == [
        (
            ("batch-1", "patient-1", "Registry", "questionnaire-1", "2026.1", ["TaskA"]),
            {
                "worker_id": "worker-1",
                "attempt_count": 2,
                "prior_bundle": {"resourceType": "Bundle", "id": "bundle-1"},
            },
        )
    ]


async def test_worker_hands_execution_failure_to_retry_state(monkeypatch):
    worker = batch_worker.EmbeddedBatchWorker("worker-1")
    claim = _claim()
    failures = []

    monkeypatch.setattr(batch_worker, "recover_expired_batch_job_leases", lambda delay: ([], []))
    monkeypatch.setattr(batch_worker, "claim_next_batch_job", lambda worker_id, lease: claim)
    monkeypatch.setattr(
        batch_worker,
        "fail_batch_job_attempt",
        lambda *args: failures.append(args) or "retry",
    )

    async def _run_batch_job(*args, **kwargs):
        worker._stop_event.set()
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(batch_worker, "run_batch_job", _run_batch_job)

    await worker._run()

    assert failures == [
        (
            "batch-1",
            "worker-1",
            2,
            "temporary failure",
            batch_worker.batch_job_retry_delay_seconds,
        )
    ]


async def test_worker_stop_releases_active_claim(monkeypatch):
    worker = batch_worker.EmbeddedBatchWorker("worker-1")
    claim = _claim()
    started = asyncio.Event()
    blocked = asyncio.Event()
    released = []
    claimed = False

    monkeypatch.setattr(batch_worker, "recover_expired_batch_job_leases", lambda delay: ([], []))

    def _claim_once(worker_id, lease):
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return claim

    monkeypatch.setattr(batch_worker, "claim_next_batch_job", _claim_once)
    monkeypatch.setattr(
        batch_worker,
        "release_batch_job_attempt",
        lambda *args: released.append(args) or True,
    )

    async def _run_batch_job(*args, **kwargs):
        started.set()
        await blocked.wait()

    monkeypatch.setattr(batch_worker, "run_batch_job", _run_batch_job)

    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await worker.stop()

    assert released == [("batch-1", "worker-1", 2)]
