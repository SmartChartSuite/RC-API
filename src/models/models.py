"""Module for defining models and classes for the API"""
import logging
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CustomFormatter(logging.Formatter):
    """Custom Formatter object for formatting logging messages throughout the API"""

    grey = "\x1b[38;21m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format_str = "{asctime}   {levelname:8s} --- {name}: {message}"

    FORMATS = {
        logging.DEBUG: grey + format_str + reset,
        logging.INFO: green + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, "%m/%d/%Y %I:%M:%S %p", style="{")
        return formatter.format(record)


class JobIDParameter(BaseModel):
    """Job ID Parameter for Job Status support"""

    name: str = "jobId"
    valueString: UUID = Field(default_factory=uuid4)


class JobStatusParameter(BaseModel):
    """Job Status Parameter for Job Status support"""

    name: str = "jobStatus"
    valueString: str = "inProgress"


class ResultParameter(BaseModel):
    """Result Parameter for Job Status Support"""

    name: str = "result"
    resource: dict = {"resourceType": "Bundle"}


class JobStartParameter(BaseModel):
    """Job Start Time Parameter for Job Status Support"""

    name: str = "jobStartDateTime"
    valueDateTime: str = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


class JobCompletedParameter(BaseModel):
    """Job Completed Time Parameter for Job Status Support"""

    name: str = "jobCompletedDateTime"
    valueDateTime: str = "2099-12-31T00:00:00Z"


class ParametersJob(BaseModel):
    """Parameters Job object for Job Status Support"""

    resourceType: str = "Parameters"
    parameter: list = [JobIDParameter(), JobStartParameter(), JobCompletedParameter(), JobStatusParameter(), ResultParameter()]
