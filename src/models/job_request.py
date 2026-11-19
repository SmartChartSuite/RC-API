"""Pydantic models for the FHIR `Parameters` request accepted by `/batchjob`.

This module validates the required `patientId` and `jobPackage` parameters and
provides accessors for optional and repeated `job` values. It defines request
shapes only and performs no questionnaire resolution, persistence, or execution;
those responsibilities belong to `src.services.batch_job_service` and its
collaborators.
"""

from pydantic import BaseModel, model_validator


class JobRequestParameter(BaseModel):
    name: str
    valueString: str | None = None
    valueDate: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> JobRequestParameter:
        if (self.valueString is None) == (self.valueDate is None):
            raise ValueError("Each batch-job parameter must contain exactly one of valueString or valueDate.")
        return self


class JobRequest(BaseModel):
    """FHIR Parameters resource envelope for POST /batchjob.

    Required parameters: patientId, jobPackage.
    Optional parameters: jobPackageVersion, job, documentStartDate, documentEndDate.
    """

    resourceType: str = "Parameters"
    parameter: list[JobRequestParameter]

    @model_validator(mode="after")
    def validate_required_params(self) -> JobRequest:
        names = {p.name for p in self.parameter}
        missing = [n for n in ("patientId", "jobPackage") if n not in names]
        if missing:
            raise ValueError(f"Missing required parameter(s): {missing}. POST body must include 'patientId' and 'jobPackage' parameters.")
        return self

    def get_param(self, name: str) -> str | None:
        """Convenience method to extract a named string or date parameter value."""
        for p in self.parameter:
            if p.name == name:
                return p.valueString if p.valueString is not None else p.valueDate
        return None

    def get_params(self, name: str) -> list[str]:
        """Return all values for a repeated named parameter in request order."""
        return [value for p in self.parameter if p.name == name for value in (p.valueString if p.valueString is not None else p.valueDate,) if value is not None]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "patientId", "valueString": "patient-123"},
                        {"name": "jobPackage", "valueString": "ExampleRegistry"},
                        {"name": "jobPackageVersion", "valueString": "1.0.0"},
                        {"name": "job", "valueString": "ExampleStructuredTask"},
                        {"name": "job", "valueString": "2026_01/example/example-unstructured-task"},
                        {"name": "documentStartDate", "valueDate": "2024-01-01"},
                        {"name": "documentEndDate", "valueDate": "2024-12-31"},
                    ],
                }
            ]
        }
    }
