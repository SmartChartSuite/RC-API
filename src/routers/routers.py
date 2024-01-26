'''Routing module for the API'''
import os
import base64
import logging
import uuid
from typing import Union, Dict, OrderedDict, Any
import requests
from requests import Response
from datetime import datetime

from fastapi import (
    APIRouter, Body, BackgroundTasks
)
from fastapi.responses import JSONResponse
from fastapi_utils.tasks import repeat_every

from fhir.resources.bundle import Bundle
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from fhir.resources.parameters import Parameters
from fhir.resources.operationoutcome import OperationOutcome

from ..services.libraryhandler import (create_cql, create_nlpql)

from ..models.models import ParametersJob
from ..models.functions import (
    make_operation_outcome, run_cql, run_nlpql, get_results, check_results, create_linked_results, validate_cql
)
from ..models.functions import get_form as functions_get_form
from ..util.settings import (
    cqfr4_fhir, external_fhir_server_url, external_fhir_server_auth, nlpaas_url
)

# Create logger
logger = logging.getLogger('rcapi.routers.routers')

apirouter = APIRouter()
jobs: Dict[str, ParametersJob] = {}

@apirouter.on_event("startup")
@repeat_every(seconds=60*60*24, logger=logger)
def clear_jobs_array():
    logger.info('Clearing jobs array...')
    global jobs
    del jobs
    jobs = {} # noqa: F841
    logger.info('Finished clearing jobs!')


@apirouter.get("/")
def root():
    '''Root return function for the API'''
    logger.info('Retrieved root of API')
    return make_operation_outcome('processing', 'This is the base URL of API. Unable to handle this request as it is the root.')


@apirouter.get("/health")
def health_check() -> dict:
    '''Health check endpoint'''
    logger.info('Retrieved health check endpoint')
    cqf_ruler_up: bool = False
    cqf_ruler_reason: str = ''
    nlpaas_up: bool = False
    nlpaas_reason: str = ''
    rcapi_up: bool = False
    rcapi_reason: str = ''
    oo_template = {'issue': []}

    try:
        cqf_ruler_resp = requests.get(cqfr4_fhir+'metadata')
        if cqf_ruler_resp.status_code == 200:
            cqf_ruler_up = True
            cqf_ruler_reason = 'CQF Ruler is up and running'
            oo_template['issue'].append({'severity': 'information', 'code': 'informational', 'diagnostics': cqf_ruler_reason})
        elif cqf_ruler_resp.status_code == 404:
            cqf_ruler_reason = "CQF Ruler returned a 404, URL not found, ensure you used the correct URL in the environment variable CQF_RULER_R4"
            oo_template['issue'].append({'severity': 'error', 'code': 'transient', 'diagnostics': cqf_ruler_reason})
        else:
            cqf_ruler_reason = cqf_ruler_resp.text
            oo_template['issue'].append({'severity': 'error', 'code': 'transient', 'diagnostics': cqf_ruler_reason})
    except requests.exceptions.ConnectionError:
        logger.error('Could not connect to CQF Ruler, requests will be unable to be completed')
        cqf_ruler_reason = 'Could not connect to CQF Ruler, ensure the service is running and the correct URL is provided in the environment variable CQF_RULER_R4'
        oo_template['issue'].append({'severity': 'error', 'code': 'transient', 'diagnostics': cqf_ruler_reason})

    if nlpaas_url:
        try:
            nlpaas_resp = requests.get(nlpaas_url)
            if nlpaas_resp.status_code == 200:
                nlpaas_up = True
                nlpaas_reason = 'NLPaaS is up and running'
                oo_template['issue'].append({'severity': 'information', 'code': 'informational', 'diagnostics': nlpaas_reason})
            elif nlpaas_resp.status_code == 404:
                nlpaas_reason = 'NLPaaS returned a 404, URL not found, ensure you used the correct URL in the environment variable NLPAAS_URL'
                oo_template['issue'].append({'severity': 'warning', 'code': 'transient', 'diagnostics': nlpaas_reason})
            else:
                nlpaas_reason = nlpaas_resp.text
                logger.warning('Could not connect to NLPaaS, NLP requests will be unable to be completed')
                oo_template['issue'].append({'severity': 'warning', 'code': 'transient', 'diagnostics': nlpaas_reason})
        except requests.exceptions.ConnectionError:
            logger.warning('Could not connect to NLPaaS, NLP requests will be unable to be completed')
            nlpaas_reason = 'Could not connect to NLPaaS, ensure the service is running and the correct URL is provided in the environment variable NLPAAS_URL'
            oo_template['issue'].append({'severity': 'warning', 'code': 'transient', 'diagnostics': nlpaas_reason})
    else:
        nlpaas_up = False
        nlpaas_reason = 'NLPAAS_URL not defined in environmental variables, no NLP jobs will be completed. Please set this variable if you want to run NLP jobs'
        logger.warning('Could not connect to NLPaaS, NLP requests will be unable to be completed')
        oo_template['issue'].append({'severity': 'warning', 'code': 'transient', 'diagnostics': nlpaas_reason})

    if cqf_ruler_up:
        rcapi_up = True
        rcapi_reason = 'RC-API is up and running'
        oo_template['issue'].append({'severity': 'information', 'code': 'informational', 'diagnostics': rcapi_reason})
    else:
        rcapi_reason = 'RC-API is not up and running because: ' + cqf_ruler_reason
        oo_template['issue'].append({'severity': 'error', 'code': 'transient', 'diagnostics': rcapi_reason})

    return OperationOutcome.parse_obj(oo_template).dict()


