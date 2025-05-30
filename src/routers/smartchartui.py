import json
import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.list import List
from fhir.resources.R4B.parameters import Parameters
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.operationoutcome import OperationOutcome

from src.models.batchjob import BatchParametersJob, StartBatchJobsParameters
from src.models.forms import get_form
from src.models.functions import get_param_index, make_operation_outcome, start_jobs
from src.models.models import ParametersJob, StartJobsParameters
from src.responsemodels.prettyjson import PrettyJSONResponse
from src.services.jobhandler import get_job_list_from_form, get_value_from_parameter, update_patient_resource_in_parameters
from src.services.jobstate import add_to_batch_jobs, add_to_jobs, delete_batch_job, get_all_batch_jobs, get_batch_job, get_child_job_statuses, get_job, update_job_to_complete
from src.util.fhirclient import FhirClient
from src.util.settings import session

logger: logging.Logger = logging.getLogger("rcapi.routers.smartchartui")

external_fhir_client = FhirClient(os.getenv("EXTERNAL_FHIR_SERVER_URL"))
internal_fhir_client = FhirClient(os.getenv("CQF_RULER_R4"))

smartchart_router = APIRouter()


@smartchart_router.get("/smartchartui/Patient/{patient_id}", response_class=PrettyJSONResponse)
def read_patient(patient_id: str):
    """Read a Patient resource from the external FHIR Server (e.g. Epic)"""
    if "/" in patient_id:
        patient_id = extract_patient_id(patient_id)
    response = external_fhir_client.readResource("Patient", patient_id)
    return response


def extract_patient_id(patient_id: str):
    return patient_id.split("/")[-1]


@smartchart_router.get("/smartchartui/Patient", response_class=PrettyJSONResponse)
def search_patient(identifier: str | None = None, name: str | None = None, birthdate: str | None = None, _id: str | None = None):
    if _id:
        return external_fhir_client.searchResource("Patient", parameters={"_id": _id})
    if identifier:
        return external_fhir_client.searchResource("Patient", parameters={"identifier": identifier})
    if name and birthdate:
        return external_fhir_client.searchResource("Patient", parameters={"name": name, "birthdate": birthdate})
    if name:
        return external_fhir_client.searchResource("Patient", parameters={"name": name})
    if birthdate:
        return external_fhir_client.searchResource("Patient", parameters={"birthdate": birthdate})

    return external_fhir_client.searchResource("Patient")


@smartchart_router.get("/smartchartui/group")
def search_group():
    """
    Search for all Group resources on the internal SmartChart FHIR server (ex: SmartChart Suite CQF Ruler) with an implicit include. The include
    is handled in this API due to that the Patient resources which are members of the Group exist on a second server.
    """
    logger.info(f"Looking for groups on server {internal_fhir_client.server_base}")
    resource_list: list = internal_fhir_client.searchResource("Group", flatten=True)
    group_list: list = deepcopy(resource_list)
    output_list: list = []
    for group in group_list:
        output_list.append(group)
        for member in group["member"]:
            patient_reference: str = member["entity"]["reference"]
            try:
                patient_resource = session.get(patient_reference).json()
                output_list.append(patient_resource)
            except Exception:
                logger.error(f"There was an issue collecting the Patient resource from the following URL: {patient_reference}")
    logger.info(f"Returning {len(group_list)} Group(s) with a total of {len(output_list) - len(group_list)} Patient(s)")
    return output_list


# TODO: Does this need to exist? Duplciates get form from routers.py. Need to consider if there is another way data may be returned.
@smartchart_router.get("/smartchartui/questionnaire")
def search_questionnaire():
    response = internal_fhir_client.searchResource("Questionnaire?context=smartchartui", flatten=True)
    return response


@smartchart_router.get("/smartchartui/job/{id}")
def get_job_request(id: str, include_patient: bool = False, response_class=PrettyJSONResponse):
    requested_job = get_job(id)
    if requested_job is None:
        return PrettyJSONResponse(
            content=make_operation_outcome("not-found", f"The {id} job id was not found. If this is an error, please try running the jobPackage again with a new job id."), status_code=404
        )
    else:
        return PrettyJSONResponse(content=requested_job)


