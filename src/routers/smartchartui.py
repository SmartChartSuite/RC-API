from datetime import datetime
import logging
import os
import uuid
import json
from copy import deepcopy
from fastapi import APIRouter, BackgroundTasks
from src.models.functions import make_operation_outcome, start_jobs
from src.services.jobstate import add_to_batch_jobs, add_to_jobs, get_all_batch_jobs, get_batch_job, get_job, update_job_to_complete
from src.models.models import JobCompletedParameter, ParametersJob, StartJobsParameters
from src.routers.routers import get_form
from src.services.jobhandler import get_job_list_from_form, get_value_from_parameter, update_patient_resource_in_parameters
from src.models.batchjob import BatchParametersJob, StartBatchJobsParameters
from src.responsemodels.prettyjson import PrettyJSONResponse
from fhir.resources.parameters import Parameters
from fhir.resources.patient import Patient

from src.util.fhirclient import FhirClient

logger = logging.getLogger('rcapi.routers.smartchartui')

external_fhir_client = FhirClient(os.getenv('EXTERNAL_FHIR_SERVER_URL'))
internal_fhir_client = FhirClient(os.getenv('CQF_RULER_R4'))

smartchart_router = APIRouter()

'''Read a Patient resource from the external FHIR Server (e.g. Epic)'''
@smartchart_router.get("/smartchartui/patient/{patient_id}", response_class=PrettyJSONResponse)
def read_patient(patient_id: str):
    # TODO: Is there a better way to check of an id is a URL?
    if "/" in patient_id:
        patient_id = extract_patient_id(patient_id)
    response = external_fhir_client.readResource("Patient", patient_id)
    return response

def extract_patient_id(patient_id: str):
    return patient_id.split("/")[-1]

'''
Search for all Group resources on the internal SmartChart FHIR server (ex: SmartChart Suite CQF Ruler) with an implicit include. The include
is handled in this API due to that the Patient resources which are members of the Group exist on a second server.
'''
@smartchart_router.get("/smartchartui/group")
def search_group():
    resource_list = internal_fhir_client.searchResource("Group", flatten=True)
    group_list = deepcopy(resource_list)
    for group in group_list:
        for member in group["member"]:
            print(member["entity"]["reference"].startswith(external_fhir_client.server_base))
            print(external_fhir_client.server_base)
    return resource_list

# TODO: Does this need to exist? Duplciates get form from routers.py. Need to consider if there is another way data may be returned.
@smartchart_router.get("/smartchartui/questionnaire")
def search_questionnaire():
    response = internal_fhir_client.searchResource("Questionnaire", flatten=True)
    return response

@smartchart_router.get("/smartchartui/job/{id}")
def get_job_request(id: str, include_patient: bool = False, response_class=PrettyJSONResponse):
    requested_job = get_job(id)
    if requested_job is None:
        return PrettyJSONResponse(content=make_operation_outcome("not-found", f"The {id} job id was not found. If this is an error, please try running the jobPackage again with a new job id."), status_code=404)
    else:
        return PrettyJSONResponse(content=json.loads(requested_job[0]))

@smartchart_router.get("/smartchartui/batchjob")
def get_all_batch_jobs_request(include_patient: bool = False):
    requested_batch_jobs = get_all_batch_jobs() # TODO: Change this to not return tuple
    requested_batch_jobs = [json.loads(x[0]) for x in requested_batch_jobs]
    batch_jobs_as_resources = []
    if include_patient:
        for batch_job in requested_batch_jobs:
            batch_job_resource = Parameters(**batch_job)
            patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
            patient_resource = Patient(**read_patient(patient_id))
            batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
            batch_jobs_as_resources.append(batch_job_resource.dict())
    else:
        batch_jobs_as_resources = requested_batch_jobs
    return batch_jobs_as_resources

@smartchart_router.get("/smartchartui/batchjob/{id}")
def get_batch_job_request(id: str, include_patient: bool = False, response_class=PrettyJSONResponse):
    requested_batch_job = get_batch_job(id)[0] # TODO: Change this to not return tuple
    requested_batch_job = json.loads(requested_batch_job)
    if include_patient:
        batch_job_resource = Parameters(**requested_batch_job)
        patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
        patient_resource = Patient(**read_patient(patient_id))
        batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
        requested_batch_job = batch_job_resource.dict()
    return requested_batch_job

'''Batch request to run every job in a jobPackage individually'''
# TODO: Support include_patient parameter
@smartchart_router.post("/smartchartui/batchjob")
def post_batch_job(post_body: StartBatchJobsParameters, background_tasks: BackgroundTasks, include_patient: bool = False, response_class=PrettyJSONResponse):
    return start_batch_job(post_body, background_tasks, include_patient)

