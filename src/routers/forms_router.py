"""Routing file for form-related operations"""

import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body
from fastapi.responses import JSONResponse
from fastapi_restful.tasks import repeat_every

from src.models.forms import convert_jobpackage_csv_to_questionnaire, get_form
from src.models.functions import get_param_index, make_operation_outcome, start_jobs
from src.models.models import JobCompletedParameter, ParametersJob, StartJobsParameters
from src.util.settings import cqfr4_fhir, session

logger: logging.Logger = logging.getLogger("rcapi.routers.forms_router")

router = APIRouter()

jobs: dict[str, ParametersJob] = {}


def init_jobs_array() -> None:
    global jobs
    del jobs
    jobs = {}  # noqa: F841
    logger.info("Initialized jobs array")


@repeat_every(seconds=60 * 60 * 24, logger=logger)
def clear_jobs_array() -> None:
    logger.info("Clearing jobs array...")
    global jobs
    del jobs
    jobs = {}  # noqa: F841
    logger.info("Finished clearing jobs!")


@router.get("/forms", response_model=dict)
def get_list_of_forms():
    """Get Bundle of Questionnaires from CQF Ruler"""
    cqfr4_fhir_url = os.environ["CQF_RULER_R4"]
    # Pull list of forms from CQF Ruler
    if cqfr4_fhir_url[-5:] == "fhir/":
        pass
    elif cqfr4_fhir_url[-4:] == "fhir":
        cqfr4_fhir_url = cqfr4_fhir_url + "/"
    else:
        return make_operation_outcome("invalid", f"The CQF Ruler url ({cqfr4_fhir_url}) passed in as an environmental variable is not correct, please check that it ends with fhir or fhir/")  # type:ignore
    req = session.get(cqfr4_fhir_url + "Questionnaire")
    if req.status_code == 200:
        return req.json()
    logger.error(f"Getting Questionnaires from server failed with code {req.status_code}")
    return make_operation_outcome("transient", f"Getting Questionnaires from server failed with code {req.status_code}.")


@router.get("/forms/{form_name}")
def get_form_endpoint(form_name: str) -> dict:
    return get_form(form_name=form_name, form_version=None, return_Questionnaire_class_obj=False)


@router.post("/forms")
def save_form(questions: dict):
    """Check to see if library and version of this exists"""

    req = session.get(cqfr4_fhir + f"Questionnaire?name:exact={questions['name']}&version={questions['version']}")
    if req.status_code != 200:
        logger.error(f"Trying to get Questionnaire from server failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Getting Questionnaire from server failed with code {req.status_code}")

    search_bundle = req.json()
    try:
        questionnaire_current_id = search_bundle["entry"][0]["resource"]["id"]
        logger.info(f"Found Questionnaire with name {questions['name']} and version {questions['version']}")
        logger.info("Not completing POST operation because a Questionnaire with that name and version already exist on this FHIR Server")
        logger.info("Change Questionnaire name or version number or use PUT to update this version")
        return make_operation_outcome("duplicate", f"There is already a Questionnaire with this name with resource id {questionnaire_current_id}")
    except KeyError:
        logger.info("Questionnaire with that name not found, continuing POST operation")

    # Create Questionnaire in CQF Ruler
    req = session.post(cqfr4_fhir + "Questionnaire", json=questions)
    if req.status_code != 201:
        logger.error(f"Posting Questionnaire to server failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Posting Questionnaire to server failed with code {req.status_code}")

    resource_id = req.json()["id"]
    return make_operation_outcome("informational", f"Resource successfully posted with id {resource_id}", severity="information")


@router.post("/forms/start", response_model=None)
def start_jobs_header_function(post_body: StartJobsParameters, background_tasks: BackgroundTasks, asyncFlag: bool = False) -> JSONResponse | dict:
    """Header function for starting jobs either synchronously or asynchronously"""
    if asyncFlag:
        logger.info("asyncFlag detected, running asynchronously")
        new_job = ParametersJob()
        uid_param_index = get_param_index(parameter_list=new_job.parameter, param_name="jobId")
        starttime_param_index = get_param_index(parameter_list=new_job.parameter, param_name="jobStartDateTime")
        new_job.parameter[uid_param_index].valueString = str(uuid.uuid4())
        new_job.parameter[starttime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f"Created new job with jobId {new_job.parameter[uid_param_index].valueString}")  # type: ignore
        jobs[new_job.parameter[uid_param_index].valueString] = new_job  # type: ignore
        logger.info("Added to jobs array")
        background_tasks.add_task(start_async_jobs, post_body, new_job.parameter[uid_param_index].valueString)  # type: ignore
        logger.info("Added background task")
        return JSONResponse(content=new_job.model_dump(exclude_none=True), headers={"Location": f"/forms/status/{new_job.parameter[uid_param_index].valueString}"})  # type: ignore

    return start_jobs(post_body)