@smartchart_router.get("/smartchartui/batchjob")
def get_all_batch_jobs_request(include_patient: bool = False):
    requested_batch_jobs = get_all_batch_jobs()
    batch_jobs_as_resources = []
    if include_patient:
        for batch_job in requested_batch_jobs:
            batch_job_resource = Parameters(**batch_job)
            patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
            if not patient_id:
                continue
            patient_resource = Patient(**read_patient(patient_id))
            batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
            batch_jobs_as_resources.append(batch_job_resource.dict())
    else:
        batch_jobs_as_resources = requested_batch_jobs
    return [add_status_to_batch_job(batch_job) for batch_job in batch_jobs_as_resources]


@smartchart_router.get("/smartchartui/batchjob/{id}")
def get_batch_job_request(id: str, include_patient: bool = False, response_class=PrettyJSONResponse):
    requested_batch_job: dict | None = get_batch_job(id)
    if not requested_batch_job:
        return JSONResponse(make_operation_outcome("not-found", f"Batch Job ID {id} was not found in the database"), 404)
    if include_patient:
        batch_job_resource = Parameters(**requested_batch_job)
        patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
        if not patient_id:
            return {}
        patient_resource = Patient(**read_patient(patient_id))
        batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
        requested_batch_job = batch_job_resource.dict()
    return requested_batch_job


@smartchart_router.delete("/smartchartui/batchjob/{id}")
def delete_batch_job_endpoint(id: str):
    return delete_batch_job(id)


@smartchart_router.get("/smartchartui/results/{id}")
def get_batch_job_results(id: str):
    """Fetches compiled results as a FHIR Bundle for a given job."""

    requested_batch_job = get_batch_job(id)
    if not requested_batch_job:
        return JSONResponse(make_operation_outcome("not-found", f"Batch Job ID {id} was not found in the database"), 404)

    batch_job_resource = Parameters(**requested_batch_job)

    # TODO: Swap the list structure in batch jobs to a parameters.parts structure with name = job, and use it to tie together things here to generate the components properly.
    # TODO: Per above, temp handling given an all statuses complete.
    # 1. Get child job IDs.
    child_job_list_resource: List = get_value_from_parameter(batch_job_resource, "childJobs", use_iteration_strategy=True, value_key="resource")
    child_job_list_dict = child_job_list_resource.dict()
    child_job_list_dict_entries = child_job_list_dict["entry"]
    child_job_ids: list = [entry["item"]["display"] for entry in child_job_list_dict_entries]

    # 2. For each child job ID
    #   a. read from DB.
    #   b. extract status parameter
    #   c. Add status parameter along with job
    #   b. extract results parameter (results bundle)
    #   c. compile results bundle into single list, removing duplicates.
    status_list: list = []
    result_list: list = []
    for job_id in child_job_ids:
        logger.info(f"Reading job {job_id} from Database.")
        job = get_job(job_id)
        if job is not None:
            try:
                job_parameters_resource = Parameters(**job)
                status_list.append(get_value_from_parameter(job_parameters_resource, "jobStatus", use_iteration_strategy=True, value_key="valueString"))
                result: Bundle = get_value_from_parameter(job_parameters_resource, "result", use_iteration_strategy=True, value_key="resource")
                if result.entry:
                    for entry in result.entry:
                        result_list.append(entry.resource.json())
                else:
                    result_list = []
            except BaseException as e:
                if isinstance(result, OperationOutcome):
                    logger.error("OperationOutcome found in job results, reporting diagnostics strings:")
                    for issue in result.issue:
                        logger.error("    " + issue.diagnostics)
                else:
                    logger.error(e)
                logger.error(f"Error parsing job: {job_id}")
        else:
            status_list.append("inProgress")
        result_list = list(dict.fromkeys(result_list))

    # 3. Create status observation based on TODOs above
    #   a. Set Observation.status per condotions of all child job.
    #   b. Once TODOs addressed, add components with job/status pairs for individual handling.

    status_list_bool = [status == "complete" for status in status_list]
    overall_status_bool = all(status_list_bool)
    overall_status: str = ""
    if overall_status_bool:
        overall_status = "complete"
    else:
        overall_status = "preliminary"
    status_counter = f"{len([status for status in status_list if status == 'complete'])}/{len(child_job_ids)}"
    status_observation = create_results_status_observation(overall_status, status_counter)

    # 4. Create collection bundle wrapper.
    #   a. Insert status observation into first entry of collection bundle wrapper.
    #   b. Call data source and get Patinet and insert into entries of collection bundle wrapper.
    #   c. Insert all other resources not of type Patient into the entries of collection bundle wrapper.
    # bundle = Bundle(**create_results_bundle(status_observation, result_list))
    bundle = create_results_bundle(status_observation, result_list)

    # 5. Return bundle to user.
    return PrettyJSONResponse(bundle)


