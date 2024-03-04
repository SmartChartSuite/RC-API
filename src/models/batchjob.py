from dataclasses import Field
from uuid import UUID, uuid4
from pydantic import BaseModel

from src.models.models import JobIDParameter, JobStartParameter, JobStatusParameter, ResultParameter

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
    #valueString: UUID = Field(default_factory=uuid4)
    valueString: str

class BatchTypeParameter(BaseModel):
    '''Batch Type Parameter for Batch Job Status support'''
    name: str = "batchType"
    valueString: str

class BatchParametersJob(BaseModel):
    '''Parameters Job object for Batch Job Status Support'''
    resourceType: str = "Parameters"
    parameter: list = [BatchIdParameter, JobIDParameter(), JobStartParameter(), JobStatusParameter(), ResultParameter()]