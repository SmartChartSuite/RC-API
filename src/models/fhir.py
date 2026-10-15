"""Canonical FHIR resource models and raw proxy payload types shared across the API.

This module defines the canonical Pydantic models for FHIR resources, Bundles,
Parameters, and OperationOutcomes. Resource models allow unknown FHIR fields and
provide round-trip JSON serialization for write endpoints; the transport layer uses
one unstructured raw-JSON alias rather than a parallel resource hierarchy. This
module performs no transport, persistence, or domain orchestration; HTTP proxying
belongs to `src.services.fhir_proxy`, while application workflows belong to other
`src.services` modules.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class OperationOutcomeIssue(BaseModel):
    severity: Literal["fatal", "error", "warning", "information"]
    code: Literal[
        "invalid",
        "structure",
        "required",
        "value",
        "invariant",
        "security",
        "login",
        "unknown",
        "expired",
        "forbidden",
        "suppressed",
        "processing",
        "not-supported",
        "duplicate",
        "multiple-matches",
        "not-found",
        "deleted",
        "too-long",
        "code-inavlid",
        "extension",
        "too-costly",
        "business-rule",
        "conflict",
        "transient",
        "lock-error",
        "no-store",
        "exception",
        "timeout",
        "incomplete",
        "throttled",
        "informational",
    ]
    details: dict[str, Any] | None = None
    diagnostics: str | None = None
    expression: list[str] | None = None


class OperationOutcome(BaseModel):
    resourceType: Literal["OperationOutcome"] = "OperationOutcome"
    issue: list[OperationOutcomeIssue]


type FHIRJSON = dict[str, Any]
type FHIRProxyResult = FHIRJSON


@dataclass(frozen=True)
class FHIRProxyResponse:
    """FHIR payload plus the upstream or proxy status needed by HTTP routes."""

    data: FHIRProxyResult
    status_code: int
    error_type: Literal["timeout", "transport"] | None = None


class FHIRBaseModel(BaseModel):
    """Canonical base for resource models with typed fields and unknown-field passthrough.

    Concrete resource models declare `resourceType` as a required `Literal` with no default, so the
    OpenAPI schema lists it as required and a body for another resource type is rejected."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def require_resource_type(cls, value: Any) -> Any:
        """Reject a missing resourceType with a specific message instead of Pydantic's generic one."""
        if "resourceType" in cls.model_fields and isinstance(value, Mapping) and "resourceType" not in value:
            raise ValueError("resourceType is required")
        return value

    def to_fhir_json(self) -> FHIRJSON:
        """Dump only fields supplied by the client while preserving explicit nulls and extras."""
        payload = self.model_dump(mode="json", exclude_unset=True)
        if "resourceType" not in payload:
            raise ValueError("FHIR resource payload must include resourceType")
        return payload


class BundleEntry(FHIRBaseModel):
    resource: dict[str, Any]


class BundleResource(FHIRBaseModel):
    resourceType: Literal["Bundle"]
    type: str | None = None
    total: int | None = None
    entry: list[BundleEntry] | None = None


class GroupMember(FHIRBaseModel):
    entity: dict[str, Any] | None = None


class GroupResource(FHIRBaseModel):
    resourceType: Literal["Group"]
    id: str | None = None
    name: str | None = None
    member: list[GroupMember] | None = None


class PatientResource(FHIRBaseModel):
    resourceType: Literal["Patient"]
    id: str | None = None


class QuestionnaireResource(FHIRBaseModel):
    resourceType: Literal["Questionnaire"]
    id: str | None = None
    name: str | None = None
    version: str | None = None


class QuestionnaireResponseResource(FHIRBaseModel):
    resourceType: Literal["QuestionnaireResponse"]
    id: str | None = None
    questionnaire: str | None = None
    status: str | None = None
    subject: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    authored: str | None = None


class LibraryResource(FHIRBaseModel):
    resourceType: Literal["Library"]
    id: str | None = None
    name: str | None = None
    version: str | None = None


class ParametersParameter(BaseModel):
    name: str
    valueString: str | None = None
    valueReference: dict | None = None
    valueBoolean: bool | None = None
    valueInteger: int | None = None
    valueDecimal: float | None = None
    valueDateTime: str | None = None
    valueDate: str | None = None
    valueCode: str | None = None
    valueUri: str | None = None
    valueCanonical: str | None = None
    valueQuantity: dict | None = None
    valueCoding: dict | None = None
    valueCodeableConcept: dict | None = None
    # Can add more value types as needed
    resource: dict | None = None
    part: list[ParametersParameter] | None = None

    @model_validator(mode="after")
    def check_inv_1(self) -> ParametersParameter:
        value_fields = [
            "valueString",
            "valueReference",
            "valueBoolean",
            "valueInteger",
            "valueDecimal",
            "valueDateTime",
            "valueDate",
            "valueCode",
            "valueUri",
            "valueCanonical",
            "valueQuantity",
            "valueCoding",
            "valueCodeableConcept",
        ]
        has_value = any(getattr(self, f) is not None for f in value_fields)

        has_resource = self.resource is not None
        has_part = bool(self.part)

        # FHIR inv-1: exactly one of:
        # - value[x]
        # - resource
        # - part
        groups_set = sum([has_value, has_resource, has_part])
        if groups_set != 1:
            raise ValueError("inv-1: A parameter must have one and only one of (value[x], resource, part)")

        return self


ParametersParameter.model_rebuild()


class ParametersResponse(BaseModel):
    resourceType: Literal["Parameters"] = "Parameters"
    parameter: list[ParametersParameter]
