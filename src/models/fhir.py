"""Shared FHIR response models used by router output typing."""

from typing import Any, Literal, TypedDict, TypeAlias

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


class FHIRResourceJSON(TypedDict, total=False):
    resourceType: str
    id: str
    name: str
    version: str


class BundleEntryJSON(TypedDict):
    resource: dict[str, Any]


class BundleJSON(TypedDict, total=False):
    resourceType: Literal["Bundle"]
    type: str
    total: int
    entry: list[BundleEntryJSON]


class OperationOutcomeIssueJSON(TypedDict, total=False):
    severity: Literal["fatal", "error", "warning", "information"]
    code: str
    details: dict[str, Any]
    diagnostics: str
    expression: list[str]


class OperationOutcomeJSON(TypedDict):
    resourceType: Literal["OperationOutcome"]
    issue: list[OperationOutcomeIssueJSON]


class GroupJSON(TypedDict, total=False):
    resourceType: Literal["Group"]
    id: str
    name: str
    version: str
    member: list[dict[str, Any]]


class PatientJSON(TypedDict, total=False):
    resourceType: Literal["Patient"]
    id: str
    name: str
    version: str


class QuestionnaireJSON(TypedDict, total=False):
    resourceType: Literal["Questionnaire"]
    id: str
    name: str
    version: str


class LibraryJSON(TypedDict, total=False):
    resourceType: Literal["Library"]
    id: str
    name: str
    version: str


FHIRProxyResult: TypeAlias = BundleJSON | GroupJSON | PatientJSON | QuestionnaireJSON | LibraryJSON | OperationOutcomeJSON | FHIRResourceJSON


class FHIRBaseModel(BaseModel):
    """Base model for FHIR resources where fields beyond the typed subset are allowed."""

    model_config = ConfigDict(extra="allow")


class BundleEntry(FHIRBaseModel):
    resource: dict[str, Any]


class BundleResource(FHIRBaseModel):
    resourceType: Literal["Bundle"] = "Bundle"
    type: str | None = None
    total: int | None = None
    entry: list[BundleEntry] | None = None


class GroupMember(FHIRBaseModel):
    entity: dict[str, Any] | None = None


class GroupResource(FHIRBaseModel):
    resourceType: Literal["Group"] = "Group"
    id: str | None = None
    name: str | None = None
    member: list[GroupMember] | None = None


class PatientResource(FHIRBaseModel):
    resourceType: Literal["Patient"] = "Patient"
    id: str | None = None


class QuestionnaireResource(FHIRBaseModel):
    resourceType: Literal["Questionnaire"] = "Questionnaire"
    id: str | None = None
    name: str | None = None
    version: str | None = None


class QuestionnaireResponseResource(FHIRBaseModel):
    resourceType: Literal["QuestionnaireResponse"] = "QuestionnaireResponse"
    id: str | None = None
    questionnaire: str | None = None
    status: str | None = None
    subject: dict[str, Any] | None = None


class LibraryResource(FHIRBaseModel):
    resourceType: Literal["Library"] = "Library"
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
    part: list["ParametersParameter"] | None = None

    @model_validator(mode="after")
    def check_inv_1(self) -> "ParametersParameter":
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