@apirouter.get("/forms", response_model=dict)
def get_list_of_forms():
    '''Get Bundle of Questionnaires from CQF Ruler'''
    cqfr4_fhir_url = os.environ["CQF_RULER_R4"]
    # Pull list of forms from CQF Ruler
    if cqfr4_fhir_url[-5:] == 'fhir/':
        pass
    elif cqfr4_fhir_url[-4:] == 'fhir':
        cqfr4_fhir_url = cqfr4_fhir_url + '/'
    else:
        return make_operation_outcome('invalid',
                                      f'The CQF Ruler url ({cqfr4_fhir_url}) passed in as an environmental variable is not correct, please check that it ends with fhir or fhir/')  # type:ignore
    req = requests.get(cqfr4_fhir_url + 'Questionnaire')
    if req.status_code == 200:
        return req.json()
    logger.error(f'Getting Questionnaires from server failed with code {req.status_code}')
    return make_operation_outcome('transient', f'Getting Questionnaires from server failed with code {req.status_code}.')


@apirouter.get("/forms/cql")
def get_cql_libraries():
    '''Pulls list of CQL libraries from CQF Ruler'''

    req = requests.get(cqfr4_fhir + 'Library?content-type=text/cql')
    if req.status_code == 200:
        return req.json()

    logger.error(f'Getting CQL Libraries from server failed with status code {req.status_code}')
    return make_operation_outcome('transient', f'Getting CQL Libraries from server failed with code {req.status_code}')


@apirouter.get("/forms/nlpql")
def get_nlpql_libraries():
    '''Pulls list of CQL libraries from CQF Ruler'''

    req = requests.get(cqfr4_fhir + 'Library?content-type=text/nlpql')
    if req.status_code == 200:
        return req.json()

    logger.error(f'Getting NLPQL Libraries from server failed with status code {req.status_code}')
    return make_operation_outcome('transient', f'Getting NLPQL Libraries from server failed with code {req.status_code}')


@apirouter.get("/forms/cql/{library_name}")
def get_cql(library_name: str):
    '''Return CQL library based on name'''
    req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text%2Fcql')
    if req.status_code != 200:
        logger.error(f'Getting library from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting Library from server failed with code {req.status_code}')

    search_bundle = req.json()
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


@apirouter.get("/forms/nlpql/{library_name}")
def get_nlpql(library_name: str):
    '''Return NLPQL library by name'''
    req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text%2Fnlpql')
    if req.status_code != 200:
        logger.error(f'Getting library from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting Library from server failed with code {req.status_code}')

    search_bundle = req.json()
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


