"""Pydantic models for job package response CRUD (POST /response body and DB shape)."""

from datetime import datetime

from pydantic import BaseModel


class ResponseParameter(BaseModel):
    name: str
    valueString: str | None = None
    resource: dict | None = None


class ResponseRequest(BaseModel):
    """FHIR Parameters envelope for POST /response.

    Required: batchJobId, jobPackage, patientId, response (QuestionnaireResponse resource).
    """

    resourceType: str = "Parameters"
    parameter: list[ResponseParameter]

    def get_param(self, name: str) -> str | None:
        for p in self.parameter:
            if p.name == name:
                return p.valueString
        return None

    def get_resource_param(self, name: str) -> dict | None:
        for p in self.parameter:
            if p.name == name:
                return p.resource
        return None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "batchJobId", "valueString": "<uuid>"},
                        {"name": "jobPackage", "valueString": "SyphilisRegistry"},
                        {"name": "patientId", "valueString": "12345"},
                        {
                            "name": "response",
                            "resource": {
                                "resourceType": "QuestionnaireResponse",
                                "questionnaire": "Questionnaire/SyphilisRegistry",
                                "status": "completed",
                                "item": [{"linkId": "1.1", "answer": [{"valueString": "Yes"}]}],
                            },
                        },
                    ],
                }
            ]
        }
    }


class QuestionnaireResponseRecord(BaseModel):
    """DB-shaped record returned from GET /response endpoints."""

    response_id: str
    batch_job_id: str
    job_package: str
    patient_id: str
    user_id: str
    response: dict  # full FHIR QuestionnaireResponse resource
    created_at: datetime
    updated_at: datetime
