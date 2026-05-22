"""Pydantic models for job package response CRUD."""

from typing import Any

from pydantic import BaseModel

from src.models.fhir import ParametersResponse


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


class ResponseRecord(BaseModel):
    responseId: str
    batchJobId: str
    jobPackage: str
    patientId: str
    userId: str
    response: dict[str, Any]
    createdAt: str | None = None
    updatedAt: str | None = None


class ResponseCreatedResponse(ParametersResponse):
    """FHIR Parameters body returned when a response record is created."""
