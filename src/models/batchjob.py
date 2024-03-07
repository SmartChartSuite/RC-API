from uuid import UUID, uuid4
from pydantic import BaseModel, Field

from src.models.models import JobIDParameter, JobStartParameter, JobStatusParameter, ParametersJob, ResultParameter

'''
Model for the BatchJob Post
'''
class StartBatchJobsParametersParameter(BaseModel):
    name: str
    valueString: str

class StartBatchJobsParameters(BaseModel):
    resourceType: str = "Parameters"
    parameter: list[StartBatchJobsParametersParameter]

'''
Models for the BatchJob Response
'''
class BatchIdParameter(BaseModel):
    '''Batch ID Parameter for Batch Job Status support'''
    name: str = "batchId"
    valueString: str = Field(default_factory=uuid4)

# TODO: Add support for identifiers instead of ids
class BatchPatientIdParameter(BaseModel):
    '''Batch Type Parameter for Batch Job Status support'''
    name: str = "patientId"
    valueString: str = ""

class BatchTypeParameter(BaseModel):
    '''Batch Type Parameter for Batch Job Status support'''
    name: str = "batchType"
    valueString: str = "patient"

class BatchJobPackageParameter(BaseModel):
    '''Batch JobPackage Parameter for Batch Job Status support'''
    name: str = "jobPackage"
    valueString: str = ""

class BatchJobListPararameter(BaseModel):
    '''Batch JobPackage Parameter for Batch Job Status support'''
    name: str = "childJobs"
    resource: dict = {"resourceType": "List"}
    
# TODO: Add back in type parameter once implemented.
class BatchParametersJob(BaseModel):
    '''Parameters Job object for Batch Job Status Support'''
    resourceType: str = "Parameters"
    parameter: list = [ BatchIdParameter(), BatchPatientIdParameter(), BatchJobPackageParameter(), JobStartParameter(), BatchJobListPararameter()]


'''
{
    "resourceType": "Parameters",
    "extension": [
        {
                "url": "batch-job-list",
                "extension": [
                    {
                        "url": "jobId",
                        "valueString": "12345"
                    },
                    {
                        "url": "jobId",
                        "valueString": "ab123"
                    },
                    {
                        "url": "jobId",
                        "valueString": "780nm"
                    }
                ]
            }
    ],
    "parameter": [
        {
            "name": "batchId",
            "valueString": "67684fdb-a4db-47c8-980b-e8ed308c9cc1"
        },
        {
            "name": "patientId",
            "valueString": "27280"
        },
        {
            "name": "jobStartDateTime",
            "valueDateTime": "2024-03-05T16:15:59Z"
        }
    ]
}
'''