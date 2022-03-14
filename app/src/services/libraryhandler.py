from ..services.errorhandler import error_to_operation_outcome

from ..models.functions import (
    make_operation_outcome, validate_cql, validate_nlpql
)

from ..util.settings import ( cqfr4_fhir )

from fhir.resources.library import Library

import base64
import logging
import requests

logger = logging.getLogger("rcapi.services.libraryhandler")

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
    put_flag = False
    try:
        existing_cql_library = search_bundle['entry'][0]['resource']
        logger.info(f'Found CQL Library with name {name} and version {version}, therefore will be updating the resource with PUT.')
        put_flag = True
    except KeyError:
        put_flag = False
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

    if not put_flag:
        # Store Library object in CQF Ruler
        r = requests.post(cqfr4_fhir+'Library', json=cql_library)
        if r.status_code != 201:
            logger.error(f'Posting Library {name} to server failed with status code {r.status_code}')
            return make_operation_outcome('transient', f'Posting Library {name} to server failed with status code {r.status_code}')
        resource_id = r.json()['id']
        return resource_id
    else:
        cql_library['id'] = existing_cql_library['id']
        r = requests.put(cqfr4_fhir+f'Library/{existing_cql_library["id"]}', json=cql_library)
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
    put_flag = False
    try:
        existing_nlpql_library = search_bundle['entry'][0]['resource']
        put_flag = True
        logger.info(f'Found NLPQL Library with name {name} and version {version}, therefore will be updating this library with PUT.')
    except KeyError:
        put_flag = False
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

    if not put_flag:
        # Store Library object in CQF Ruler
        r = requests.post(cqfr4_fhir+'Library', json=nlpql_library)
        if r.status_code != 201:
            logger.error(f'Posting Library {name} to server failed with status code {r.status_code}')
            return make_operation_outcome('transient', f'Posting Library to server failed with code {r.status_code}')
        resource_id = r.json()['id']
        return resource_id
    else:
        nlpql_library['id'] = existing_nlpql_library['id']
        r = requests.put(cqfr4_fhir+f'Library/{existing_nlpql_library["id"]}', json=nlpql_library)
        if r.status_code != 201:
            logger.error(f'Putting Library {name} to server failed with status code {r.status_code}')
            return make_operation_outcome('transient', f'Putting Library to server failed with code {r.status_code}')
        resource_id = r.json()['id']
        return resource_id