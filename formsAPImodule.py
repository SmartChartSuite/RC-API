from datetime import datetime
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional, List, Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from bson import ObjectId
from requests_futures.sessions import FuturesSession
from fastapi.middleware.cors import CORSMiddleware
import pymongo
import time
import base64

app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:4200"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class Answer(BaseModel):
    text: str
    value: str

class EvidenceBundleObject(BaseModel):
    information: Optional[List[str]] = None

class Question(BaseModel):
    question_name: str
    question_type: str
    question_number: str
    group: str
    answers: List[Answer]
    evidence_bundle: EvidenceBundleObject
    nlpql_grouping: str

class QuestionsJSON(BaseModel):
    name: str
    owner: str
    description: str
    allocated_users: list
    groups: list
    questions: List[Question]
    evidence_bundles: list
    version: str
    questions_with_evidence_count: Optional[str] = None

class StartJobPostBody(BaseModel):
    formId: str
    evidenceBundles: List[str]
    patientId: str

class NLPQLDict(BaseModel):
    name: str
    content: str

class CQLPost(BaseModel):
    code: str

bundle_template = {
    "resourceType": "Bundle",
    "meta": {
        "lastUpdated": ""
    },
    "type": "collection",
    "entry": []
}

def convertToQuestionnaire(questions: QuestionsJSON):
    
    data = {
        "meta": {
            "profile": ["http://sample.com/StructureDefinition/smartchart-form"]
        },
        "url": "http://hl7.org/fhir/Questionnaire/TacoExample",
        "id": str(questions['_id']),
        "version": questions['version'],
        "title": questions['name'],
        "name": questions['name'].replace(' ',''),
        "status": "draft",
        "description": questions['description'],
        "subjectType": ['Patient'],
        "publisher": questions['owner'],
        "experimental": "true",
        "extension": [
            {
                "url": "form-evidence-bundle-list",
                "extension": list(map(lambda x: {"url":"evidence_bundle", "valueString": x}, questions['evidence_bundles']))
            }
        ]
    }

    quest = Questionnaire(**data)

    quest.item = []
    for i, group in enumerate(questions['groups']):
        item_data = {'linkId': group, 'type': 'group'}
        quest.item.append(item_data)

    for question in questions['questions']:

        groupNumber = questions['groups'].index(question['group'])
        
        if question['question_type']=='TEXT': question_type = 'string'
        elif question['question_type']=='RADIO': question_type = 'choice'
        elif question['question_type']=='DESCRIPTION': question_type = 'display'
        
        if question['answers'] != []:
            answer_data = []
            for answer in question['answers']:
                answer_data.append({'valueString': answer['text']})
        
        if question['answers'] != []:
            question_data = {
                'linkId': question['question_number'],
                'text': question['question_name'],
                'type': question_type,
                'answerOption': answer_data
            }
        else:
            question_data = {
                'linkId': question['question_number'],
                'text': question['question_name'],
                'type': question_type,
            }

        evidence_bundles_reformat = []
        nlpql_name = question['nlpql_grouping']
        try:
            if question['evidence_bundle'][nlpql_name] is not None:
                for name in question['evidence_bundle'][nlpql_name]:
                    new_name = '.'.join([nlpql_name, name])
                    evidence_bundles_reformat.append(new_name)

                evidence_bundle_ext = [{
                        "url": "evidenceBundles",
                        "extension": list(map(lambda x: {"url": "evidence-bundle", "valueString": x}, evidence_bundles_reformat))
                }]

                question_data['extension'] = evidence_bundle_ext
        except KeyError:
            pass

        try:
            quest.item[groupNumber]['item'].append(question_data)
        except KeyError:
            quest.item[groupNumber]['item'] = []
            quest.item[groupNumber]['item'].append(question_data)
    
    return quest.dict()

def bundle_forms(forms: list):
    bundle = bundle_template
    bundle['entry'] = []
    for form in forms:
        bundle["entry"].append({"fullUrl": "Questionnaire/" + form["id"], "resource": form})
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    bundle["meta"]["lastUpdated"] = timestamp
    return bundle

def run_cql(cql_posts: list):
    session = FuturesSession()
    url = 'https://apps.hdap.gatech.edu/cql/evaluate'
    headers = {'Content-Type': 'application/json'}
    futures = []
    for cql_post in cql_posts:
        futures.append(session.post(url, json=cql_post, headers=headers))
        print(f'Started running job')
    return futures

def get_cql_results(futures: list, libraries: list, patientId: str):
    results = []
    for i, future in enumerate(futures):
        result = future.result().json()
        full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
        results.append(full_result)
    return results

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
            library_task = question['extension'][0]['valueString']
            library, task = library_task.split('.')

            target_library = None
            for library_result in results:
                if library_result['libraryName'] == library:
                    target_library = library_result
                    break
            if target_library is None:
                raise HTTPException(404, 'Library specified in form extension was not found in these CQL results, please run the appropriate CQL libraries needed for this form')

            target_result = None
            for result in target_library['results']:
                if result['name'] == task:
                    target_result = result['result']
                    break
            if target_result is None:
                raise HTTPException(404, f'CQL result {task} not found in library {library}')

            body = {
                'answer': {'type': question['type'] },
                'cqlResults': target_result
            }
            linked_results[linkId] = body
   
    return linked_results

client = pymongo.MongoClient("mongodb+srv://formsapiuser:i3lworks@forms.18m6i.mongodb.net/Forms?retryWrites=true&w=majority")
db = client.SmartChartForms


@app.get("/")
async def root():
    return "Please use one of the endpoints, this is just the root of the api"

