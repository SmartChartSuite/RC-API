from fhir.resources.fhirtypes import String
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Optional, List, Union
from fhir.resources.questionnaire import Questionnaire
from bson import ObjectId
import pymongo
from fastapi.middleware.cors import CORSMiddleware
import time

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
    evidenceBundle: str
    patientId: str


class NLPQLDict(BaseModel):
    name: str
    content: str

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
            form_meta = {"_id": str(document["_id"]),
                        "name": document["name"],
                        "description": document["description"]}
            form_list.append(form_meta)
        return form_list
    else:
        forms_fhir = []
        for form in all_forms:
            forms_fhir.append(convertToQuestionnaire(form))
        return bundle_forms(forms_fhir)

@app.get("/forms/{form_id}", response_model=Union[QuestionsJSON, dict, str])
async def get_form(form_id: str, returnAsFHIR: bool = False, returnAsFhir: bool = False):
    # return questions.json from database, if returnAsFHIR: convert questions.json to questionnaire and return that
    if returnAsFHIR or returnAsFhir:
        result_form = convertToQuestionnaire(db.forms.find_one({'_id': ObjectId(form_id)}))
    else:
        try:
            result_form = db.forms.find_one({'_id': ObjectId(form_id)})
        except IndexError:
            return "No form with that id found"
    return result_form

@app.post("/forms")
async def create_form(questions: QuestionsJSON):
    result = db.forms.insert_one(questions.dict())
    return "You have posted a questions.json with a generated ID of {}".format(str(result.inserted_id))

@app.put("/forms/{form_id}")
async def update_form(form_id: str, new_questions: QuestionsJSON):
    result = db.forms.replace_one({"_id": ObjectId(form_id)}, new_questions.dict())
    return "You have updated a form with a form id of {}".format(form_id)

@app.post("/forms/start", response_model=Union[list, str])
async def start_jobs(post_body: StartJobPostBody):
    #time.sleep(5)
    try:
        result = db.fakeReturn.find_one({'form_id': post_body.formId, 'evidence_bundle': post_body.evidenceBundle})
        del result["_id"]
    except IndexError:
        return f"No job with the name {post_body.nlpqlGrouping} for the form {post_body.formId} was found"
    
    #for testing purposes only, need to remove when actually being used and format correctly
    result = []
    for i in range(1,21):
        result.append({'linkId': i, 'evidence_bundles': [f'eb{i*2}', f'eb{(i*2)+1}']})
    return result

@app.post("/forms/nlpql")
async def save_nlpql(post_body: NLPQLDict):
    result = db.nlpql.insert_one(post_body.dict())
    return f'Saved NLPQL file named {post_body.name} in database'
# uvicorn formsAPImodule:app --reload