def start_async_jobs(post_body: StartJobsParameters, uid: str) -> None:
    """Start job asychronously"""
    job_result = start_jobs(post_body)
    if uid not in jobs:
        new_job = ParametersJob()
        uid_param_index: int = get_param_index(parameter_list=new_job.parameter, param_name="jobId")
        starttime_param_index: int = get_param_index(parameter_list=new_job.parameter, param_name="jobStartDateTime")
        new_job.parameter[uid_param_index].valueString = uid
        new_job.parameter[starttime_param_index].valueDateTime = "9999-12-31T00:00:00Z"
        jobs[uid] = new_job

    tmp_job_obj = jobs[uid]

    status_param_index: int = get_param_index(parameter_list=tmp_job_obj.parameter, param_name="jobStatus")
    result_param_index: int = get_param_index(parameter_list=tmp_job_obj.parameter, param_name="result")

    jobs[uid].parameter[result_param_index].resource = job_result
    jobs[uid].parameter[status_param_index].valueString = "complete"

    jobs[uid].parameter.append(JobCompletedParameter(valueDateTime=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")))

    logger.info(f"Job id {uid} complete and results are available at /forms/status/{uid}")


@router.get("/forms/status/all")
def return_all_jobs():
    """Return all job statuses"""
    return jobs


@router.get("/forms/status/{uid}")
def get_job_status(uid: str):
    """Return the status of a specific job"""
    try:
        try:
            job_status_obj = jobs[uid]
            result_param_index: int = get_param_index(parameter_list=job_status_obj.parameter, param_name="result")
            job_results = job_status_obj.parameter[result_param_index].resource
            job_results_severity = job_results["issue"][0]["severity"] if job_results else "unknown"
            job_results_code = job_results["issue"][0]["code"] if job_results else "unknown"
            if job_results_code == "not-found":
                return JSONResponse(status_code=404, content=job_results)
            if job_results_severity == "error":
                return JSONResponse(status_code=500, content=job_results)
            else:
                return jobs[uid]
        except KeyError:
            return jobs[uid]
    except KeyError:
        return JSONResponse(
            content=make_operation_outcome("code-invalid", f"The {uid} job id was not found as an async job. Please try running the jobPackage again with a new job id."), status_code=404
        )


@router.put("/forms/{form_name}")
def update_form(form_name: str, new_questions: dict):
    """Update Questionnaire using name"""
    req = session.get(cqfr4_fhir + f"Questionnaire?name:exact={form_name}")
    if req.status_code != 200:
        logger.error(f"Getting Questionnaire from server failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Getting Questionnaire from server failed with status code {req.status_code}")

    search_bundle = req.json()
    try:
        resource_id = search_bundle["entry"][0]["resource"]["id"]
        logger.info(f"Found Questionnaire with name {form_name}")
    except KeyError:
        logger.error("Questionnaire with that name not found")
        return make_operation_outcome("not-found", f"Getting Questionnaire named {form_name} not found on server")

    new_questions["id"] = resource_id
    req = session.put(cqfr4_fhir + f"Questionnaire/{resource_id}", json=new_questions)
    if req.status_code != 200:
        logger.error(f"Putting Questionnaire from server failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Putting Questionnaire from server failed with status code {req.status_code}")

    return make_operation_outcome("informational", f"Questionnaire {form_name} successfully put on server with resource_id {resource_id}", severity="information")


@router.post("/forms/jobPackageToQuestionnaire")
def convert_jobpackage_to_questionnaire(cql_only: bool = False, smartchart: bool = False, csv_string: str = Body(...)):
    return convert_jobpackage_csv_to_questionnaire(jobpackage_csv=csv_string, cql_only=cql_only, smartchart=smartchart)
