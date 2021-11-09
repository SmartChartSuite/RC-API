import json
from re import search
from fastapi import (
    APIRouter,
    Body,
    HTTPException
)
from fhir.resources import resource
from requests.api import request

from ..models.forms import (
    CustomFormatter, StartJobPostBody, NLPQLDict, make_operation_outcome, bundle_forms, run_cql, get_cql_results
)

from typing import Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from fhir.resources.parameters import Parameters
from fhir.resources.operationoutcome import OperationOutcome
from fhir.resources.observation import Observation
from bson import ObjectId
from requests_futures.sessions import FuturesSession

from ..util.settings import (formsdb, cqfr4_fhir)

import os
import base64
import pymongo
import ast
import logging
import requests
import uuid

import time

# Formats logging message to include the level of log message
#logging.basicConfig(format='%(asctime)s %(levelname)s - %(message)s', level=logging.DEBUG, datefmt='%m/%d/%Y %I:%M:%S %p')

# Create logger
logger = logging.getLogger("SPUD Forms API")
logger.setLevel(logging.DEBUG)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

formsrouter = APIRouter()


@formsrouter.get("/")
def root():
    logger.info('Retrieved root of API')
    return make_operation_outcome('processing', 'This is the base URL of API. Unable to handle this request as it is the root.')

@formsrouter.get("/forms", response_model=dict)
def get_list_of_forms():

    # Pull list of forms from CQF Ruler
    r = requests.get(cqfr4_fhir+'Questionnaire')
    if r.status_code == 200:
        return r.json()
    else:
        logger.error(f'Getting Questionnaires from server failed with code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Questionnaires from server failed with code {r.status_code}, see API logs for more detail')

@formsrouter.get("/forms/cql")
def get_cql_libraries():

    # Pulls list of CQL libraries from CQF Ruler
    r = requests.get(cqfr4_fhir+'Library')
    if r.status_code == 200:
        return r.json()
    else:
        logger.error(f'Getting Libraries from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Libraries from server failed with code {r.status_code}, see API logs for more detail')

@formsrouter.get("/forms/cql/{library_name}")
def get_cql(library_name: str):

    # Return CQL library
    r = requests.get(cqfr4_fhir+f'Library?name={library_name}')
    if r.status_code != 200:
        logger.error(f'Getting library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Library from server failed with code {r.status_code}, see API logs for more detail')

    search_bundle = r.json()
    try:
        cql_library = search_bundle['entry'][0]['resource']
        logger.info(f'Found CQL Library with name {library_name}')
    except KeyError:
        logger.error('CQL Library with that name not found')
        return make_operation_outcome('not-found', f'CQL Library named {library_name} not found on the FHIR server.')

    # Decode CQL from base64 Library encoding
    base64_cql = cql_library['content'][0]['data']
    cql_bytes = base64.b64decode(base64_cql)
    decoded_cql = cql_bytes.decode('ascii')
    return decoded_cql

@formsrouter.get("/forms/{form_name}", response_model=Union[dict, str])
def get_form(form_name: str):

    # Return Questionnaire from CQF Ruler based on form name
    r = requests.get(cqfr4_fhir+f'Questionnaire?name={form_name}')
    if r.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with code {r.status_code}, see API logs for more detail')

    search_bundle = r.json()
    try:
        questionnaire = search_bundle['entry'][0]['resource']
        logger.info(f'Found Questionnaire with name {form_name}')
        return questionnaire
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return make_operation_outcome('not-found', f'Getting Questionnaire named {form_name} not found on the FHIR server.')

@formsrouter.post("/forms")
def save_form(questions: Questionnaire):

    # Check to see if library and version of this exists
    r = requests.get(cqfr4_fhir+f'Questionnaire?name={questions.name}&version={questions.version}')
    if r.status_code != 200:
        logger.error(f'Trying to get Questionnaire from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with code {r.status_code}, see API logs for more detail')

    search_bundle = r.json()
    try:
        questionnaire_current_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {questions.name} and version {questions.version}')
        logger.info('Not completing POST operation because a Questionnaire with that name and version already exist on this FHIR Server')
        logger.info('Change Questionnaire name or version number or use PUT to update this version')
        return make_operation_outcome('duplicate', f'There is already a Questionnaire with this name with resource id {questionnaire_current_id}')
    except KeyError:
        logger.info('Questionnaire with that name not found, continuing POST operation')

    # Create Questionnaire in CQF Ruler
    r = requests.post(cqfr4_fhir+'Questionnaire', json=questions.dict())
    if r.status_code != 201:
        logger.error(f'Posting Questionnaire to server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Posting Questionnaire to server failed with code {r.status_code}, see API logs for more detail')

    resource_id = r.json()['id']
    return make_operation_outcome('informational',f'Resource successfully posted with id {resource_id}', severity='information')

