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
    # time.sleep(10)
    form_list = []
    all_forms = formsdb.forms.find()

    for form in all_forms:
        del form["_id"]
        form_list.append(form)
    return bundle_forms(form_list)

@formsrouter.get("/forms/cql")
def get_cql_libraries():
    form_list = []
    all_forms = formsdb.cql.find()
    for document in all_forms:
        form_meta = {"name": document["name"],
                    "version": document["version"]}
        form_list.append(form_meta)
    return form_list

@formsrouter.get("/forms/cql/{libraryName}")
def get_cql(libraryName: str):
    cql_library = formsdb.cql.find_one({"name": libraryName})
    if cql_library is None:
        raise HTTPException(status_code=404, detail='CQL Library not found')
    base64_cql = cql_library['content'][0]['data']
    cql_bytes = base64.b64decode(base64_cql)
    decoded_cql = cql_bytes.decode('ascii')
    return decoded_cql

@formsrouter.get("/forms/{form_id}", response_model=Union[dict, str])
def get_form(form_id: str):
    result_form = formsdb.forms.find_one({'id': form_id})
    if result_form is None:
        raise HTTPException(404, 'Form with that ID not found in the database')
    else :
        del result_form["_id"]
        return result_form

@formsrouter.post("/forms")
def create_form(questions: Questionnaire):
    duplicate = formsdb.forms.find_one({"id": questions.id})
    if duplicate is not None:
        return f"This Questionnaire already exists in the database. To update, use PUT at /forms/{questions.id}. If you would like to create a new version of the form, change the id of the Questionnaire resource and try POSTing again."
    else:
        result = formsdb.forms.insert_one(questions.dict())
    if result.acknowledged:
        return f"You have created a Questionnaire with an id of {questions.id} in the database"
    else: return 'Something went wrong!'

@formsrouter.post("/forms/start", response_model=Union[dict, str])
def start_jobs(post_body: StartJobPostBody):
    #get cql library names to be run
    libraries = post_body.evidenceBundles
    #pull cql libraries from db
    cql_posts = []
    for library in libraries:
        cql_library = formsdb.cql.find_one({'name': library})
        if cql_library is None:
            return f'Your evidence bundle {library} does not exist in the database. Please POST that to /forms/cql before trying to run the CQL.'
        base64_cql = cql_library['content'][0]['data']
        cql_bytes = base64.b64decode(base64_cql)
        decoded_cql = cql_bytes.decode('utf-8')
        formatted_cql = str(decoded_cql.replace('"', '\"'))
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


    futures = run_cql(cql_posts)
    print('Created futures array')
    results = get_cql_results(futures, libraries, post_body.patientId)
    print('Retrived all results from futures')
    linked_results = create_linked_results(results, post_body.formId, formsdb)

    return linked_results

@formsrouter.post("/forms/nlpql")
def save_nlpql(post_body: NLPQLDict):
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
    result = formsdb.forms.replace_one({"id": form_id}, new_questions.dict())
    print(result)
    if result.modified_count != 0:
        return "You have updated a Questionnaire with an id of {}".format(form_id)
    else:
        return "There was no Questionnaire found with that id. Please first POST this Questionnaire to the database."

def create_linked_results(results: list, form_id: str, db: pymongo.database.Database):
    # {
    # 1: {
    #    answer: {type: choice},
    #    cqlResults: []
    #    },
    # 2: {},
    # 3: {}
    # }
    form = db.forms.find_one({'id': form_id})
    if form is None:
        raise HTTPException(404, 'Form needed to create evidence links not found')

    linked_results = {}
    for group in form['item']:
        for question in group['item']:
            linkId = question['linkId']
            try:
                for extension in question['extension']:
                    if extension['url'] == 'http://gtri.gatech.edu/fakeFormIg/cqlTask':
                        library_task = extension['valueString']
                library, task = library_task.split('.')
            except KeyError:
                pass

            target_library = None
            for library_result in results:
                if library_result['libraryName'] == library:
                    target_library = library_result
                    break
            if target_library is None:
                break

            target_result = None
            for result in target_library['results']:
                if result['name'] == task:
                    target_result = result['result']
                    break
            if target_result is None:
                raise HTTPException(404, f'CQL result {task} not found in library {library}')

            if target_result[0] in ['[', '{']:
                formatted_result = ast.literal_eval(target_result)
            else:
                formatted_result = target_result

            body = {
                'answer': {'type': question['type'] },
                'cqlResults': formatted_result
            }
            linked_results[linkId] = body

    print(linked_results)
    return linked_results