@apirouter.get("/forms/{form_name}", response_model=Union[dict, str])
def get_form(form_name: str):
    '''Return Questionnaire from CQF Ruler based on form name'''
    req = requests.get(cqfr4_fhir + f'Questionnaire?name:exact={form_name}')
    if req.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with code {req.status_code}')

    search_bundle = req.json()
    try:
        questionnaire = search_bundle['entry'][0]['resource']
        logger.info(f'Found Questionnaire with name {form_name}')
        return questionnaire
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return make_operation_outcome('not-found', f'Questionnaire named {form_name} not found on the FHIR server.')


@apirouter.post("/forms")
def save_form(questions: Questionnaire):
    '''Check to see if library and version of this exists'''

    req = requests.get(cqfr4_fhir + f'Questionnaire?name:exact={questions.name}&version={questions.version}')
    if req.status_code != 200:
        logger.error(f'Trying to get Questionnaire from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with code {req.status_code}')

    search_bundle = req.json()
    try:
        questionnaire_current_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {questions.name} and version {questions.version}')
        logger.info('Not completing POST operation because a Questionnaire with that name and version already exist on this FHIR Server')
        logger.info('Change Questionnaire name or version number or use PUT to update this version')
        return make_operation_outcome('duplicate', f'There is already a Questionnaire with this name with resource id {questionnaire_current_id}')
    except KeyError:
        logger.info('Questionnaire with that name not found, continuing POST operation')

    # Create Questionnaire in CQF Ruler
    req = requests.post(cqfr4_fhir + 'Questionnaire', json=questions.dict())
    if req.status_code != 201:
        logger.error(f'Posting Questionnaire to server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Posting Questionnaire to server failed with code {req.status_code}')

    resource_id = req.json()['id']
    return make_operation_outcome('informational', f'Resource successfully posted with id {resource_id}', severity='information')


@apirouter.post("/forms/start", response_model=None)
def start_jobs_header_function(post_body: Parameters, background_tasks: BackgroundTasks, asyncFlag: bool = False) -> JSONResponse | dict:
    '''Header function for starting jobs either synchronously or asynchronously'''
    if asyncFlag:
        logger.info('asyncFlag detected, running asynchronously')
        new_job = ParametersJob()
        new_job.parameter[0].valueString = str(uuid.uuid4())
        new_job.parameter[1].valueDateTime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f'Created new job with jobId {new_job.parameter[0].valueString}')
        jobs[new_job.parameter[0].valueString] = new_job
        logger.info('Added to jobs array')
        background_tasks.add_task(start_async_jobs, post_body, new_job.parameter[0].valueString)
        logger.info('Added background task')
        return JSONResponse(content=new_job.dict(), headers={'Location': f'/forms/status/{new_job.parameter[0].valueString}'})

    return start_jobs(post_body)


def start_async_jobs(post_body: Parameters, uid: str) -> None:
    '''Start job asychronously'''
    job_result = start_jobs(post_body)
    if uid not in jobs:
        new_job = ParametersJob()
        new_job.parameter[0].valueString = uid
        new_job.parameter[1].valueDateTime = "9999-12-31T00:00:00Z"
        jobs[uid] = new_job
    jobs[uid].parameter[3].resource = job_result
    jobs[uid].parameter[2].valueString = "complete"
    logger.info(f'Job id {uid} complete and results are available at /forms/status/{uid}')


