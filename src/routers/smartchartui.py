import json
import logging
import os
import typing
from fastapi import APIRouter, Request, Response

from src.util.fhirclient import FhirClient

class PrettyJSONResponse(Response):
    media_type = "application/json"
    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=4,
            separators=(", ", ": "),
        ).encode("utf-8")


logger = logging.getLogger('rcapi.routers.smartchartui')

external_fhir_client = FhirClient(os.getenv('EXTERNAL_FHIR_SERVER_URL'))
internal_fhir_client = FhirClient(os.getenv('CQF_RULER_R4'))

apirouter = APIRouter()

'''Read a Patient resource from the external FHIR Server (ex: Epic)'''
@apirouter.get("/smartchartui/patient/{patient_id}", response_class=PrettyJSONResponse)
def read_patient(patient_id: str):
    response = external_fhir_client.readResource("Patient", patient_id)
    return response

'''Search for all Group resources on the internal SmartChart FHIR server (ex: SmartChart Suite CQF Ruler)'''
@apirouter.get("/smartchartui/group")
def search_group():
    response = internal_fhir_client.searchResource("Group", flatten=True)
    return response

@apirouter.get("/smartchartui/questionnaire")
def search_questionnaire():
    response = internal_fhir_client.searchResource("Questionnaire", flatten=True)
    return response