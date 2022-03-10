from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID, uuid4
import logging

class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;21m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = '%(levelname)s %(name)s: %(asctime)s - %(message)s'

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: green + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, '%m/%d/%Y %I:%M:%S %p')
        return formatter.format(record)

class Answer(BaseModel):
    text: str
    value: str

class EvidenceBundleObject(BaseModel):
    information: Optional[List[str]] = None

class Question(BaseModel):
    question_name: str
    question_type: str
    question_number: str
    group: str
    answers: List[Answer]
    evidence_bundle: EvidenceBundleObject
    nlpql_grouping: str

class QuestionsJSON(BaseModel):
    name: str
    owner: str
    description: str
    allocated_users: list
    groups: list
    questions: List[Question]
    evidence_bundles: list
    version: str
    questions_with_evidence_count: Optional[str] = None

class StartJobPostBody(BaseModel):
    formId: str
    evidenceBundles: List[str]
    patientId: str

class NLPQLDict(BaseModel):
    name: str
    content: str

class CQLPost(BaseModel):
    code: str

class JobIDParameter(BaseModel):
    name: str = "jobId"
    valueString: UUID = Field(default_factory=uuid4)

class JobStatusParameter(BaseModel):
    name: str = "jobStatus"
    valueString: str = "inProgress"

class ResultParameter(BaseModel):
    name: str = "result"
    resource: dict = {"resourceType": "Bundle"}

class ParametersJob(BaseModel):
    resourceType: str = "Parameters"
    parameter: list = [JobIDParameter(), JobStatusParameter(), ResultParameter()]

bundle_template = {
    "resourceType": "Bundle",
    "meta": {
        "lastUpdated": ""
    },
    "type": "collection",
    "entry": []
}