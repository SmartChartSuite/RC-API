from datetime import datetime
from fastapi.exceptions import HTTPException
from fhir.resources.operationoutcome import OperationOutcome
from pydantic import BaseModel, Field
from typing import Dict, Optional, List, Union
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.library import Library
from bson import ObjectId
from requests.exceptions import HTTPError
from requests_futures.sessions import FuturesSession
from fastapi.middleware.cors import CORSMiddleware
from uuid import UUID, uuid4
from ..util.settings import cqfr4_fhir
import logging

class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;21m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = '%(asctime)s %(levelname)s - %(message)s'

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: green + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, '%m/%d/%Y %I:%M:%S %p')
        return formatter.format(record)

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

class JobIDParameter(BaseModel):
    name: str = "jobId"
    valueString: UUID = Field(default_factory=uuid4)

class JobStatusParameter(BaseModel):
    name: str = "jobStatus"
    valueString: str = "inProgress"

class ResultParameter(BaseModel):
    name: str = "result"
    valueString: dict = {}

# TODO: 
class ParametersJob(BaseModel):
    resourceType: str = "Parameters"
    parameter: list = [JobIDParameter(), JobStatusParameter(), ResultParameter()]

bundle_template = {
    "resourceType": "Bundle",
    "meta": {
        "lastUpdated": ""
    },
    "type": "collection",
    "entry": []
}

def make_operation_outcome(code: str, diagnostics: str, severity = 'error'):
    oo_template = {
        'issue': [
            {
                'severity': severity,
                'code': code,
                'diagnostics': diagnostics,
            }
        ]
    }
    return OperationOutcome(**oo_template).dict()

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

def run_cql(library_ids: list, parameters_post: dict):

    # Create an asynchrounous HTTP Request session
    session = FuturesSession()
    futures = []
    for library_id in library_ids:
        url = cqfr4_fhir+f'Library/{library_id}/$evaluate'
        future = session.post(url, json=parameters_post)
        futures.append(future)
    return futures

def get_cql_results(futures: list, libraries: list, patientId: str):

    results = []
    # Get JSON result from the given future object, will wait until request is done to grab result (would be a blocker when passed multiple futures and one result isnt done)
    for i, future in enumerate(futures):
        pre_result = future.result()
        if pre_result.status_code == 504:
            return 'Upstream request timeout'
        if pre_result.status_code == 408:
            return 'stream timeout'
        result = pre_result.json()

        # Formats result into format for further processing and linking
        full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
        results.append(full_result)
    return results