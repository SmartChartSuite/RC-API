"""Response models for /batchjob endpoints."""

from src.models.fhir import ParametersResponse


class BatchJobAcceptedResponse(ParametersResponse):
    """FHIR Parameters body returned when a batch job is accepted."""