@formsrouter.post("/forms/start", response_model=Union[dict, str])
def start_jobs(post_body: Parameters):

    # Make list of parameters
    body_json = post_body.dict()
    parameters = body_json['parameter']
    parameter_names = [x['name'] for x in parameters]
    logger.info(f'Recieved parameters {parameter_names}')

    try:
        patient_id = parameters[parameter_names.index('patientId')]['valueString']
    except ValueError:
        logger.error('patientID was not found in the parameters posted')
        return make_operation_outcome('required', 'patientID was not found in the parameters posted')

    try:
        library = parameters[parameter_names.index('job')]['valueString']
    except ValueError:
        logger.error('job was not found in the parameters posted')
        return make_operation_outcome('required', 'job was not found in the parameters posted')

    try:
        form_name = parameters[parameter_names.index('jobPackage')]['valueString']
    except ValueError:
        logger.error('jobPackage was not found in the parameters posted')
        return make_operation_outcome('required', 'jobPackage was not found in the parameters posted')

    # Pull CQL library resource ID from CQF Ruler
    r = requests.get(cqfr4_fhir+f'Library?name={library}')
    if r.status_code != 200:
        logger.error(f'Getting library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting library from server failed with status code {r.status_code}')

    search_bundle = r.json()
    try:
        library_server_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found CQL Library with name {library} and server id {library_server_id}')
    except KeyError:
        logger.error('CQL Library with that name not found')
        return make_operation_outcome('not-found','CQL Library with that name not found')

    # Pull Questionnaire resource ID from CQF Ruler
    r = requests.get(cqfr4_fhir+f'Questionnaire?name={form_name}')
    if r.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with status code {r.status_code}')

    search_bundle = r.json()
    try:
        form_server_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {form_name} and server id {form_server_id}')
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return make_operation_outcome('not-found','Questionnaire with that name not found')

    # Create parameters post body for library evaluation
    parameters_post = {
        'resourceType': 'Parameters',
        'parameter': [
            {
                'name': 'patientId',
                'valueString': patient_id
            },
            {
                'name': 'context',
                'valueString': 'Patient'
            },
            {
                'name': 'dataEndpoint',
                "resource": {
                    "resourceType": "Endpoint",
                    "identifier": [{
                        "system": "http://example.org/enpoint-identifier",
                        "value": "omopv53onfhir4"
                    }],
                    "status": "active",
                    "connectionType": {
                        "system": "http://terminology.hl7.org/CodeSystem/endpoint-connection-type",
                        "code": "hl7-fhir-rest"
                    },
                    "name": "OMOPonFHIR v5.3.1 on R4",
                    "payloadType": [{
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/endpoint-payload-type",
                            "code": "any"
                        }]
                    }],
                    "address": "https://apps.hdap.gatech.edu/omopv53onfhir4/fhir/",
                    "header": ["Authorization: Basic Y2xpZW50OnNlY3JldA=="]
			    }
            }
        ]
    }

    # Pass library id to be evaluated, gets back a future object that represent the pending status of the POST
    logger.info('Started submitting jobs')
    future = run_cql(library_server_id, parameters_post)
    logger.info('Submitted all jobs')

    # Passes future to get the results from it, will wait until all are processed until returning results
    logger.info('Start getting job results')
    result = get_cql_results(future, library, patient_id)
    logger.info(f'Retrived result for job {library}')

    # Upstream request timeout handling
    if type(result)==str:
        return make_operation_outcome('timeout',result)


    # Creates the linked evidence format needed for the UI to render evidence thats related to certain questions from a certain form
    logger.info('Start linking results')
    bundled_results = create_linked_results(result, form_name)
    logger.info('Finished linking results')

    return bundled_results

