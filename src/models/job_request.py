"""FHIR Parameters request body model for POST /batchjob."""

from pydantic import BaseModel, model_validator


class JobRequestParameter(BaseModel):
    name: str
    valueString: str


class JobRequest(BaseModel):
    """FHIR Parameters resource envelope for POST /batchjob.

    Required parameters: patientId, jobPackage.
    Optional parameters: jobPackageVersion.
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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "patientId", "valueString": "12345"},
                        {"name": "jobPackage", "valueString": "SyphilisRegistry"},
                        {"name": "jobPackageVersion", "valueString": "1.0"},
                    ],
                }
            ]
        }
    }
