''' TODO: Potentially Temporary Abstraction of Job Management, separated for use for Batch Jobs testing'''
from collections import OrderedDict
from datetime import datetime
import json
import logging
import sqlite3
from uuid import UUID

from src.models.models import ParametersJob

logger = logging.getLogger("rcapi.services.jobstate")

# SQL Lite temporary handling to persist batch jobs.
# TODO: Change to class

def create_database():
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    cur.execute("CREATE TABLE if not exists batch_jobs(batch_job_id text, batch_job blob)")
    cur.execute("CREATE TABLE if not exists jobs(job_id text, job blob)")
    con.commit()

create_database()

'''TODO: Refactor or Delete the following functions, temporary functions to access global'''
def add_to_jobs(new_job, index) -> bool:
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    res = cur.execute("SELECT job_id FROM jobs")
    current_job_id_list = res.fetchall()

    if index not in current_job_id_list:
        print(new_job)
        print("-----------------")
        data = [(index, json.dumps(new_job.model_dump(), cls=UUIDEncoder))]
        print(data)
        cur.executemany("INSERT INTO jobs VALUES(?, ?)", data)
        con.commit()
        logger.info("Added to jobs")
        return True
    else:
        return False

def add_to_batch_jobs(new_batch_job: ParametersJob, index: str) -> bool:
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    res = cur.execute("SELECT batch_job_id FROM batch_jobs")
    current_job_id_list = res.fetchall()
    print(new_batch_job)
    if index not in current_job_id_list:
        data = [(index, json.dumps(new_batch_job.model_dump(), cls=UUIDEncoder))]
        #TODO: Make adding child jobs part of a single atomic transaction.
        cur.executemany("INSERT INTO batch_jobs VALUES(?, ?)", data)
        con.commit()
        logger.info("Added to batch jobs")
        return True
    else:
        return False

def get_job(index):
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    res = cur.execute("SELECT job FROM jobs WHERE job_id=:index", {"index": index})
    return res.fetchone()

def get_all_batch_jobs():
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    res = cur.execute("SELECT batch_job FROM batch_jobs")
    return res.fetchall()

def get_batch_job(index: str):
    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    res = cur.execute("SELECT batch_job FROM batch_jobs WHERE batch_job_id=:index", {"index": index})
    return res.fetchone()

def update_job_to_complete(job_id, job_result):
    # TODO: Why is this returning from sqllite as a tuple?
    job = json.loads(get_job(job_id)[0], object_pairs_hook=OrderedDict)
    param_list = job["parameter"]

    #print(type(job_result))

    for param in param_list:
        if param["name"] == "jobStatus":
            param["valueString"] = "complete"
        # elif param["name"] == "jobCompletedDateTime":
        #     param["valueDateTime"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        elif param["name"] == "result":
            param["resource"] = job_result
    
    job["parameter"].append(OrderedDict({"name": "jobCompletedDateTime", "valueDateTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")}))

    con = sqlite3.connect("batch_jobs.sqlite")
    cur = con.cursor()
    cur.executemany("UPDATE jobs SET job = ? WHERE job_id = ?", [(json.dumps(job, cls=BytesEncoder), job_id)])
    con.commit()


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