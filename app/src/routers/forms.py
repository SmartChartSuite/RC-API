import json
from re import search
from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    BackgroundTasks
)
from fhir.resources import resource
from requests.api import request

from ..models.forms import (
    CustomFormatter, StartJobPostBody, NLPQLDict, ParametersJob, flatten_results, make_operation_outcome, bundle_forms, run_cql, get_cql_results, flatten_results
)

from typing import Union, Dict
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from fhir.resources.parameters import Parameters
from fhir.resources.operationoutcome import OperationOutcome
from fhir.resources.observation import Observation
from bson import ObjectId
from requests_futures.sessions import FuturesSession

from ..util.settings import cqfr4_fhir, external_fhir_server_url, external_fhir_server_auth

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
logger.setLevel(logging.INFO)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

formsrouter = APIRouter()
jobs: Dict[str, ParametersJob] = {}

@formsrouter.get("/")
def root():
    logger.info('Retrieved root of API')
    return make_operation_outcome('processing', 'This is the base URL of API. Unable to handle this request as it is the root.')

@formsrouter.get("/forms", response_model=dict)
def get_list_of_forms():

    # Pull list of forms from CQF Ruler
    # TODO: Check for valid URL including foreward slash
    r = requests.get(cqfr4_fhir+'Questionnaire')
    if r.status_code == 200:
        return r.json()
    else:
        logger.error(f'Getting Questionnaires from server failed with code {r.status_code}')
        # TODO: change this so doesnt error
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
def start_jobs_header_function(post_body: Parameters, background_tasks: BackgroundTasks, asyncFlag: bool=False):
    if asyncFlag:
        logger.info('asyncFlag detected, running asynchronously')
        new_job = ParametersJob()
        new_job.parameter[0].valueString = str(uuid.uuid4())
        logger.info(f'Created new job with jobId {new_job.parameter[0].valueString}')
        jobs[new_job.parameter[0].valueString] = new_job
        logger.info('Added to jobs array')
        background_tasks.add_task(start_async_jobs, post_body, new_job.parameter[0].valueString)
        logger.info('Added background task')
        # TODO: Add location header to response with relative URL
        return new_job.dict()
    else:
        return start_jobs(post_body)

def start_async_jobs(post_body: Parameters, uid: str):
    logger.info(jobs)
    jobs[uid].parameter[2].valueString = start_jobs(post_body)
    jobs[uid].parameter[1].valueString = "complete"