def start_jobs(post_body: Parameters) -> dict:
    '''Start jobs for both sync and async'''
    # Make list of parameters
    body_json = post_body.dict()
    parameters = body_json['parameter']
    if not all([((any([name.startswith('value') for name in param.keys()])) or 'resource' in param or 'part' in param) for param in parameters]):
        logger.error('Parameters model is invalid, please check that all parameters have a name and value')
        return make_operation_outcome(code='structure', diagnostics='Parameters.parameters is not correct, ensure it has a name and value')
    parameter_names: list[str] = [x['name'] for x in parameters]
    logger.info(f'Recieved parameters {parameter_names}')

    try:
        patient_id: str = parameters[parameter_names.index('patientId')]['valueString']
        has_patient_identifier = False
    except ValueError:
        try:
            logger.info('patientID was not found in the parameters posted, trying looking for patientIdentifier')
            patient_identifier: str | int = parameters[parameter_names.index('patientIdentifier')]['valueString']
            has_patient_identifier = True
        except ValueError:
            logger.error('patientID or patientIdentifier was not found in parameters posted')
            return make_operation_outcome('required', 'patientID or patientIdentifier was not found in the parameters posted')
    if not has_patient_identifier:
        patient_identifier = 1

    run_all_jobs = False
    try:
        library: str = parameters[parameter_names.index('job')]['valueString']
        libraries_to_run: list[str] = [library]
    except ValueError:
        logger.info('job was not found in the parameters posted, will be running all jobs for the jobPackage given')
        run_all_jobs = True

    try:
        form_name: str = parameters[parameter_names.index('jobPackage')]['valueString']
    except ValueError:
        logger.error('jobPackage was not found in the parameters posted')
        return make_operation_outcome('required', 'jobPackage was not found in the parameters posted')

    form_version: str | None = None
    try:
        form_version = parameters[parameter_names.index('jobPackageVersion')]['valueString']
    except ValueError:
        logger.info('No form version given, will be using newest created Questionnaire matching the name')

    # Pull Questionnaire resource ID from CQF Ruler
    questionnaire = functions_get_form(form_name=form_name, form_version=form_version)
    if questionnaire["resourceType"] == "OperationOutcome":
        return questionnaire

    cql_flag = False
    nlpql_flag = False
    if run_all_jobs:
        cql_libraries_to_run: list[str] = []
        nlpql_libraries_to_run: list[str] = []
        cql_library_server_ids: list[str] = []
        nlpql_library_server_ids: list[str] = []

        cql_libraries_to_run_extension: dict = questionnaire['extension'][0]['extension']
        for extension in cql_libraries_to_run_extension:
            cql_libraries_to_run.append(extension['valueString'])
        logger.info(f'Going to run the following CQL libraries for this jobPackage: {cql_libraries_to_run}')

        try:
            nlpql_libraries_to_run_extension: dict = questionnaire['extension'][1]['extension']
            for extension in nlpql_libraries_to_run_extension:
                nlpql_libraries_to_run.append(extension['valueString'])
            logger.info(f'Going to run the following NLPQL libraries for this jobPackage: {nlpql_libraries_to_run}')
        except IndexError:
            logger.info('No NLPQL Libraries found, moving on')

        libraries_to_run = cql_libraries_to_run + nlpql_libraries_to_run

        cql_libraries_to_run = []
        nlpql_libraries_to_run = []

        for library_name_full in libraries_to_run:
            library_name, library_name_ext = library_name_full.split('.')
            req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text/{library_name_ext}')
            if req.status_code != 200:
                logger.error(f'Getting library from server failed with status code {req.status_code}')
                return make_operation_outcome('transient', f'Getting library from server failed with status code {req.status_code}')

            search_bundle = req.json()
            try:
                library_server_id = search_bundle['entry'][0]['resource']['id']
                logger.info(f'Found {library_name_ext.upper()} Library with name {library_name} and server id {library_server_id}')
                try:
                    library_type = search_bundle['entry'][0]['resource']['content'][0]['contentType']
                except KeyError:
                    return make_operation_outcome('invalid',
                                                  (f'Library with name {library_name} does not contain a content type in content[0].contentType. '
                                                   'Because of this, the API is unable to process the library. Please update the Library to include a content type.'))
                if library_type == 'text/nlpql':
                    nlpql_flag = True
                    nlpql_library_server_ids.append(library_server_id)
                    nlpql_libraries_to_run.append(library_name)
                elif library_type == 'text/cql':
                    cql_flag = True
                    cql_library_server_ids.append(library_server_id)
                    cql_libraries_to_run.append(library_name)
                else:
                    logger.error(f'Library with name {library_name} was found but content[0].contentType was not found to be text/cql or text/nlpql.')
                    return make_operation_outcome('invalid', f'Library with name {library_name} was found but content[0].contentType was not found to be text/cql or text/nlpql.')
            except KeyError:
                logger.error(f'Library with name {library_name} not found')
                return make_operation_outcome('not-found', f'Library with name {library_name} not found')

    if not run_all_jobs:
        # Pull CQL library resource ID from CQF Ruler
        library_name_ext_split = library.split('.') #type: ignore
        if len(library_name_ext_split) == 2:
            library_name = library_name_ext_split[0]
            library_type = library_name_ext_split[1]
        else:
            library_name = library #type: ignore
            library_type = 'cql'

        req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text/{library_type.lower()}')
        if req.status_code != 200:
            logger.error(f'Getting library from server failed with status code {req.status_code}')
            return make_operation_outcome('transient', f'Getting library from server failed with status code {req.status_code}')

        search_bundle = req.json()
        try:
            library_server_id = search_bundle['entry'][0]['resource']['id']

            logger.info(f'Found Library with name {library} and server id {library_server_id}') #type: ignore
            try:
                library_type = search_bundle['entry'][0]['resource']['content'][0]['contentType']
            except KeyError:
                return make_operation_outcome('invalid',
                                              (f'Library with name {library_name} does not contain a content type in content[0].contentType. '
                                               'Because of this, the API is unable to process the library. Please update the Library to include a content type.'))
            if library_type == 'text/nlpql':
                nlpql_flag = True
                nlpql_library_server_ids = [library_server_id]
                nlpql_libraries_to_run = search_bundle['entry'][0]['resource']['name']
            elif library_type == 'text/cql':
                cql_flag = True
                cql_library_server_ids = [library_server_id]
                cql_libraries_to_run = search_bundle['entry'][0]['resource']['name']
            else:
                logger.error(f'Library with name {library_name} was found but content[0].contentType was not found to be text/cql or text/nlpql.')
                return make_operation_outcome('invalid', f'Library with name {library_name} was found but content[0].contentType was not found to be text/cql or text/nlpql.')
        except KeyError:
            logger.error(f'Library with name {library} not found') #type: ignore
            return make_operation_outcome('not-found', f'Library with name {library} not found') #type: ignore

    if has_patient_identifier:
        if external_fhir_server_auth:
            req = requests.get(external_fhir_server_url + f'/Patient?identifier={patient_identifier}', headers={'Authorization': external_fhir_server_auth}) #type: ignore
        else:
            req = requests.get(external_fhir_server_url + f'/Patient?identifier={patient_identifier}') #type: ignore
        if req.status_code != 200:
            logger.error(f'Getting Patient from server failed with status code {req.status_code}')
            return make_operation_outcome('transient', f'Getting Patient from server failed with status code {req.status_code}')

        search_bundle = req.json()
        try:
            patient_id = search_bundle['entry'][0]['resource']['id']
            logger.info(f'Found Patient with identifier {patient_identifier} and server id {patient_id}') #type: ignore
        except KeyError:
            logger.error(f'Patient with identifier {patient_identifier} not found') #type: ignore
            return make_operation_outcome('not-found', f'Patient with identifier {patient_identifier} not found') #type: ignore

    # Create parameters post body for library evaluation
    parameters_post = {
        'resourceType': 'Parameters',
        'parameter': [
            {
                'name': 'patientId',
                'valueString': patient_id #type: ignore
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
                    "address": external_fhir_server_url
                }
            }
        ]
    }
    if external_fhir_server_auth:
        parameters_post['parameter'][2]['resource']["header"] = [f'Authorization: {external_fhir_server_auth}']

    # Pass library id to be evaluated, gets back a future object that represent the pending status of the POST
    futures = []
    if cql_flag:
        logger.info('Start submitting CQL jobs')
        futures_cql = run_cql(cql_library_server_ids, parameters_post) #type: ignore
        futures.append(futures_cql)
        logger.info('Submitted all CQL jobs')
    if nlpql_flag and nlpaas_url != 'False':
        logger.info('Start submitting NLPQL jobs')
        futures_nlpql = run_nlpql(nlpql_library_server_ids, patient_id, external_fhir_server_url, external_fhir_server_auth) #type: ignore
        if isinstance(futures_nlpql, dict):
            return futures_nlpql
        futures.append(futures_nlpql)
        logger.info('Submitted all NLPQL jobs.')

    if cql_flag and nlpql_flag and nlpaas_url != 'False':
        libraries_to_run = [cql_libraries_to_run, nlpql_libraries_to_run] #type: ignore
    elif cql_flag:
        libraries_to_run = [cql_libraries_to_run] #type: ignore
    elif nlpql_flag and nlpaas_url != 'False':
        libraries_to_run = [[nlpql_libraries_to_run]] #type: ignore

    # Passes future to get the results from it, will wait until all are processed until returning results
    logger.info('Start getting job results')
    results_list: tuple[list[dict], list[dict]] = get_results(futures, libraries_to_run, patient_id, [cql_flag, nlpql_flag]) #type: ignore
    results_cql: list[dict] = results_list[0]
    results_nlpql: list[dict] = results_list[1]
    logger.info(f'Retrieved results for jobs {libraries_to_run}') #type: ignore

    # Upstream request timeout handling
    if isinstance(results_cql, str):
        return make_operation_outcome('timeout', results_cql)

    # Checks results for any CQL issues
    results_check_return = check_results(results_cql)

    if isinstance(results_check_return, dict):
        logger.error('There were errors in the CQL, see OperationOutcome')
        logger.error(results_check_return)
    else:
        logger.info('No errors returned from backend services, continuing to link results')

    # Creates the registry bundle format
    logger.info('Start linking results')
    bundled_results = create_linked_results([results_cql, results_nlpql], form_name, patient_id) #type: ignore
    if bundled_results["resourceType"] == "OperationOutcome":
        logger.error(bundled_results["issue"][0]["diagnostics"])
    else:
        logger.info(f'Finished linking results, returning Bundle with {bundled_results["total"] if "total" in bundled_results else 0} entries')

    ##return Bundle(**bundled_results).dict(exclude_none=True)
    return bundled_results