@formsrouter.post("/forms/nlpql")
def save_nlpql(code: str = Body(...)):
    # Get name and version of NLPQL Library
    split_cql = code.split()
    name = split_cql[1].strip('"')
    version = split_cql[3].strip('"')

    # Encode NLPQL as base64Binary
    code_bytes = code.encode('utf-8')
    base64_bytes = base64.b64encode(code_bytes)
    base64_nlpql = base64_bytes.decode('utf-8')
    logger.info('Encoded NLPQL')

    # Create Library object
    data = {
        'name': name,
        'version': version,
        'status': 'draft',
        'experimental': True,
        'type': {'coding':[{'code':'logic-library'}]},
        'content': [{
            'contentType': 'text/nlpql',
            'data': base64_nlpql
        }]
    }
    nlpql_library = Library(**data)
    nlpql_library = nlpql_library.dict()
    nlpql_library['content'][0]['data'] = base64_nlpql
    logger.info('Created Library object')

    # Store Library object in CQF Ruler
    r = requests.post(cqfr4_fhir+'Library', json=nlpql_library)
    if r.status_code != 201:
        logger.error(f'Posting Library {name} to server failed with status code {r.status_code}')
        logger.error(r.json())
        return make_operation_outcome('transient', f'Posting Library to server failed with code {r.status_code}, see API logs for more detail')

    resource_id = r.json()['id']
    return make_operation_outcome('informational',f'Resource successfully posted with id {resource_id}', severity='information')

