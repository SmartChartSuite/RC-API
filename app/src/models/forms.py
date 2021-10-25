from datetime import datetime
from pydantic import BaseModel
from typing import Dict, Optional, List, Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from bson import ObjectId
from requests_futures.sessions import FuturesSession
from fastapi.middleware.cors import CORSMiddleware


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

    # Create an asynchrounous HTTP Request session to do multiple requests at the same time
    session = FuturesSession()
    url = 'https://apps.hdap.gatech.edu/cql/evaluate'
    headers = {'Content-Type': 'application/json'}
    futures = []
    for i, cql_post in enumerate(cql_posts):
        # Get future object that represents the response when its finished but isnt a blocker
        futures.append(session.post(url, json=cql_post, headers=headers))
        print(f'Started running job')
    return futures

def get_cql_results(futures: list, libraries: list, patientId: str):
    results = []
    for i, future in enumerate(futures):
        # Get JSON result from the given future object, will wait until request is done to grab result (would be a blocker when passed multiple futures and one result isnt done)
        result = future.result().json()
        print(f'Got result for library {libraries[i]}')

        # Formats result into format for further processing and linking
        full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
        results.append(full_result)
    return results