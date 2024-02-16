from dataclasses import Field
from uuid import UUID, uuid4
from pydantic import BaseModel

from models.models import JobIDParameter, JobStartParameter, JobStatusParameter, ResultParameter

class BatchIdParameter(BaseModel):
    '''Job ID Parameter for Job Status support'''
    name: str = "batchId"
    valueString: UUID = Field(default_factory=uuid4)

class BatchParametersJob(BaseModel):
    '''Parameters Job object for Batch Job Status Support'''
    resourceType: str = "Parameters"
    parameter: list = [BatchIdParameter, JobIDParameter(), JobStartParameter(), JobStatusParameter(), ResultParameter()]