@formsrouter.post("/forms/cql")
def save_cql(code: str = Body(...)):

    # Get name and version of cql library
    split_cql = code.split()
    name = split_cql[1]
    version = split_cql[3].strip("'")

    # Check to see if library and version of this exists
    r = requests.get(cqfr4_fhir+f'Library?name={name}&version={version}')
    if r.status_code != 200:
        logger.error(f'Trying to get library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}
    search_bundle = r.json()
    try:
        cql_library = search_bundle['entry'][0]['resource']
        logger.info(f'Found CQL Library with name {name} and version {version}')
        logger.info('Not completing POST operation because a CQL Library with that name and version already exist on this FHIR Server')
        logger.info('Change library name or version number or use PUT to update this version')
        return make_operation_outcome('duplicate', 'There is already a library with that name and version')
    except KeyError:
        logger.info('CQL Library with that name not found, continuing POST operation')

    # Encode CQL as base64Binary
    code_bytes = code.encode('utf-8')
    base64_bytes = base64.b64encode(code_bytes)
    base64_cql = base64_bytes.decode('utf-8')
    logger.info('Encoded CQL')

    # Create Library object
    data = {
        'name': name,
        'version': version,
        'status': 'draft',
        'experimental': True,
        'type': {'coding':[{'code':'logic-library'}]},
        'content': [{
            'contentType': 'text/cql',
            'data': base64_cql
        }]
    }
    cql_library = Library(**data)
    cql_library = cql_library.dict()
    cql_library['content'][0]['data'] = base64_cql
    logger.info('Created Library object')

    # Store Library object in CQF Ruler
    r = requests.post(cqfr4_fhir+'Library', json=cql_library)
    if r.status_code != 201:
        logger.error(f'Posting Library {name} to server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    resource_id = r.json()['id']
    return make_operation_outcome('informational',f'Resource successfully posted with id {resource_id}', severity='information')

@formsrouter.put("/forms/{form_name}")
def update_form(form_name: str, new_questions: Questionnaire):

    r = requests.get(cqfr4_fhir+f'Questionnaire?name={form_name}')
    if r.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    search_bundle = r.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {form_name}')
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return {}

    new_questions.id = resource_id
    r = requests.put(cqfr4_fhir+f'Questionnaire/{resource_id}', json=new_questions.dict())
    if r.status_code != 200:
        logger.error(f'Putting Questionnaire from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    return f'Questionnaire resource updated with server id {resource_id}'

@formsrouter.put("/forms/cql/{library_name}")
def update_cql(library_name: str, code: str = Body(...)):

    r = requests.get(cqfr4_fhir+f'Library?name={library_name}')
    if r.status_code != 200:
        logger.error(f'Getting Library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    search_bundle = r.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Library with name {library_name}')
    except KeyError:
        logger.error('Library with that name not found')
        return {}

    # Get name and version of cql library
    split_cql = code.split()
    name = split_cql[1]
    version = split_cql[3].strip("'")

    # Encode CQL as base64Binary
    code_bytes = code.encode('utf-8')
    base64_bytes = base64.b64encode(code_bytes)
    base64_cql = base64_bytes.decode('utf-8')
    logger.info('Encoded CQL')

    # Create Library object
    data = {
        'name': name,
        'version': version,
        'status': 'draft',
        'experimental': True,
        'type': {'coding':[{'code':'logic-library'}]},
        'content': [{
            'contentType': 'text/cql',
            'data': base64_cql
        }]
    }
    cql_library = Library(**data)
    cql_library = cql_library.dict()
    cql_library['content'][0]['data'] = base64_cql
    cql_library['id']=resource_id
    logger.info('Created Library object')

    r = requests.put(cqfr4_fhir+f'Library/{resource_id}', json=cql_library)
    if r.status_code != 200:
        logger.error(f'Putting Library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    return f'Library resource updated with server id {resource_id}'

@formsrouter.put("/forms/nlpql/{library_name}")
def update_nlpql(library_name: str, new_nlpql: str = Body(...)):

    r = requests.get(cqfr4_fhir=f'Library?name={library_name}')
    if r.status_code != 200:
        logger.error(f'Getting Library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    search_bundle = r.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Library with name {library_name}')
    except KeyError:
        logger.error('Library with that name not found')
        return {}

    r = requests.put(cqfr4_fhir+f'Library/{resource_id}', data=new_nlpql)
    if r.status_code != 200:
        logger.error(f'Putting Library from server failed with status code {r.status_code}')
        logger.error(r.json())
        return {}

    raise HTTPException(200, f'Library resource updated with server id {resource_id}')

def create_linked_results(result: dict, form_name: str):

    # Get form (using get_form from this API)
    form = get_form(form_name)

    bundle_entries = []

    # For each group of questions in the form
    for group in form['item']:

        # For each question in the group in the form
        for question in group['item']:


            linkId = question['linkId']

            # If the question has these extensions, get their values, if not, keep going
            try:
                for extension in question['extension']:
                    if extension['url'] == 'http://gtri.gatech.edu/fakeFormIg/cqlTask':
                        library_task = extension['valueString']
                    if extension['url'] == 'http://gtri.gatech.edu/fakeFormIg/cardinality':
                        cardinality = extension['valueString']
                library, task = library_task.split('.')
            except KeyError:
                pass

            # If this question has a task in a library whose results were passed to this function, get the results from that library run
            target_library = None
            if result['libraryName'] == library:
                target_library = result
            # if library was not found, just skip rest of loop to move on because its not needed
            if target_library is None:
                continue

            # Create answer observation for this question
            answer_obs_uuid = str(uuid.uuid4())
            answer_obs = {
                "resourceType": "Observation",
                "id": answer_obs_uuid,
                "status": "final",
                "code": {
                    "coding": [
                        {
                            "system": f"urn:gtri:heat:form:{form_name}",
                            "code": linkId
                        }
                    ]
                },
                "focus": [],
            }
            answer_obs = Observation(**answer_obs)

            # Find the result in the CQL library run that corresponds to what the question has defined in its cqlTask extension
            target_result = None
            for entry in result['results']['entry']:
                if entry['fullUrl'] == task:
                    value_return = [item for item in entry['resource']['parameter'] if item.get('name')=='value'][0]
                    supporting_resources = None
                    try:
                        if value_return['resourceType']=='Bundle':
                            supporting_resources = value_return['entry']
                            single_resource_flag = False
                        else:
                            resource_type = value_return['resourceType']
                            supporting_resources = [value_return]
                            single_resource_flag = True
                    except KeyError:
                        single_return_type = [item for item in entry['resource']['parameter'] if item.get('name')=='resultType'][0]['valueString']
                        single_return_value = value_return[f'value{single_return_type}']
                    logger.info('Found task and supporting resources')
            if supporting_resources is not None:
                for resource in supporting_resources:
                    focus_object = {'reference': resource['fullUrl']}
                    answer_obs.focus.append(focus_object)

            # If cardinality is a series, does the standard return body format
            if cardinality == 'series':
                # Construct final answer object bundle before result bundle insertion
                answer_obs_bundle_item = {
                    'fullUrl' : 'Observation/'+answer_obs_uuid,
                    'resource': answer_obs.dict()
                }

            # If cardinality is a single, does a modified return body to have the value in multiple places
            else:
                single_answer = single_return_value
                value_key = 'value'+single_return_type
                answer_obs_bundle_item = {
                    'fullUrl' : 'Observation/'+answer_obs_uuid,
                    'resource': answer_obs.dict(),
                    value_key: single_answer
                }

            #Add items to return bundle entry list
            bundle_entries.append(answer_obs_bundle_item)
            if supporting_resources is not None:
                bundle_entries.append(supporting_resources)

    return_bundle_id = str(uuid.uuid4())
    return_bundle = {
        'resourceType': 'Bundle',
        'id': return_bundle_id,
        'entry': bundle_entries
    }

    return return_bundle
