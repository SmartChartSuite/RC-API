from fastapi.responses import JSONResponse

from ..services.errorhandler import error_to_operation_outcome
from ..models.functions import (
    make_operation_outcome, validate_cql, validate_nlpql
)
from ..util.settings import ( cqfr4_fhir )

from typing import Union, Dict
from fhir.resources.questionnaire import Questionnaire #TODO: replace to using fhirclient package as well as below imports
from fhir.resources.library import Library
from fhir.resources.parameters import Parameters

from pprint import pprint

import os
import base64
import logging
import requests
import uuid

logger = logging.getLogger("rcapi.libraryhandler")

def create_cql(cql):
    # Validate
    # Handle
    # Return Success
    try:
        if not cql:
            raise ValueError('CQL is empty string.')
    except ValueError as e:
        logger.exception(e)
        error_to_operation_outcome(e)
        return e

    validation_results = validate_cql(cql)
    if type(validation_results)==dict:
        return validation_results
    else:
        pass

    # Get name and version of cql library
    split_cql = cql.split()
    name = split_cql[1]
    version = split_cql[3].strip("'")

    # Check to see if library and version of this exists
    r = requests.get(cqfr4_fhir+f'Library?name={name}&version={version}&content-type=text/cql')
    if r.status_code != 200:
        logger.error(f'Trying to get library from server failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Getting Library from server failed with status code {r.status_code}')
    search_bundle = r.json()
    try:
        # TODO: Add handling to change to put if this is passed back.
        cql_library = search_bundle['entry'][0]['resource']
        logger.info(f'Found CQL Library with name {name} and version {version}')
        logger.info('Not completing POST operation because a CQL Library with that name and version already exist on this FHIR Server')
        logger.info('Change library name or version number or use PUT to update this version')
        return make_operation_outcome('duplicate', f'There is already a library with that name ({name}) and version ({version})')
    except KeyError:
        logger.info('CQL Library with that name not found, continuing POST operation')

    # Encode CQL as base64Binary
    code_bytes = cql.encode('utf-8')
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
        return make_operation_outcome('transient', f'Posting Library {name} to server failed with status code {r.status_code}')

    resource_id = r.json()['id']
    return resource_id

def create_nlpql(nlpql):
    # Validates NLPQL using NLPaaS before saving to Library
    validation_results = validate_nlpql(nlpql)
    if type(validation_results)==dict:
        return validation_results
    else:
        pass

    # Get name and version of NLPQL Library
    split_nlpql = nlpql.split()

    name = split_nlpql[5].strip('"')
    version = split_nlpql[7].strip(';').strip('"')

    r = requests.get(cqfr4_fhir+f'Library?name={name}&version={version}&content-type=text/nlpql')
    if r.status_code != 200:
        logger.error(f'Trying to get library from server failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Getting Library from server failed with status code {r.status_code}')
    search_bundle = r.json()
    try:
        cql_library = search_bundle['entry'][0]['resource']
        logger.info(f'Found NLPQL Library with name {name} and version {version}')
        logger.info('Not completing POST operation because a NLPQL Library with that name and version already exist on this FHIR Server')
        logger.info('Change library name or version number or use PUT to update this version')
        return make_operation_outcome('duplicate', f'There is already a library with that name ({name}) and version ({version})')
    except KeyError:
        logger.info('NLPQL Library with that name not found, continuing POST operation')

    # Encode NLPQL as base64Binary
    code_bytes = nlpql.encode('utf-8')
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

    # Store Library object in CQF Ruler
    r = requests.post(cqfr4_fhir+'Library', json=nlpql_library)
    if r.status_code != 201:
        logger.error(f'Posting Library {name} to server failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Posting Library to server failed with code {r.status_code}')

    resource_id = r.json()['id']
    return resource_id