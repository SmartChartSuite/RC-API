"""Module for defining models and classes for the API"""

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class JobParameter(BaseModel):
    """Base Job Parameter model"""

    name: str
    valueString: str | None = None
    valueDateTime: str | None = None
    resource: dict | None = None


class JobIDParameter(JobParameter):
    """Job ID Parameter for Job Status support"""

    name: str = "jobId"
    valueString: str = Field(default_factory=uuid4)  # type: ignore


class JobStatusParameter(JobParameter):
    """Job Status Parameter for Job Status support"""

    name: str = "jobStatus"
    valueString: str = "inProgress"


class ResultParameter(JobParameter):
    """Result Parameter for Job Status Support"""

    name: str = "result"
    resource: dict = {"resourceType": "Bundle"}


class JobStartParameter(JobParameter):
    """Job Start Time Parameter for Job Status Support"""

    name: str = "jobStartDateTime"
    valueDateTime: str = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


class JobCompletedParameter(JobParameter):
    """Job Completed Time Parameter for Job Status Support"""

    name: str = "jobCompletedDateTime"
    valueDateTime: str = "2099-12-31T00:00:00Z"


class ParametersJob(BaseModel):
    """Parameters Job object for Job Status Support"""

    resourceType: str = "Parameters"
    parameter: list[JobParameter] = [
        JobIDParameter(),
        JobStartParameter(),
        JobStatusParameter(),
        ResultParameter(),
    ]


class StartJobsParametersParameter(BaseModel):
    name: str
    valueString: str


class StartJobsParameters(BaseModel):
    resourceType: str = "Parameters"
    parameter: list[StartJobsParametersParameter]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "resourceType": "Parameters",
                    "parameter": [{"name": "patientId", "valueString": "12345"}, {"name": "jobPackage", "valueString": "SyphilisRegistry"}, {"name": "job", "valueString": "demographics.cql"}],
                }
            ]
        }
    }


class FlatNLPQLResultDisplayObject(BaseModel):
    date: str | None = None
    result_content: str | None = None
    sentence: str | None = None
    highlights: list | None = None
    start: list[int] | None = None
    end: list[int] | None = None


class FlatNLPQLResult(BaseModel):
    _id: str | None = None
    _ids_1: str | None = None
    batch: str | None = None
    concept_code: str | None = None
    concept_code_system: str | None = None
    context_type: str | None = None
    display_name: str | None = None
    education_level: str | None = None
    employment_status: str | None = None
    end: int | None = None
    experiencer: str | None = None
    housing: str | None = None
    immigration_status: str | None = None
    inserted_date: str | None = None
    job_date: str | None = None
    job_id: int | None = None
    languages: str | None = None
    negation: str | None = None
    nlpql_feature: str | None = None
    nlpql_features_1: str | None = None
    owner: str | None = None
    phenotype_final: str | None = None
    phenotype_id: int | None = None
    pipeline_id: int | None = None
    pipeline_type: str | None = None
    raw_definition_text: str | None = None
    religion: str | None = None
    report_date: str | None = None
    report_id: str | None = None
    report_type: str | None = None
    report_text: str | None = None
    result_display: FlatNLPQLResultDisplayObject
    section: str | None = None
    section_header: str | None = None
    section_text: str | None = None
    sentence: str | None = None
    sexual_orientation: str | None = None
    solr_id: str | None = None
    source: str | None = None
    start: str | None = None
    subject: str | None = None
    temporality: str | None = None
    term: str | None = None
    text: str | None = None
    tuple: str | None = None
    value: str | dict | None = None


class NLPQLTupleResult(BaseModel):
    sourceNote: str
    answerValue: str | dict
    answerType: str
