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
    valueString: UUID = Field(default_factory=uuid4)

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

class BatchJobPatientIncludedParameter(BaseModel):
    '''Batch JobPackage Parameter for When request is flagged to include'''
    name: str = "patientResource"
    resource: dict = {"resourceType": "Patient"}
    
# TODO: Add back in type parameter once implemented.
class BatchParametersJob(BaseModel):
    '''Parameters Job object for Batch Job Status Support'''
    resourceType: str = "Parameters"
    parameter: list = [
        BatchIdParameter(),
        BatchPatientIdParameter(),
        BatchJobPackageParameter(),
        JobStartParameter(),
        BatchJobListPararameter(),
        BatchJobPatientIncludedParameter()
        ]