"""Response models for /batchjob endpoints."""

from typing import ClassVar

from pydantic import ConfigDict

from src.models.fhir import ParametersResponse


class BatchJobAcceptedResponse(ParametersResponse):
    """FHIR Parameters body returned when a batch job is accepted."""

    model_config: ClassVar[ConfigDict] = {
        "json_schema_extra": {
            "examples": [
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "batchId", "valueString": "550e8400-e29b-41d4-a716-446655440000"},
                        {"name": "jobStartDateTime", "valueDateTime": "2026-07-20T14:03:11Z"},
                        {"name": "patientId", "valueString": "patient-123"},
                        {"name": "jobPackage", "valueString": "ExampleRegistry"},
                        {
                            "name": "batchJobQuestionnaireResponse",
                            "valueReference": {"reference": "QuestionnaireResponse/123e4567-e89b-12d3-a456-426614174000"},
                        },
                        {"name": "batchJobStatus", "valueString": "pending"},
                    ],
                }
            ]
        }
    }
