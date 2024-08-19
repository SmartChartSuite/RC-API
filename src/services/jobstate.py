"""TODO: Potentially Temporary Abstraction of Job Management, separated for use for Batch Jobs testing"""

import json
import logging
from collections import OrderedDict
from datetime import datetime
from uuid import UUID

from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, update

from src.models.batchjob import BatchParametersJob
from src.models.models import ParametersJob
from src.services.errorhandler import make_operation_outcome
from src.util.databaseclient import BatchJobs, Jobs, db_engine, execute_orm_no_return, execute_orm_query, save_object

logger: logging.Logger = logging.getLogger("rcapi.util.jobstate")


def add_to_jobs(new_job_body: ParametersJob, job_id, patient_id_type, patient_id, job_package, parent_batch_job_id, job_start_datetime, job_status) -> bool:
    current_job_id_list: list[str] = execute_orm_query(db_engine, select(Jobs.job_id))

    if job_id not in current_job_id_list:
        new_job_instance = Jobs(
            job_id=job_id,
            job=new_job_body.model_dump(exclude_none=True),
            patient_id_type=patient_id_type,
            patient_id=patient_id,
            job_package=job_package,
            parent_batch_job_id=parent_batch_job_id,
            job_start_datetime=job_start_datetime,
            job_status=job_status,
        )
        insert: str | None = save_object(db_engine, new_job_instance)
        if insert:
            logger.error("There was an issue inserting a new job into the database")
            logger.error(insert)
            return False
        logger.info(f"Created job {job_id} in jobs table.")
        return True
    else:
        return False


def add_to_batch_jobs(new_batch_job: ParametersJob | BatchParametersJob, index: str) -> bool:
    current_job_id_list: list[str] = execute_orm_query(db_engine, select(BatchJobs.batch_job_id))

    if index not in current_job_id_list:
        new_batch_job_instance = BatchJobs(batch_job_id=index, batch_job=new_batch_job.model_dump(exclude_none=True))
        insert: str | None = save_object(db_engine, new_batch_job_instance)
        if insert:
            logger.error("There was an issue inserting a new batch job into the database")
            logger.error(insert)
            return False
        logger.info(f"Created batch job {index} in jobs table.")
        return True
    else:
        return False


def get_job(index: str) -> dict | None:
    result: list[dict] = execute_orm_query(db_engine, select(Jobs.job).where(Jobs.job_id == index))
    return result[0] if result else None


def get_all_batch_jobs() -> list[dict]:
    return execute_orm_query(db_engine, select(BatchJobs.batch_job))


def get_batch_job(index: str) -> dict | None:
    result: list[dict] = execute_orm_query(db_engine, select(BatchJobs.batch_job).where(BatchJobs.batch_job_id == index))
    return result[0] if result else None


def delete_batch_job(job_id: str) -> JSONResponse:
    existing_batch_job: dict | None = get_batch_job(job_id)
    if not existing_batch_job:
        return JSONResponse(make_operation_outcome("not-found", f"Batch Job ID {job_id} was not found in the database"), 404)

    del_batch_job: dict | None = execute_orm_no_return(db_engine, delete(BatchJobs).where(BatchJobs.batch_job_id == job_id))

    if del_batch_job:
        logger.error("There was an issue deleting this relationship in the DB")
        logger.error(del_batch_job["detail"])
        return JSONResponse(make_operation_outcome("exception", del_batch_job["detail"]), 500)

    return JSONResponse(make_operation_outcome("deleted", f"Batch Job ID {job_id} has been successfully deleted from the database", "information"))


def update_job_to_complete(job_id: str, job_result) -> None:
    job = get_job(job_id)
    if not job:
        logger.error(f"Job {job_id} was not found in the database, this should not occur but is here for error handling.")
        return None

    param_list = job["parameter"]

    for param in param_list:
        if param["name"] == "jobStatus":
            param["valueString"] = "complete"
        # elif param["name"] == "jobCompletedDateTime":
        #     param["valueDateTime"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        elif param["name"] == "result":
            param["resource"] = job_result

    job["parameter"].append(OrderedDict({"name": "jobCompletedDateTime", "valueDateTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")}))

    update_out = execute_orm_no_return(db_engine, update(Jobs).where(Jobs.job_id == job_id).values(job_id=job_id, job=job, job_status="complete"))

    if update_out:
        logger.error("There was an issue updating the job in the database")
        logger.error(update_out)
    else:
        logger.info(f"Updated job {job_id} in jobs table.")


def get_child_job_statuses(batch_job_id: str) -> dict:

    child_job_statuses: list[Jobs] = execute_orm_query(db_engine, select(Jobs).where(Jobs.parent_batch_job_id == batch_job_id))

    return {item.job_id: item.job_status for item in child_job_statuses}


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        return json.JSONEncoder.default(self, obj)


class BytesEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode("utf-8")
        return json.JSONEncoder.default(self, obj)
