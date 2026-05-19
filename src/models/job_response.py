"""Response models for GET /batchjob endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TaskResult(BaseModel):
    task_name: str
    task_type: Literal["structured", "unstructured"]
    status: Literal["pending", "running", "complete", "error", "skipped"]
    result: dict | list | str | None = None
    error: str | None = None


class BatchJobStatus(BaseModel):
    """Lightweight status model returned by GET /batchjob and GET /batchjob/{id}."""

    batch_id: str
    patient_id: str
    job_package: str
    status: Literal["pending", "running", "complete", "error"]
    created_at: datetime
    completed_at: datetime | None = None
    patient: dict | None = None  # populated when include_patient=True