@smartchart_router.post("/smartchartui/batchjob", response_class=PrettyJSONResponse)
def post_batch_job(post_body: StartBatchJobsParameters, background_tasks: BackgroundTasks, include_patient: bool = False):
    return start_batch_job(post_body, background_tasks, include_patient)


# TODO: Remove after refactoring more into the jobhandler and jobstate files?
def start_batch_job(post_body, background_tasks: BackgroundTasks, include_patient: bool):
    # Pull "metadata" from the post_body sent by the client.
    form_name: str = get_value_from_parameter(post_body, "jobPackage")
    patient_id = get_value_from_parameter(post_body, "patientId")

    # Setup base for the new batch job model. (Without jobs added yet.)
    new_batch_job = BatchParametersJob()
    batch_id_param_index = get_param_index(new_batch_job.parameter, "batchId")
    starttime_param_index = get_param_index(new_batch_job.parameter, "jobStartDateTime")
    patient_id_param_index = get_param_index(new_batch_job.parameter, "patientId")
    job_package_param_index = get_param_index(new_batch_job.parameter, "jobPackage")
    child_jobs_param_index = get_param_index(new_batch_job.parameter, "childJobs")

    new_batch_job_batch_id = str(uuid.uuid4())
    new_batch_job.parameter[batch_id_param_index].valueString = new_batch_job_batch_id
    new_batch_job.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_batch_job.parameter[patient_id_param_index].valueString = patient_id  # TODO: Add fully qualified URL
    new_batch_job.parameter[job_package_param_index].valueString = form_name

    # Get the form based on the form_name in the post-body jobPackage parameter, then extract a list of all jobs.
    form = get_form(form_name=form_name, form_version=None, return_Questionnaire_class_obj=False)
    job_list: list[str] = get_job_list_from_form(form)

    # Build a temporary list of start job post bodies, one for each job from the job_list identified.
    # TODO: Refactor to not require internal start bodies
    start_bodies = [temp_start_job_body(patient_id, form_name, job) for job in job_list]

    # Temporary holder for the list of responses to include in the batch job response
    child_job_ids = [start_child_job_task(start_body=start_body, parent_batch_job_id=new_batch_job_batch_id, background_tasks=background_tasks) for start_body in start_bodies]

    list_resource = create_list_resource(child_job_ids)
    new_batch_job.parameter[child_jobs_param_index].resource = list_resource

    added: bool = add_to_batch_jobs(new_batch_job, new_batch_job.parameter[batch_id_param_index].valueString)

    if not added:
        return JSONResponse(
            make_operation_outcome(
                "processing",
                "The Batch Job was unable to be added to the database. This would most likely occur if a new UUID was generated that matches one already existing in the database. "
                "This shouldn't happen, but is here for error handling. Please see logs for further information and debugging.",
            ),
            500,
        )

    batch_job_resource = Parameters(**new_batch_job.model_dump())

    if include_patient:
        patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
        patient_resource = Patient(**read_patient(patient_id))
        batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
        batch_job_resource = batch_job_resource.dict(exclude_none=True)

    if isinstance(batch_job_resource, Parameters):
        batch_job_resource = batch_job_resource.dict(exclude_none=True)

    return PrettyJSONResponse(
        batch_job_resource,
        headers={"Location": f"/smartchartui/batchjob/{new_batch_job.parameter[batch_id_param_index].valueString}"},
    )