@apirouter.get('/forms/status/all')
def return_all_jobs():
    '''Return all job statuses'''
    return jobs


@apirouter.get('/forms/status/{uid}')
def get_job_status(uid: str):
    '''Return the status of a specific job'''
    try:
        try:
            job_results = jobs[uid].parameter[3].resource
            job_results_severity = job_results['issue'][0]['severity']
            job_results_code = job_results['issue'][0]['code']
            if job_results_code == 'not-found':
                return JSONResponse(status_code=404, content=job_results)
            if job_results_severity == 'error':
                return JSONResponse(status_code=500, content=job_results)
            else:
                return jobs[uid]
        except KeyError:
            return jobs[uid]
    except KeyError:
        return JSONResponse(content=make_operation_outcome('not-found', f'The {uid} job id was not found as an async job. Please try running the jobPackage again with a new job id.'), status_code=404)


@apirouter.post("/forms/nlpql")
def save_nlpql(code: str = Body(...)):
    '''Persist NLPQL as a Library Resource on CQF Ruler'''
    resource_id = create_nlpql(code)
    if isinstance(resource_id, str):
        return JSONResponse(content=make_operation_outcome('informational', f'Resource successfully posted with id {resource_id}', severity='information'), status_code=201)
    elif isinstance(resource_id, dict):
        return JSONResponse(content=resource_id, status_code=400)


