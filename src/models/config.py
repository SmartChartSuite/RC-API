from pydantic import BaseModel


class ConfigEndpointPrimaryIdentifier(BaseModel):
    label: str | None = None
    system: str | None = None


class ConfigEndpointModel(BaseModel):
    primaryIdentifier: ConfigEndpointPrimaryIdentifier | None = None
