"""FHIR Parameters request body model for POST /batchjob."""

from pydantic import BaseModel, model_validator


class JobRequestParameter(BaseModel):
    name: str
    valueString: str


class JobRequest(BaseModel):
    """FHIR Parameters resource envelope for POST /batchjob.

    Required parameters: patientId, jobPackage.
    Optional parameters: jobPackageVersion, job.
    """

    resourceType: str = "Parameters"
    parameter: list[JobRequestParameter]

    @model_validator(mode="after")
    def validate_required_params(self) -> "JobRequest":
        names = {p.name for p in self.parameter}
        missing = [n for n in ("patientId", "jobPackage") if n not in names]
        if missing:
            raise ValueError(f"Missing required parameter(s): {missing}. POST body must include 'patientId' and 'jobPackage' parameters.")
        return self

    def get_param(self, name: str) -> str | None:
        """Convenience method to extract a named parameter value."""
        for p in self.parameter:
            if p.name == name:
                return p.valueString
        return None

    def get_params(self, name: str) -> list[str]:
        """Return all values for a repeated named parameter in request order."""
        return [p.valueString for p in self.parameter if p.name == name]

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
                    ],
                }
            ]
        }
    }
