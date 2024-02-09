import logging
import os
from fastapi import APIRouter
from src.responsemodels.prettyjson import PrettyJSONResponse

from src.util.fhirclient import FhirClient


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