def start_jobs(post_body: Parameters):

    # Make list of parameters
    body_json = post_body.dict()
    parameters = body_json['parameter']
    parameter_names = [x['name'] for x in parameters]
    logger.info(f'Recieved parameters {parameter_names}')

    try:
        patient_id = parameters[parameter_names.index('patientId')]['valueString']
        has_patient_identifier = False
    except ValueError:
        try:
            logger.info('patientID was not found in the parameters posted, trying looking for patientIdentifier')
            patient_identifier = parameters[parameter_names.index('patientIdentifier')]['valueString']
            has_patient_identifier = True
        except ValueError:
            logger.error('patientID or patientIdentifier was not found in parameters posted')
            return make_operation_outcome('required', 'patientID or patientIdentifier was not found in the parameters posted')

    run_all_jobs = False
    try:
        library = parameters[parameter_names.index('job')]['valueString']
        libraries_to_run = [library]
    except ValueError:
        logger.info('job was not found in the parameters posted, will be running all jobs for the jobPackage given')
        run_all_jobs = True

    try:
        form_name = parameters[parameter_names.index('jobPackage')]['valueString']
    except ValueError:
        logger.error('jobPackage was not found in the parameters posted')
        return make_operation_outcome('required', 'jobPackage was not found in the parameters posted')

    # Pull Questionnaire resource ID from CQF Ruler
    r = requests.get(cqfr4_fhir+f'Questionnaire?name={form_name}')
    if r.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {r.status_code}')
        #TODO fix this
        logger.error(r.json())
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with status code {r.status_code}')

    search_bundle = r.json()
    try:
        form_server_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {form_name} and server id {form_server_id}')
    except KeyError:
        logger.error(f'Questionnaire with name {form_name} not found')
        return make_operation_outcome('not-found',f'Questionnaire with name {form_name} not found')

    if run_all_jobs:
        libraries_to_run = []
        library_server_ids = []
        libraries_to_run_extension = search_bundle['entry'][0]['resource']['extension'][0]['extension']
        for extension in libraries_to_run_extension:
            libraries_to_run.append(extension['valueString'])
        logger.info(f'Going to run the following libraries for this jobPackage: {libraries_to_run}')

        for library_name in libraries_to_run:
            r = requests.get(cqfr4_fhir+f'Library?name={library_name}')
            if r.status_code != 200:
                logger.error(f'Getting library from server failed with status code {r.status_code}')
                # TODO: this is going to error sometimes
                logger.error(r.json())
                return make_operation_outcome('transient', f'Getting library from server failed with status code {r.status_code}')

            search_bundle = r.json()
            try:
                library_server_id = search_bundle['entry'][0]['resource']['id']
                library_server_ids.append(library_server_id)
                logger.info(f'Found CQL Library with name {library_name} and server id {library_server_id}')
            except KeyError:
                logger.error('CQL Library with that name not found')
                return make_operation_outcome('not-found','CQL Library with that name not found')

    if not run_all_jobs:
        # Pull CQL library resource ID from CQF Ruler
        r = requests.get(cqfr4_fhir+f'Library?name={library}')
        if r.status_code != 200:
            logger.error(f'Getting library from server failed with status code {r.status_code}')
            logger.error(r.json())
            return make_operation_outcome('transient', f'Getting library from server failed with status code {r.status_code}')

        search_bundle = r.json()
        try:
            library_server_id = search_bundle['entry'][0]['resource']['id']
            library_server_ids = [library_server_id]
            logger.info(f'Found CQL Library with name {library} and server id {library_server_id}')
        except KeyError:
            logger.error('CQL Library with that name not found')
            return make_operation_outcome('not-found','CQL Library with that name not found')

    if has_patient_identifier:
        r = requests.get(external_fhir_server_url+f'/Patient?identifier={patient_identifier}', headers={'Authorization': external_fhir_server_auth})
        if r.status_code != 200:
            logger.error(f'Getting Patient from server failed with status code {r.status_code}')
            # TODO: this is going to error sometimes
            logger.error(r.json())
            return make_operation_outcome('transient', f'Getting library from server failed with status code {r.status_code}')

        search_bundle = r.json()
        try:
            patient_id = search_bundle['entry'][0]['resource']['id']
            logger.info(f'Found Patient with identifier {patient_identifier} and server id {patient_id}')
        except KeyError:
            logger.error('Patient with that identifier not found')
            return make_operation_outcome('not-found','Patient with that identifier not found')

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
                    "status": "active",
                    "connectionType": {
                        "system": "http://terminology.hl7.org/CodeSystem/endpoint-connection-type",
                        "code": "hl7-fhir-rest"
                    },
                    "name": "External FHIR Server",
                    "payloadType": [{
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/endpoint-payload-type",
                            "code": "any"
                        }]
                    }],
                    "address": external_fhir_server_url,
                    "header": [f'Authorization: {external_fhir_server_auth}']
			    }
            }
        ]
    }

    # Pass library id to be evaluated, gets back a future object that represent the pending status of the POST
    logger.info('Start submitting jobs')
    futures = run_cql(library_server_ids, parameters_post)
    logger.info('Submitted all jobs')

    # Passes future to get the results from it, will wait until all are processed until returning results
    logger.info('Start getting job results')
    results = get_cql_results(futures, libraries_to_run, patient_id)
    logger.info(f'Retrieved result for jobs {libraries_to_run}')

    # Upstream request timeout handling
    if type(results)==str:
        return make_operation_outcome('timeout',results)

    # Creates the linked evidence format needed for the UI to render evidence thats related to certain questions from a certain form
    logger.info('Start linking results')
    bundled_results = create_linked_results(results, form_name)
    logger.info('Finished linking results')

    return bundled_results

