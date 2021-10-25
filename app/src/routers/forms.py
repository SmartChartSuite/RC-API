from fastapi import (
    APIRouter,
    Body,
    HTTPException
)

from ..models.forms import (
    StartJobPostBody, NLPQLDict, bundle_forms, run_cql, get_cql_results
)

from typing import Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from bson import ObjectId
from requests_futures.sessions import FuturesSession

from ..util.settings import formsdb

import os
import base64
import pymongo
import ast

import time

formsrouter = APIRouter()


@formsrouter.get("/")
def root():
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
def start_jobs(post_body: StartJobPostBody):

    # Get CQL library names to be run
    libraries = post_body.evidenceBundles

    # Pull CQL libraries from db
    cql_posts = []
    for library in libraries:
        cql_library = formsdb.cql.find_one({'name': library})
        if cql_library is None:
            return f'Your evidence bundle {library} does not exist in the database. Please POST that to /forms/cql before trying to run the CQL.'

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
            "patientId": post_body.patientId,
            "terminologyServiceUri": os.environ["TERMOLOGY_SERVICE_URL"],
            "terminologyUser": os.environ["TERMOLOGY_USER"],
            "terminologyPass": os.environ["TERMOLOGY_PASS"]
        }
        cql_posts.append(full_post_body)
        print(f'Retrieved library named {library}')

    # Pass list of post bodies to be POSTed to the CQL Execution Service, gets back a list of future objects that represent the pending status of the POSTs
    futures = run_cql(cql_posts)
    print('Created futures array')

    # Passes list of futures to get the results from them, will wait until all are processed until returning results
    results = get_cql_results(futures, libraries, post_body.patientId)
    print(f'Retrieved all results from futures for jobs {str(libraries)}')

    # Creates the linked evidence format needed for the UI to render evidence thats related to certain questions from a certain form
    linked_results = create_linked_results(results, post_body.formId, formsdb)

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