@app.get("/forms", response_model=Union[list, dict])
async def get_list_of_forms(returnBundle: bool = False):
    # Pull list of forms from the database
    form_list = []
    all_forms = db.forms.find()

    if returnBundle is not True:
        for document in all_forms:
            form_meta = {"id": document["id"],
                        "name": document["name"],
                        "description": document["description"]}
            form_list.append(form_meta)
        return form_list
    else:
        for form in all_forms:
            del form["_id"]
            form_list.append(form)
        return bundle_forms(form_list)

@app.get("/forms/cql")
async def get_cql_libraries():
    form_list = []
    all_forms = db.cql.find()
    for document in all_forms:
        form_meta = {"name": document["name"],
                    "version": document["version"]}
        form_list.append(form_meta)
    return form_list

@app.get("/forms/cql/{libraryName}")
async def get_cql(libraryName: str):
    cql_library = db.cql.find_one({"name": libraryName})
    if cql_library is None:
        raise HTTPException(status_code=404, detail='CQL Library not found')
    base64_cql = cql_library['content'][0]['data']
    cql_bytes = base64.b64decode(base64_cql)
    decoded_cql = cql_bytes.decode('ascii')
    return decoded_cql

@app.get("/forms/{form_id}", response_model=Union[dict, str])
async def get_form(form_id: str):
    result_form = db.forms.find_one({'id': form_id})
    if result_form is None:
        raise HTTPException(404, 'Form with that ID not found in the database')
    else : 
        del result_form["_id"]
        return result_form

@app.post("/forms")
async def create_form(questions: Questionnaire):
    duplicate = db.forms.find_one({"id": questions.id})
    if duplicate is not None:
        return f"This Questionnaire already exists in the database. To update, use PUT at /forms/{questions.id}. If you would like to create a new version of the form, change the id of the Questionnaire resource and try POSTing again."
    else:
        result = db.forms.insert_one(questions.dict())
    if result.acknowledged:
        return f"You have created a Questionnaire with an id of {questions.id} in the database"
    else: return 'Something went wrong!'

@app.post("/forms/start", response_model=Union[dict, str])
async def start_jobs(post_body: StartJobPostBody):
    #get cql library names to be run
    libraries = post_body.evidenceBundles
    #pull cql libraries from db
    cql_posts = []
    for library in libraries:
        cql_library = db.cql.find_one({'name': library})
        if cql_library is None:
            return f'Your evidence bundle {library} does not exist in the database. Please POST that to /forms/cql before trying to run the CQL.'
        base64_cql = cql_library['content'][0]['data']
        cql_bytes = base64.b64decode(base64_cql)
        decoded_cql = cql_bytes.decode('ascii')
        formatted_cql = str(decoded_cql.replace('"', '\"'))
        full_post_body = {
            "code": formatted_cql,
            "dataServiceUri":"https://apps.hdap.gatech.edu/omoponfhir3/fhir/",
            "dataUser":"client",
            "dataPass":"secret",
            "patientId": post_body.patientId,
            "terminologyServiceUri":"https://cts.nlm.nih.gov/fhir/",
            "terminologyUser":"jduke99",
            "terminologyPass":"v6R4*SsU39"
        }
        cql_posts.append(full_post_body)
        print(f'Retrieved library named {library}')
    
    futures = run_cql(cql_posts)
    print('Created futures array')
    results = get_cql_results(futures, libraries, post_body.patientId)
    print('Retrived all results from futures')
    linked_results = create_linked_results(results, post_body.formId, db)

    return linked_results

@app.post("/forms/nlpql")
async def save_nlpql(post_body: NLPQLDict):
    result = db.nlpql.insert_one(post_body.dict())
    return f'Saved NLPQL file named {post_body.name} in database'

@app.post("/forms/cql")
async def save_cql(code: str = Body(...)):
    # Get name and version of cql library
    split_cql = code.split()
    name = split_cql[1]
    version = split_cql[3].strip("'")

    # Encode CQL as base64Binary
    code_bytes = code.encode('ascii')
    base64_bytes = base64.b64encode(code_bytes)
    base64_cql = base64_bytes.decode('ascii')

    # Create Library object
    data = {
        'name': name,
        'version': version,
        'status': 'draft',
        'experimental': True,
        'type': {'coding':[{'code':'logic-library'}]},
        'content': [{
            'contentType': 'cql',
            'data': base64_cql
        }]
    }
    cql_library = Library(**data)

    # Store Library object in db
    result = db.cql.insert_one(cql_library.dict())
    if result.acknowledged:
        return f'Saved CQL Library resource named {name} in database'
    else:
        return f'Something went wrong!'

@app.put("/forms/{form_id}")
async def update_form(form_id: str, new_questions: Questionnaire):
    result = db.forms.replace_one({"id": form_id}, new_questions.dict())
    print(result)
    if result.modified_count != 0:
        return "You have updated a Questionnaire with an id of {}".format(form_id)
    else:
        return "There was no Questionnaire found with that id. Please first POST this Questionnaire to the database."

# uvicorn formsAPImodule:app --reload

# Old Start Jobs
async def start_jobs_old(post_body: StartJobPostBody):
    time.sleep(5)
    try:
        result = db.fakeReturn.find_one({'form_id': post_body.formId, 'evidence_bundle': post_body.evidenceBundle})
        del result["_id"]
    except:
        return f"No form with the id {post_body.formId} was found"
    
    #for testing purposes only, need to remove when actually being used and format correctly
    result = []
    if post_body.evidenceBundle == 'information':
        for i in range(1,10):
            result.append({'linkId': i, 'evidence_bundles': [f'eb{i*2}', f'eb{(i*2)+1}']})
    elif post_body.evidenceBundle == 'maternal_demographics':
        for i in range(10,22):
            result.append({'linkId': i, 'evidence_bundles': [f'eb{i*2}', f'eb{(i*2)+1}']})
    return result