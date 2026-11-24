"""Admin request and response models for batch review copies."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.models.fhir import ParametersParameter


class ReviewerCopyRequest(BaseModel):
    """Stable reviewer identity and display label for one review copy."""

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    label: str = Field(min_length=1, max_length=128)


class BatchReviewCopiesRequest(BaseModel):
    """FHIR Parameters request for copies of one completed source batch."""

    resourceType: Literal["Parameters"] = "Parameters"
    parameter: list[ParametersParameter] = Field(min_length=1, max_length=21)

    @staticmethod
    def _reviewer_from_parameter(parameter: ParametersParameter) -> ReviewerCopyRequest:
        if not parameter.part:
            raise ValueError("Each reviewer parameter must contain key and label parts")
        values: dict[str, str] = {}
        for part in parameter.part:
            if part.name in values or part.name not in {"key", "label"} or not isinstance(part.valueString, str):
                raise ValueError("Reviewer parts must contain one valueString key and one valueString label")
            values[part.name] = part.valueString
        if set(values) != {"key", "label"}:
            raise ValueError("Each reviewer parameter must contain exactly key and label parts")
        return ReviewerCopyRequest.model_validate(values)

    @model_validator(mode="after")
    def validate_parameters(self) -> BatchReviewCopiesRequest:
        reviewer_parameters = [parameter for parameter in self.parameter if parameter.name == "reviewer"]
        if not reviewer_parameters or len(reviewer_parameters) > 20:
            raise ValueError("The request must contain between one and twenty reviewer parameters")
        for parameter in reviewer_parameters:
            self._reviewer_from_parameter(parameter)

        assignment_parameters = [parameter for parameter in self.parameter if parameter.name == "assignOriginalTo"]
        if len(assignment_parameters) > 1 or any(parameter.valueString is None for parameter in assignment_parameters):
            raise ValueError("assignOriginalTo must be a single valueString parameter")
        if any(parameter.name not in {"reviewer", "assignOriginalTo"} for parameter in self.parameter):
            raise ValueError("Only reviewer and assignOriginalTo parameters are supported")
        return self

    def reviewer_pairs(self) -> list[tuple[str, str]]:
        return [(reviewer.key, reviewer.label) for parameter in self.parameter if parameter.name == "reviewer" for reviewer in [self._reviewer_from_parameter(parameter)]]

    def original_reviewer_key(self) -> str | None:
        return next((parameter.valueString for parameter in self.parameter if parameter.name == "assignOriginalTo"), None)

    model_config = {
        "json_schema_extra": {
            "example": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "reviewer",
                        "part": [
                            {"name": "key", "valueString": "reviewer-1"},
                            {"name": "label", "valueString": "Name1"},
                        ],
                    },
                    {
                        "name": "reviewer",
                        "part": [
                            {"name": "key", "valueString": "reviewer-2"},
                            {"name": "label", "valueString": "Name2"},
                        ],
                    },
                    {"name": "assignOriginalTo", "valueString": "reviewer-1"},
                ],
            }
        }
    }


class BatchReviewCopyResponse(BaseModel):
    """Metadata for one created or previously existing review copy."""

    batchId: str
    sourceBatchId: str
    reviewerKey: str
    reviewerLabel: str
    questionnaireTitle: str
    status: str
    created: bool
    isOriginal: bool


class BatchReviewCopiesResponse(BaseModel):
    """Result of an idempotent review-copy request."""

    sourceBatchId: str
    copies: list[BatchReviewCopyResponse]
