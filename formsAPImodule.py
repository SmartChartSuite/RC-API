import requests
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Optional, List, Union
from fhir.resources.questionnaire import Questionnaire
from bson import ObjectId
import pymongo

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

client = pymongo.MongoClient("mongodb+srv://formsapiuser:454Ik0LIQuOuHSQz@forms.18m6i.mongodb.net/Forms?retryWrites=true&w=majority")
db = client.SmartChartForms

app = FastAPI()

@app.get("/")
async def root():
    return "Please use one of the endpoints, this is just the root of the api"

@app.get("/forms", response_model=list)
async def get_list_of_forms():
    # Pull list of forms from the database
    form_list = []
    all_forms = db.forms.find()
    for document in all_forms:
        form_meta = {"_id": str(document["_id"]),
                     "name": document["name"]}
        form_list.append(form_meta)
    return form_list

@app.get("/forms/{form_id}", response_model=Union[QuestionsJSON, str])
async def get_form(form_id: str, returnAsFHIR: bool = False):
    # return questions.json from database, if returnAsFHIR: convert questions.json to questionnaire and return that
    if returnAsFHIR:
        result_form = "Not implemented yet"
    else:
        result_form = db.forms.find({'_id': ObjectId(form_id)})[0]
    return result_form

@app.post("/forms")
async def create_form(questions: QuestionsJSON):
    result = db.forms.insert_one(questions.dict())
    return "You have posted a questions.json with a generated ID of {}".format(str(result.inserted_id))

@app.put("/forms/{form_id}")
async def update_form(form_id: str, new_questions: QuestionsJSON):
    result = db.forms.replace_one({"_id": ObjectId(form_id)}, new_questions.dict())
    return "You have updated a form with a form id of {}".format(form_id)


# uvicorn formsAPImodule:app --reload