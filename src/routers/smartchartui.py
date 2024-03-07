from datetime import datetime
import logging
import os
import uuid
from fastapi import APIRouter, BackgroundTasks
from src.models.models import ParametersJob
from src.routers.routers import get_form
from src.services.jobhandler import get_job_list_from_form, get_value_from_parameter
from src.models.batchjob import BatchParametersJob, StartBatchJobsParameters
from src.responsemodels.prettyjson import PrettyJSONResponse

from src.util.fhirclient import FhirClient

logger = logging.getLogger('rcapi.routers.smartchartui')

external_fhir_client = FhirClient(os.getenv('EXTERNAL_FHIR_SERVER_URL'))
internal_fhir_client = FhirClient(os.getenv('CQF_RULER_R4'))

smartchart_router = APIRouter()

'''Read a Patient resource from the external FHIR Server (e.g. Epic)'''
@smartchart_router.get("/smartchartui/patient/{patient_id}", response_class=PrettyJSONResponse)
def read_patient(patient_id: str):
    print(external_fhir_client.server_base)
    response = external_fhir_client.readResource("Patient", patient_id)
    return response

'''Search for all Group resources on the internal SmartChart FHIR server (ex: SmartChart Suite CQF Ruler)'''
@smartchart_router.get("/smartchartui/group")
def search_group():
    response = internal_fhir_client.searchResource("Group", flatten=True)
    return response

# TODO: Does this need to exist? Duplciates get form from routers.py. Need to consider if there is another way data may be returned.
@smartchart_router.get("/smartchartui/questionnaire")
def search_questionnaire():
    response = internal_fhir_client.searchResource("Questionnaire", flatten=True)
    return response

@smartchart_router.get("/smartchartui/batchjob/{id}")
def get_batch_job(id: str, response_class=PrettyJSONResponse):
    requested_batch_job = get_batch_job(id)
    return requested_batch_job

'''Batch request to run every job in a jobPackage individually'''
@smartchart_router.post("/smartchartui/batchjob")
def post_batch_job(post_body: StartBatchJobsParameters,  background_tasks: BackgroundTasks, response_class=PrettyJSONResponse):
    return start_batch_job(post_body, background_tasks)

# TODO: Remove after refactoring more into the jobhandler and jobstate files?
def start_batch_job(post_body, background_tasks: BackgroundTasks):
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
    child_job_id_list = [start_job(start_body, background_tasks) for start_body in start_bodies]
    list_resource = create_list_resource(child_job_id_list)
    new_batch_job.parameter[child_jobs_param_index].resource = list_resource

    return PrettyJSONResponse(content=new_batch_job.model_dump(), headers={"Location": f"/smartchartui/batchjob/{new_batch_job.parameter[batch_id_param_index].valueString}"})

def start_job(start_body, background_tasks: BackgroundTasks):
    new_job = ParametersJob()
    uid_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobId"][0])
    starttime_param_index = new_job.parameter.index([param for param in new_job.parameter if param.name == "jobStartDateTime"][0])
    new_job.parameter[uid_param_index].valueString = str(uuid.uuid4())
    new_job.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(f"Created new job with jobId {new_job.parameter[uid_param_index].valueString}")  # type: ignore
    #jobs[new_job.parameter[uid_param_index].valueString] = new_job  # type: ignore
    #logger.info("Added to jobs array")
    #background_tasks.add_task(start_async_jobs, post_body, new_job.parameter[uid_param_index].valueString)  # type: ignore
    #logger.info("Added background task")
    return new_job.parameter[uid_param_index].valueString


def temp_start_job_body(patient_id: str, job_package: str, job: str):
    return {
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

def create_list_resource(job_id_list: list[str]):
    list_resource = {
        "resourceType": "List",
        "status": "current",
        "mode": "working",
        "entry": []
    }
    for job_id in job_id_list:
        list_resource["entry"].append({"display": job_id})
    return list_resource