@formsrouter.get('/forms/status/{uid}')
def get_job_status(uid: str):
    try:
        return jobs[uid]
    except KeyError:
        return make_operation_outcome('not-found', f'The {uid} job id was not found as an async job. Please try running the jobPackage again with a new job id.')

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

def create_linked_results(results: list, form_name: str):

    # Get form (using get_form from this API)
    form = get_form(form_name)

    bundle_entries = []
    logger.debug(results)
    result = results[0]
    target_library = result['libraryName']

    results = flatten_results(results)
    logger.info('"Flattened" Results into the dictionary')

    # For each group of questions in the form
    for group in form['item']:

        # For each question in the group in the form
        for question in group['item']:


            linkId = question['linkId']
            logger.info(f'Working on question {linkId}')
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

            if library != target_library:
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
                'focus': []
            }
            answer_obs = Observation(**answer_obs)

            # Find the result in the CQL library run that corresponds to what the question has defined in its cqlTask extension
            target_result = None
            single_return_value = None
            supporting_resources = None
            empty_single_return = False

            try:
                value_return = results[task]
            except KeyError:
                logger.error(f'The task {task} was not found in the library results')
                return make_operation_outcome('not-found', f'The task {task} was not found in the library results')
            try:
                if value_return['resourceType']=='Bundle':
                    supporting_resources = value_return['entry']
                    single_resource_flag = False
                    logger.info(f'Found task {task} and supporting resources')
                else:
                    resource_type = value_return['resourceType']
                    single_resource_flag = True
                    logger.info(f'Found task {task} result')
            except (KeyError, TypeError) as e:
                single_return_value = value_return
                logger.info('Found single return value')

            if single_return_value == '[]':
                empty_single_return = True
                logger.info('Empty single return')
            if supporting_resources is not None:
                for resource in supporting_resources:
                    try:
                        focus_object = {'reference': resource['fullUrl']}
                        answer_obs.focus.append(focus_object)
                    except KeyError:
                        pass
            if empty_single_return:
                continue

            answer_obs = answer_obs.dict()
            if answer_obs['focus'] == []:
                logger.debug('Answer Observation does not have a focus, deleting field')
                del answer_obs['focus']

            # If cardinality is a series, does the standard return body format
            if cardinality == 'series':
                # Construct final answer object bundle before result bundle insertion
                answer_obs_bundle_item = {
                    'fullUrl' : 'Observation/'+answer_obs_uuid,
                    'resource': answer_obs
                }

            # If cardinality is a single, does a modified return body to have the value in multiple places
            else:
                single_answer = single_return_value
                logger.debug(single_answer)
                if single_answer == None:
                    continue

                #value_key = 'value'+single_return_type
                answer_obs['valueString'] = single_answer
                answer_obs_bundle_item = {
                    'fullUrl' : 'Observation/'+answer_obs_uuid,
                    'resource': answer_obs
                }

            try:
                focus_test = answer_obs_bundle_item['resource']['focus']
            except KeyError:
                try:
                    value_test = answer_obs_bundle_item['resource']['valueString']
                except KeyError:
                    continue
            #Add items to return bundle entry list
            bundle_entries.append(answer_obs_bundle_item)
            if supporting_resources is not None:
                bundle_entries.extend(supporting_resources)

    return_bundle_id = str(uuid.uuid4())
    return_bundle = {
        'resourceType': 'Bundle',
        'id': return_bundle_id,
        'entry': bundle_entries
    }

    delete_list = []
    for i, entry in enumerate(return_bundle['entry']):
        try:
            if entry['valueString'] == None:
                delete_list.append(i)
        except KeyError:
            pass

    for index in sorted(delete_list, reverse=True):
        del return_bundle['entry'][index]

    return return_bundle
