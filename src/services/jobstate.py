''' TODO: Potentially Temporary Abstraction of Job Management, separated for use for Batch Jobs testing'''
import datetime
import logging
from src.models.batchjob import BatchParametersJob

from src.models.models import ParametersJob

logger = logging.getLogger("rcapi.services.jobstate")

jobs: dict[str, ParametersJob] = {}
batch_jobs: dict[str, BatchParametersJob] = {}


'''TODO: Refactor or Delete the following functions, temporary functions to access global'''
def add_to_jobs(new_job, index) -> bool:
    if index not in jobs:
        jobs[index] = new_job
        logger.info("Added to jobs array")
        return True
    else:
        return False

def add_to_batch_jobs(new_batch_job, index) -> bool:
    if index not in batch_jobs:
        batch_jobs[index] = new_batch_job
        logger.info("Added to batch jobs array")
        return True
    else:
        return False
    
def index_in_jobs(index) -> bool:
    return index in jobs

def index_in_batch_jobs(index) -> bool:
    return index in batch_jobs

def get_job(index):
    return jobs[index]

def get_batch_job(index):
    return batch_jobs[index]

def update_job_to_complete(job_id, job_result):
    tmp_job_obj = get_job[job_id]
    status_param_index: int = tmp_job_obj.parameter.index([param for param in tmp_job_obj.parameter if param.name == 'jobStatus'][0])
    endtime_param_index: int = tmp_job_obj.parameter.index([param for param in tmp_job_obj.parameter if param.name == 'jobCompletedDateTime'][0])
    result_param_index: int = tmp_job_obj.parameter.index([param for param in tmp_job_obj.parameter if param.name == 'result'][0])
    jobs[job_id].parameter[result_param_index].resource = job_result
    jobs[job_id].parameter[status_param_index].valueString = "complete"
    jobs[job_id].parameter[endtime_param_index].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")