@apirouter.post("/forms/cql")
def save_cql(code: str = Body(...)):
    '''Persist CQL as a Library Resource on CQF Ruler'''
    resource_id = create_cql(code)
    # Empty body is handled by FASTAPI when parsing the request body. This handling is used as a fallback for any other potential ValueErrors.
    if isinstance(resource_id, ValueError):
        return JSONResponse(content=make_operation_outcome('invalid', 'Value Error'), status_code=400)
    # TODO: Add additional error handling.
    return JSONResponse(content=make_operation_outcome('informational', f'Resource successfully posted with id {resource_id}', severity='information'), status_code=201)


@apirouter.put("/forms/{form_name}")
def update_form(form_name: str, new_questions: Questionnaire):
    '''Update Questionnaire using namee'''
    req = requests.get(cqfr4_fhir + f'Questionnaire?name:exact={form_name}')
    if req.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with status code {req.status_code}')

    search_bundle = req.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Questionnaire with name {form_name}')
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return make_operation_outcome('not-found', f'Getting Questionnaire named {form_name} not found on server')

    new_questions.id = resource_id
    req = requests.put(cqfr4_fhir + f'Questionnaire/{resource_id}', json=new_questions.dict())
    if req.status_code != 200:
        logger.error(f'Putting Questionnaire from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Putting Questionnaire from server failed with status code {req.status_code}')

    return make_operation_outcome('informational', f'Questionnaire {form_name} successfully put on server with resource_id {resource_id}', severity='information')


