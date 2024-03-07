''' Potentially Temporary Abstraction of Start Job Logic, separated for use for Batch Jobs'''
# TODO: Merge and delete as needed.

import datetime
import logging
import uuid

from fastapi import BackgroundTasks
from fhir.resources.parameters import Parameters
from src.models.functions import get_form, start_jobs
from src.models.models import ParametersJob
from src.services.jobstate import add_to_jobs, index_in_jobs, update_job_to_complete

logger = logging.getLogger("rcapi.services.jobhandler")


def start_async_jobs(post_body: Parameters, uid: str) -> None:
    """Start job asychronously"""
    job_result = start_jobs(post_body)
    
    if not index_in_jobs(uid):
        new_job = ParametersJob()
        uid_param_index: int = new_job.parameter.index([param for param in new_job.parameter if param.name == 'jobId'][0])
        starttime_param_index: int = new_job.parameter.index([param for param in new_job.parameter if param.name == 'jobStartDateTime'][0])
        new_job.parameter[uid_param_index].valueString = uid
        new_job.parameter[starttime_param_index].valueDateTime = "9999-12-31T00:00:00Z"
        add_to_jobs(new_job, uid)

    update_job_to_complete(uid, job_result)
    logger.info(f"Job id {uid} complete and results are available at /forms/status/{uid}")


#TODO: Support version
def get_job_list(form_name, form_version = None):
    questionnaire = get_form(form_name)
    cql_libraries: list[str] = []
    nlpql_libraries: list[str] = []

    cql_libraries_to_run_extension: dict = questionnaire["extension"][0]["extension"]
    for extension in cql_libraries_to_run_extension:
        cql_libraries.append(extension["valueString"])

    try:
        nlpql_libraries_to_run_extension: dict = questionnaire["extension"][1]["extension"]
        for extension in nlpql_libraries_to_run_extension:
            nlpql_libraries.append(extension["valueString"])
    except IndexError:
        logger.info("No NLPQL Libraries found.")

    return cql_libraries + nlpql_libraries

# TODO: If keeping, move out of job handler, should go in a form util class.
def get_value_from_parameter(parameters_resource: Parameters, parameter_name) -> str:
    for param in parameters_resource.parameter:
        key_value_pairs: list[str] = [x for x in param]
        if key_value_pairs[0][1] == parameter_name:
            return key_value_pairs[1][1]

# TODO: Same as above note.
def get_job_list_from_form(form) -> list[str]:
    cql_url = "http://gtri.gatech.edu/fakeFormIg/cql-form-job-list"
    nlpql_url = "http://gtri.gatech.edu/fakeFormIg/nlpql-form-job-list"
    # TODO: Add handling if a type of job does not exist. Return a default empty list?
    cql_jobs = next(i for i in form['extension'] if i['url'] == cql_url)
    nlpql_jobs = next(i for i in form['extension'] if i['url'] == nlpql_url)
    return [x['valueString'] for x in cql_jobs['extension'] + nlpql_jobs['extension'] if 'valueString' in x]
    
