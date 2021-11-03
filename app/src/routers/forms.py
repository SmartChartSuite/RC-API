from fastapi import (
    APIRouter,
    Body,
    HTTPException
)

from ..models.forms import (
    CustomFormatter, StartJobPostBody, NLPQLDict, bundle_forms, run_cql, get_cql_results
)

from typing import Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from fhir.resources.parameters import Parameters
from fhir.resources.operationoutcome import OperationOutcome
from bson import ObjectId
from requests_futures.sessions import FuturesSession

from ..util.settings import formsdb

import os
import base64
import pymongo
import ast
import logging

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
    logger.warning('Test warning')
    return "Please use one of the endpoints, this is just the root of the api"

@formsrouter.get("/forms", response_model=Union[list, dict])
def get_list_of_forms():
    # Pull list of forms from the database
    form_list = []
    all_forms = formsdb.forms.find()

    for form in all_forms:
        del form["_id"]
        form_list.append(form)
    return bundle_forms(form_list)

@formsrouter.get("/forms/cql")
def get_cql_libraries():

    # Pulls list of CQL libraries from the database
    form_list = []
    all_forms = formsdb.cql.find()
    for document in all_forms:
        form_meta = {"name": document["name"],
                    "version": document["version"]}
        form_list.append(form_meta)
    return form_list

@formsrouter.get("/forms/cql/{libraryName}")
def get_cql(libraryName: str):

    # Return CQL library
    cql_library = formsdb.cql.find_one({"name": libraryName})
    if cql_library is None:
        raise HTTPException(status_code=404, detail='CQL Library not found')

    # Decode CQL from base64 Library encoding in DB
    base64_cql = cql_library['content'][0]['data']
    cql_bytes = base64.b64decode(base64_cql)
    decoded_cql = cql_bytes.decode('ascii')
    return decoded_cql

@formsrouter.get("/forms/{form_id}", response_model=Union[dict, str])
def get_form(form_id: str):

    # Return Questionnaire from the DB based on a form_id
    result_form = formsdb.forms.find_one({'id': form_id})
    if result_form is None:
        raise HTTPException(404, 'Form with that ID not found in the database')
    else :
        del result_form["_id"]
        return result_form

@formsrouter.post("/forms")
def create_form(questions: Questionnaire):

    # Create Questionnaire in the DB
    duplicate = formsdb.forms.find_one({"id": questions.id})

    # Determines if theres a duplicate form or not, if there is, returns the first statement, if not, creates the form in the db
    if duplicate is not None:
        return f"This Questionnaire already exists in the database. To update, use PUT at /forms/{questions.id}. If you would like to create a new version of the form, change the id of the Questionnaire resource and try POSTing again."
    else:
        result = formsdb.forms.insert_one(questions.dict())
    if result.acknowledged:
        return f"You have created a Questionnaire with an id of {questions.id} in the database"
    else: return 'Something went wrong!'

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
        # TODO: Turn this return into an OperationOutcome
        raise HTTPException(404, 'patientID was not found in the parameters posted')

    try:
        library = parameters[parameter_names.index('job')]['valueString']
    except ValueError:
        logger.error('job was not found in the parameters posted')
        # TODO: Turn this return into an OperationOutcome
        raise HTTPException(404, 'job was not found in the parameters posted')

    try:
        form_id = parameters[parameter_names.index('jobPackage')]['valueString']
    except ValueError:
        logger.error('jobPackage was not found in the parameters posted')
        # TODO: Turn this return into an OperationOutcome
        raise HTTPException(404, 'jobPackage was not found in the parameters posted')

    # Pull CQL libraries from db
    cql_library = formsdb.cql.find_one({'name': library})
    if cql_library is None:
        logger.error(f'The library {library} does not exist in the database.')
        # TODO: Turn this return into an OperationOutcome
        return f'Your evidence bundle {library} does not exist in the database. Please POST that to /forms/cql before trying to run the CQL.'
    logger.info('Found CQL Library in the database')

    # Decodes and formats CQL from the base64 encoded data in the Library resource
    base64_cql = cql_library['content'][0]['data']
    cql_bytes = base64.b64decode(base64_cql)
    decoded_cql = cql_bytes.decode('utf-8')
    formatted_cql = str(decoded_cql.replace('"', '\"'))

    # Creates post body for the CQL Execution Service
    full_post_body = {
        "code": formatted_cql,
        "dataServiceUri": os.environ["DATA_SERVICE_URL"],
        "dataUser":"client",
        "dataPass":"secret",
        "patientId": patient_id,
        "terminologyServiceUri": os.environ["TERMOLOGY_SERVICE_URL"],
        "terminologyUser": os.environ["TERMOLOGY_USER"],
        "terminologyPass": os.environ["TERMOLOGY_PASS"]
    }
    post_bodies = [full_post_body]
    logger.info('Created post body')

    # Pass list of post bodies to be POSTed to the CQL Execution Service, gets back a list of future objects that represent the pending status of the POSTs
    logger.info('Start submitting jobs')
    futures = run_cql(post_bodies)
    logger.info('Submitted all jobs')

    # Passes list of futures to get the results from them, will wait until all are processed until returning results
    logger.info('Start getting job results')
    results = get_cql_results(futures, [library], patient_id)
    logger.info(f'Retrived results for job {library}')

    # Creates the linked evidence format needed for the UI to render evidence thats related to certain questions from a certain form
    logger.info('Start linking results')
    linked_results = create_linked_results(results, form_id, formsdb)
    logger.info('Finished linking results')

    return linked_results