@apirouter.put("/forms/cql/{library_name}")
def update_cql(library_name: str, code: str = Body(...)):
    '''Update CQL Library in CQF Ruler by name'''
    req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text/cql')
    if req.status_code != 200:
        logger.error(f'Getting Library from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting CQL Library from server failed with status code {req.status_code}')

    search_bundle = req.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found CQL Library with name {library_name}')
    except KeyError:
        logger.error('Library with that name not found')
        return make_operation_outcome('not-found', f'Getting CQL Library named {library_name} not found on server')

    # Validate the CQL before updating
    validation_results = validate_cql(code)
    if isinstance(validation_results, dict):
        return validation_results

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
        'type': {'coding': [{'code': 'logic-library'}]},
        'content': [{
            'contentType': 'text/cql',
            'data': base64_cql
        }]
    }
    cql_library = Library(**data)
    cql_library = cql_library.dict()
    cql_library['content'][0]['data'] = base64_cql
    cql_library['id'] = resource_id
    logger.info('Created Library object')

    req = requests.put(cqfr4_fhir + f'Library/{resource_id}', json=cql_library)
    if req.status_code != 200:
        logger.error(f'Putting Library from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Putting Library from server failed with status code {req.status_code}')

    return JSONResponse(content=make_operation_outcome('informational', f'Library {library_name} successfully put on server', severity='information'), status_code=201)


@apirouter.put("/forms/nlpql/{library_name}")
def update_nlpql(library_name: str, code: str = Body(...)):
    '''Update NLPQL Library on CQF Ruler'''

    req = requests.get(cqfr4_fhir + f'Library?name={library_name}&content-type=text/nlpql')
    if req.status_code != 200:
        logger.error(f'Getting NLPQL Library from server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Getting NLPQL Library from server failed with status code {req.status_code}')
    search_bundle = req.json()
    try:
        resource_id = search_bundle['entry'][0]['resource']['id']
        logger.info(f'Found Library with name {library_name} and resource id {resource_id}')
    except KeyError:
        logger.error('Library with that name not found')
        return make_operation_outcome('not-found', f'Getting Library named {library_name} not found on server')

    # Get name and version of NLPQL Library
    split_nlpql = code.split()
    name = split_nlpql[5].strip('"')
    version = split_nlpql[7].strip(';').strip('"')

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
        'type': {'coding': [{'code': 'logic-library'}]},
        'content': [{
            'contentType': 'text/nlpql',
            'data': base64_nlpql
        }]
    }
    nlpql_library = Library(**data)
    nlpql_library = nlpql_library.dict()
    nlpql_library['content'][0]['data'] = base64_nlpql
    nlpql_library['id'] = resource_id

    req = requests.put(cqfr4_fhir + f'Library/{resource_id}', json=nlpql_library)
    if req.status_code != 200:
        logger.error(f'Putting Library to server failed with status code {req.status_code}')
        return make_operation_outcome('transient', f'Putting Library to server failed with status code {req.status_code}')

    return JSONResponse(content=make_operation_outcome('informational', f'Library {library_name} successfully put on server', severity='information'), status_code=201)