# TODO: Remove after refactoring more into the jobhandler and jobstate files?
def start_batch_job(post_body, background_tasks: BackgroundTasks, include_patient: bool):
    # Pull "metadata" from the post_body sent by the client.
    form_name = get_value_from_parameter(post_body, "jobPackage")
    patient_id = get_value_from_parameter(post_body, "patientId")

    # Setup base for the new batch job model. (Without jobs added yet.)
    new_batch_job = BatchParametersJob()
    batch_id_param_index = new_batch_job.parameter.index([param for param in new_batch_job.parameter if param.name == "batchId"][0])
    starttime_param_index = new_batch_job.parameter.index([param for param in new_batch_job.parameter if param.name == "jobStartDateTime"][0])
    patient_id_param_index = new_batch_job.parameter.index([param for param in new_batch_job.parameter if param.name == "patientId"][0])
    job_package_param_index = new_batch_job.parameter.index([param for param in new_batch_job.parameter if param.name == "jobPackage"][0])
    child_jobs_param_index = new_batch_job.parameter.index([param for param in new_batch_job.parameter if param.name == "childJobs"][0])
    new_batch_job.parameter[batch_id_param_index].valueString = str(uuid.uuid4())
    new_batch_job.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_batch_job.parameter[patient_id_param_index].valueString = patient_id # TODO: Add fully qualified URL
    new_batch_job.parameter[job_package_param_index].valueString = form_name

    # Get the form based on the form_name in the post-body jobPackage parameter, then extract a list of all jobs.
    form = get_form(form_name)
    job_list: list[str] = get_job_list_from_form(form)
    
    # Build a temporary list of start job post bodies, one for each job from the job_list identified.
    #TODO: Refactor to not require internal start bodies
    start_bodies = [temp_start_job_body(patient_id, form_name, job) for job in job_list]
    
    # Temporary holder for the list of responses to include in the batch job response
    child_job_ids = [start_child_job_task(start_body, background_tasks) for start_body in start_bodies]

    list_resource = create_list_resource(child_job_ids)
    new_batch_job.parameter[child_jobs_param_index].resource = list_resource

    added: bool = add_to_batch_jobs(new_batch_job, new_batch_job.parameter[batch_id_param_index].valueString)
    
    batch_job_resource = Parameters(**new_batch_job.model_dump())

    if include_patient:
        patient_id = get_value_from_parameter(batch_job_resource, "patientId", use_iteration_strategy=True, value_key="valueString")
        patient_resource = Patient(**read_patient(patient_id))
        batch_job_resource = update_patient_resource_in_parameters(batch_job_resource, patient_resource)
        batch_job_resource = batch_job_resource.dict()
    
    # TODO: Fix issue dumping JSON to content to include complete header info
    # return PrettyJSONResponse(batch_job_resource, headers={"Location": f"/smartchartui/batchjob/{new_batch_job.parameter[batch_id_param_index].valueString}"})
    return batch_job_resource

def start_child_job_task(start_body,  background_tasks: BackgroundTasks):
    new_job = ParametersJob()
    uid_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobId"][0])

    #TODO: Temporary fix to new_job creating duplicate UUIDs
    new_uuid = uuid.uuid4()
    new_job.parameter[uid_param_index].valueString = new_uuid

    job_id = str(new_job.parameter[uid_param_index].valueString)
    background_tasks.add_task(run_child_job, new_job, job_id, start_body)
    return job_id

def run_child_job(new_job: ParametersJob, job_id: str, start_body):
    tmp_job_obj = new_job
    starttime_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobStartDateTime"][0])
    tmp_job_obj.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    job_added_bool = add_to_jobs(new_job, job_id)
    if job_added_bool:
        logger.info(f"Created new job with jobId {job_id}")  # type: ignore
    else:
        logger.error(f"Error creating job with jobId {job_id}")
    job_result = start_jobs(start_body)
    update_job_to_complete(job_id, job_result)


def temp_start_job_body(patient_id: str, job_package: str, job: str):
    start_job_parameters = StartJobsParameters.model_validate(
        {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "patientId",
                "valueString": f"{patient_id}"
            },
            {
                "name": "jobPackage",
                "valueString": f"{job_package}"
            },
            {
                "name": "job",
                "valueString": f"{job}"
            }
        ]
    }
    )
    return start_job_parameters

def create_list_resource(job_id_list: list[str]):
    list_resource = {
        "resourceType": "List",
        "status": "current",
        "mode": "working",
        "entry": []
    }
    for job_id in job_id_list:
        list_resource["entry"].append({"item": {"display": job_id}})
    return list_resource