@formsrouter.post("/forms/nlpql")
def save_nlpql(post_body: NLPQLDict):

    # Saves NLPQL into the database in a simple format
    # TODO: Convert to Library resource format for this to prepare for future
    result = formsdb.nlpql.insert_one(post_body.dict())
    return f'Saved NLPQL file named {post_body.name} in database'

@formsrouter.post("/forms/cql")
def save_cql(code: str = Body(...)):

    # Get name and version of cql library
    split_cql = code.split()
    name = split_cql[1]
    version = split_cql[3].strip("'")

    # Encode CQL as base64Binary
    code_bytes = code.encode('utf-8')
    base64_bytes = base64.b64encode(code_bytes)
    base64_cql = base64_bytes.decode('utf-8')

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
    print(cql_library)

    # Store Library object in db
    result = formsdb.cql.insert_one(cql_library)
    if result.acknowledged:
        return f'Saved CQL Library resource named {name} in database'
    else:
        return f'Something went wrong!'

@formsrouter.put("/forms/{form_id}")
def update_form(form_id: str, new_questions: Questionnaire):
    # Replace form based on form_id
    result = formsdb.forms.replace_one({"id": form_id}, new_questions.dict())

    # If it didnt actually do anything, returns a string saying there wasn't one found to update, if it does, returns that it was successful
    if result.modified_count != 0:
        return "You have updated a Questionnaire with an id of {}".format(form_id)
    else:
        return "There was no Questionnaire found with that id. Please first POST this Questionnaire to the database."


def create_linked_results(results: list, form_id: str, db: pymongo.database.Database):


    # Get form from DB, raise 404 Not Found if form_id doesnt exist in DB
    form = db.forms.find_one({'id': form_id})
    if form is None:
        raise HTTPException(404, 'Form needed to create evidence links not found')

    linked_results = {}

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
            for library_result in results:
                if library_result['libraryName'] == library:
                    target_library = library_result
                    break
            # if library was not found, just skip rest of loop to move on because its not needed
            if target_library is None:
                continue

            # Find the result in the CQL library run that corresponds to what the question has defined in its cqlTask extension
            target_result = None
            for result in target_library['results']:
                if result['name'] == task:
                    target_result = result['result']
                    break
            # If task isnt in the defined library, raise 404 Not Found
            if target_result is None:
                raise HTTPException(404, f'CQL result {task} not found in library {library}')

            # Flag if we need to format the response as a real resource, or if its a single string (relates to cardinality extension)
            full_resource_flag = False

            if target_result[0] in ['[', '{']:
                # Format results as lists and objects and flags as a full resource
                formatted_result = ast.literal_eval(target_result)
                full_resource_flag = True
            else:
                formatted_result = target_result

            # If cardinality is a series, does the standard return body format
            if cardinality == 'series':
                body = {
                    'answer': {'type': question['type'] },
                    'cqlResults': formatted_result
                }
            # If cardinality is a single, does a modified return body to have the value in multiple places
            else:
                single_answer = formatted_result
                value_key = 'valueString'
                if full_resource_flag:
                    full_key = 'value'+question['type'].capitalize()
                    single_answer = formatted_result[full_key]
                    value_key = full_key
                body = {
                    'answer': {'type': question['type'], value_key : single_answer},
                    'cqlResults': formatted_result
                }
            linked_results[linkId] = body
    return linked_results

