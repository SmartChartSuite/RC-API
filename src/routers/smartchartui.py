from inspect import Parameter
import logging
import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from src.services.jobhandler import get_value_from_parameter
from src.models.batchjob import StartBatchJobsParameters
from src.responsemodels.prettyjson import PrettyJSONResponse

from src.util.fhirclient import FhirClient

logger = logging.getLogger('rcapi.routers.smartchartui')

external_fhir_client = FhirClient(os.getenv('EXTERNAL_FHIR_SERVER_URL'))
internal_fhir_client = FhirClient(os.getenv('CQF_RULER_R4'))

apirouter = APIRouter()

batchJobs: dict = []

'''Read a Patient resource from the external FHIR Server (ex: Epic)'''
@apirouter.get("/smartchartui/patient/{patient_id}", response_class=PrettyJSONResponse)
def read_patient(patient_id: str):
    print(external_fhir_client.server_base)
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

'''Batch request to run every job in a jobPackage individually'''
@apirouter.post("/smartchartui/batchjob")
def batch_job(post_body: StartBatchJobsParameters, response_class=PrettyJSONResponse):
    form_name = get_value_from_parameter(post_body, "jobPackage")
    
    # pass