def start_child_job_task(start_body: StartJobsParameters, parent_batch_job_id, background_tasks: BackgroundTasks):
    new_job = ParametersJob()
    uid_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobId"][0])

    # TODO: Temporary fix to new_job creating duplicate UUIDs
    new_uuid = uuid.uuid4()
    new_job.parameter[uid_param_index].valueString = str(new_uuid)

    job_id = str(new_job.parameter[uid_param_index].valueString)
    background_tasks.add_task(run_child_job, new_job, job_id, parent_batch_job_id, start_body)
    return job_id


def run_child_job(new_job: ParametersJob, job_id: str, parent_batch_job_id: str, start_body: StartJobsParameters):
    tmp_job_obj = new_job
    starttime_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobStartDateTime"][0])
    tmp_job_obj.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    start_body_dump = start_body.model_dump()
    patient_id_key = [item["name"] for item in start_body_dump["parameter"] if item["name"].startswith("patient")][0]
    patient_id = [item["valueString"] for item in start_body_dump["parameter"] if item["name"] == patient_id_key][0]
    job_package = [item["valueString"] for item in start_body_dump["parameter"] if item["name"] == "jobPackage"][0]
    job_added_bool = add_to_jobs(
        new_job_body=new_job,
        job_id=job_id,
        patient_id_type=patient_id_key,
        patient_id=patient_id,
        job_package=job_package,
        parent_batch_job_id=parent_batch_job_id,
        job_start_datetime=tmp_job_obj.parameter[starttime_param_index].valueDateTime,
        job_status="inProgress",
    )
    if job_added_bool:
        logger.info(f"Created new job with jobId {job_id}")
    else:
        logger.error(f"Error creating job with jobId {job_id}")
    job_result = start_jobs(start_body)
    update_job_to_complete(job_id, job_result)


def temp_start_job_body(patient_id: str, job_package: str, job: str):
    start_job_parameters = StartJobsParameters.model_validate(
        {
            "resourceType": "Parameters",
            "parameter": [{"name": "patientId", "valueString": f"{patient_id}"}, {"name": "jobPackage", "valueString": f"{job_package}"}, {"name": "job", "valueString": f"{job}"}],
        }
    )
    return start_job_parameters


def create_list_resource(job_id_list: list[str]):
    list_resource = {"resourceType": "List", "status": "current", "mode": "working", "entry": []}
    for job_id in job_id_list:
        list_resource["entry"].append({"item": {"display": job_id}})
    return list_resource


def create_results_status_observation(status: str, status_count: str):
    status_code = "in-progress"
    if status == "complete":
        status_code = status
    return {
        "resourceType": "Observation",
        "id": "status-observation",
        "status": status,
        "code": {"coding": [{"code": "result-status"}]},
        "valueCodeableConcept": {"coding": [{"code": status_code}], "text": f"Jobs completed: {status_count}"},
    }


def create_results_bundle(status_observation, results_list: list):
    patient_id: str = ""
    for res in results_list:
        if isinstance(res, str):
            res = json.loads(res)
        if res["resourceType"] == "Patient":
            patient_id = res["id"]
    if patient_id:
        true_patient = external_fhir_client.readResource("Patient", patient_id)
        return_entries = [create_bundle_entry(resource) for resource in results_list if not create_bundle_entry(resource)["fullUrl"].startswith("Patient")]
        return_entries.insert(0, create_bundle_entry(status_observation))
        return_entries.insert(1, create_bundle_entry(true_patient))
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "total": len(results_list),
            "entry": return_entries,
        }
    else:
        results_list.insert(0, status_observation)
        return {"resourceType": "Bundle", "type": "collection", "total": len(results_list), "entry": [create_bundle_entry(resource) for resource in results_list]}


def create_bundle_entry(resource):
    if isinstance(resource, str):
        resource = json.loads(resource)
    return {"fullUrl": f"{resource['resourceType']}/{resource['id']}", "resource": resource}


def add_status_to_batch_job(batch_job: dict) -> dict:
    new_params: list[dict] = batch_job["parameter"]
    batch_job_id = new_params[get_param_index(new_params, "batchId")]["valueString"]

    child_job_statuses = get_child_job_statuses(batch_job_id=batch_job_id)
    complete_bool = all([value == "complete" for value in child_job_statuses.values()])
    new_params.insert(4, {"name": "batchJobStatus", "valueString": "complete" if complete_bool else "inProgress"})
    batch_job["parameter"] = new_params

    return batch_job
