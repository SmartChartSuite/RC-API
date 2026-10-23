"""Pydantic models for the configuration representation returned by `/config`.

This module defines the client-facing primary-identifier shape and its schema
examples. It contains data validation and serialization only and performs no
settings loading or I/O; environment parsing and derived configuration belong to
`src.util.settings`.
"""

from pydantic import BaseModel


class ConfigEndpointPrimaryIdentifier(BaseModel):
    label: str | None = None
    system: str | None = None


class ConfigEndpointModel(BaseModel):
    primaryIdentifier: ConfigEndpointPrimaryIdentifier | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "primaryIdentifier": {
                        "system": "http://hospital.example.org/mrn",
                        "label": "Medical Record Number",
                    }
                }
            ]
        }
    }
