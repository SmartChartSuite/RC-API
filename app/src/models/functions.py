from fhir.resources.operationoutcome import OperationOutcome #TODO: replace to using fhirclient package as well as below imports
from fhir.resources.questionnaire import Questionnaire
from fhir.resources.observation import Observation
from requests_futures.sessions import FuturesSession
from datetime import datetime

from ..models.models import QuestionsJSON, bundle_template, CustomFormatter

from ..util.settings import cqfr4_fhir, nlpaas_url, log_level

import logging
import uuid
import requests
import base64


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

if log_level == "DEBUG":
    logger.setLevel(logging.DEBUG)
    ch.setLevel(logging.DEBUG)

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

def get_form(form_name: str):

    # Return Questionnaire from CQF Ruler based on form name
    r = requests.get(cqfr4_fhir+f'Questionnaire?name={form_name}')
    if r.status_code != 200:
        logger.error(f'Getting Questionnaire from server failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Getting Questionnaire from server failed with code {r.status_code}')

    search_bundle = r.json()
    try:
        questionnaire = search_bundle['entry'][0]['resource']
        logger.info(f'Found Questionnaire with name {form_name}')
        return questionnaire
    except KeyError:
        logger.error('Questionnaire with that name not found')
        return make_operation_outcome('not-found', f'Questionnaire named {form_name} not found on the FHIR server.')

def run_cql(library_ids: list, parameters_post: dict):

    # Create an asynchrounous HTTP Request session
    session = FuturesSession()
    futures = []
    for library_id in library_ids:
        url = cqfr4_fhir+f'Library/{library_id}/$evaluate'
        future = session.post(url, json=parameters_post)
        futures.append(future)
    return futures

def run_nlpql(library_ids: list, patient_id: str, external_fhir_server_url: str, external_fhir_server_auth: str):
    # Create an asynchrounous HTTP Request session
    session = FuturesSession()
    futures = []
    external_fhir_server_auth_split = external_fhir_server_auth.split(' ')
    nlpql_post_body = {
        "patient_id": patient_id,
        "fhir": {
            "serviceUrl": external_fhir_server_url,
            "auth": {
                "type": external_fhir_server_auth_split[0],
                "token": external_fhir_server_auth_split[1]
            }
        }
    }
    for library_id in library_ids:
        # Get text NLPQL from the Library in CQF Ruler
        r = requests.get(cqfr4_fhir+f'Library/{library_id}')

        library_resource = r.json()
        logger.info(f'Submitting Library {library_resource["name"]}')
        base64_nlpql = library_resource['content'][0]['data']
        nlpql_bytes = base64.b64decode(base64_nlpql)
        nlpql_plain_text = nlpql_bytes.decode('utf-8')

        # Register NLPQL in NLPAAS
        r = requests.post(nlpaas_url+'job/register_nlpql', data=nlpql_plain_text)
        if r.status_code != 200:
            logger.error(f'Trying to register NLPQL with NLPAAS failed with status code {r.status_code}')
            logger.error(r.text)
            return make_operation_outcome('transient', f'Trying to register NLPQL with NLPAAS failed with code {r.status_code}')
        result = r.json()['message']
        job_url = result.split("'")[1][1:]
        if len(job_url) == 1:
            return make_operation_outcome('invalid', job_url)
        # Start running jobs
        future = session.post(nlpaas_url+job_url, json=nlpql_post_body)
        futures.append(future)
    return futures

def get_results(futures: list, libraries: list, patientId: str, flags: list):

    results_cql = []
    results_nlpql = []
    # Get JSON result from the given future object, will wait until request is done to grab result (would be a blocker when passed multiple futures and one result isnt done)
    if flags[0] and flags[1]:
        for i, future in enumerate(futures[0]):
            pre_result = future.result()
            if pre_result.status_code == 504:
                return 'Upstream request timeout'
            if pre_result.status_code == 408:
                return 'stream timeout'
            result = pre_result.json()

            # Formats result into format for further processing and linking
            full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
            logger.info(f'Got result for {libraries[i]}')
            results_cql.append(full_result)

        for i, future in enumerate(futures[1]):
            pre_result = future.result()
            if pre_result.status_code == 504:
                return 'Upstream request timeout'
            if pre_result.status_code == 408:
                return 'stream timeout'
            result = pre_result.json()

            # Formats result into format for further processing and linking
            full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
            logger.info(f'Got result for {libraries[i]}')
            results_nlpql.append(full_result)
    elif flags[0] and not flags[1]:
        for i, future in enumerate(futures[0]):
            pre_result = future.result()
            if pre_result.status_code == 504:
                return 'Upstream request timeout'
            if pre_result.status_code == 408:
                return 'stream timeout'
            result = pre_result.json()

            # Formats result into format for further processing and linking
            full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
            logger.info(f'Got result for {libraries[i]}')
            results_cql.append(full_result)
    elif not flags[0] and flags[1]:
        for i, future in enumerate(futures[1]):
            pre_result = future.result()
            if pre_result.status_code == 504:
                return 'Upstream request timeout'
            if pre_result.status_code == 408:
                return 'stream timeout'
            result = pre_result.json()

            # Formats result into format for further processing and linking
            full_result = {'libraryName': libraries[i], 'patientId': patientId, 'results': result}
            logger.info(f'Got result for {libraries[i]}')
            results_nlpql.append(full_result)
            
    return results_cql, results_nlpql

def flatten_results(results):
    flat_results = {}
    keys_to_delete = []
    for i, result in enumerate(results):
        if result['results'] == []:
            keys_to_delete.append(i)
            continue
        library_name = result['libraryName']
        try:
            # This is trying to see if its a CQL result versus NLPAAS
            for resource_full in result['results']['entry']:
                job_name = resource_full['fullUrl']
                value_list = [item for item in resource_full['resource']['parameter'] if item.get('name')=='value']
                value_dict = value_list[0]
                value_value_list = list(value_dict.values())
                value = value_value_list[1]
                flat_results[job_name]=value
        except TypeError:
            # This goes through the NLPAAS outputs and "sorts" the result objects based on the nlpql_feature and adds to the flat results dictionary with a key of the
            # feature name and a value of the list of results that have that feature name
            job_names = []
            for d in result['results']:
                job_names.append(d['nlpql_feature'])
            job_names = list(set(job_names))
            for job_name in job_names:
                temp_list = []
                for result_obj in result['results']:
                    if result_obj['nlpql_feature'] == job_name:
                        temp_list.append(result_obj)
                flat_results[job_name] = temp_list

    return flat_results

def check_results(results):
    for result in results:
        logger.debug(result)
        try:
            # This checks if the result is from NLPAAS and skips the CQL checking that comes next
            job_id = result['results'][0]['_id']
            continue
        except KeyError:
            pass
        except IndexError:
            continue
        try:
            full_list = result['results']['entry']
        except KeyError:
            issue = result['results']['issue']
            return make_operation_outcome(issue[0]['code'], issue[0]['diagnostics'])
        except IndexError:
            pass
    return None

def create_linked_results(results: list, form_name: str):

    # Get form (using get_form from this API)
    form = get_form(form_name)

    bundle_entries = []
    logger.debug(results)
    result_length = len(results)
    if result_length == 1:
        result = results[0]
        target_library = result['libraryName']

    logger.debug(results)
    results = flatten_results(results)
    logger.info('"Flattened" Results into the dictionary')
    logger.debug(results)
    try:
        patient_resource = results['Patient']
        patient_resource_id = results['Patient']['id']
        patient_bundle_entry = {
            "fullUrl": f'Patient/{patient_resource_id}',
            "resource": patient_resource
        }
        bundle_entries.append(patient_bundle_entry)
    except KeyError:
        logger.error('Patient resource not found in results, results from CQF Ruler are logged below')
        logger.error(results)
        return make_operation_outcome('not-found', 'Patient resource not found in results from CQF Ruler, see logs for more details')

    # For each group of questions in the form
    for group in form['item']:

        # For each question in the group in the form
        for question in group['item']:

            linkId = question['linkId']
            logger.info(f'Working on question {linkId}')
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

            if result_length == 1:
                if library != target_library:
                    continue

            # Create answer observation for this question
            answer_obs_uuid = str(uuid.uuid4())
            answer_obs = {
                "resourceType": "Observation",
                "id": answer_obs_uuid,
                "status": "final",
                "category": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "survey",
                        "display": "Survey"
                    }]
                }],
                "code": {
                    "coding": [
                        {
                            "system": f"urn:gtri:heat:form:{form_name}",
                            "code": linkId
                        }
                    ]
                },
                "subject": {
                    "reference": f'Patient/{patient_resource_id}'
                },
                'focus': []
            }
            answer_obs = Observation(**answer_obs)

            # Find the result in the CQL library run that corresponds to what the question has defined in its cqlTask extension
            target_result = None
            single_return_value = None
            supporting_resources = None
            empty_single_return = False
            tuple_flag = False

            try:
                value_return = results[task]
            except KeyError:
                logger.error(f'The task {task} was not found in the library results')
                return make_operation_outcome('not-found', f'The task {task} was not found in the library results')
            try:
                if value_return['resourceType']=='Bundle':
                    supporting_resources = value_return['entry']
                    single_resource_flag = False
                    logger.info(f'Found task {task} and supporting resources')
                else:
                    resource_type = value_return['resourceType']
                    single_resource_flag = True
                    logger.info(f'Found task {task} result')
            except (KeyError, TypeError) as e:
                single_return_value = value_return
                logger.debug(f'Found single return value {single_return_value}')

            if single_return_value == '[]':
                empty_single_return = True
                logger.info('Empty single return')
            if type(single_return_value) == str and single_return_value[0:6]=='[Tuple':
                tuple_flag=True
                logger.info('Found Tuple in results')
            if supporting_resources is not None:
                for resource in supporting_resources:
                    try:
                        focus_object = {'reference': resource['fullUrl']}
                        answer_obs.focus.append(focus_object)
                    except KeyError:
                        pass
            if empty_single_return:
                continue

            answer_obs = answer_obs.dict()
            if answer_obs['focus'] == []:
                logger.debug('Answer Observation does not have a focus, deleting field')
                del answer_obs['focus']

            # If cardinality is a series, does the standard return body format
            if cardinality == 'series' and tuple_flag==False:
                # Construct final answer object bundle before result bundle insertion
                answer_obs_bundle_item = {
                    'fullUrl' : 'Observation/'+answer_obs_uuid,
                    'resource': answer_obs
                }

            # If cardinality is a single, does a modified return body to have the value in multiple places
            else:
                single_answer = single_return_value
                logger.debug(single_answer)
                if single_answer == None:
                    continue

                #value_key = 'value'+single_return_type
                if tuple_flag==False:
                    answer_obs['valueString'] = single_answer
                    answer_obs_bundle_item = {
                        'fullUrl' : 'Observation/'+answer_obs_uuid,
                        'resource': answer_obs
                    }
                elif tuple_flag==True:
                    tuple_string = single_answer.strip('[]')
                    tuple_string = tuple_string.split('Tuple ')
                    tuple_string.remove('')
                    tuple_dict_list = []
                    for item in tuple_string:
                        new_item = item.strip(', ')
                        new_item = new_item.replace('\n', '').strip('{ }').replace('"', '')
                        new_item_list = new_item.split('\t')
                        new_item_list.remove('')
                        test_dict = {}
                        for new_item in new_item_list:
                            key, value = new_item.split(': ')
                            test_dict[key] = value
                        tuple_dict_list.append(test_dict)
                    logger.debug(tuple_dict_list)
                    tuple_observations = []
                    for answer_tuple in tuple_dict_list:
                        answer_value_split = answer_tuple['answerValue'].split('^')
                        logger.info(answer_value_split)
                        supporting_resource_type_map = {'dosage': 'MedicationStatement', 'value': 'Observation', 'onset': 'Condition'}
                        try:
                            supporting_resource_type = supporting_resource_type_map[answer_tuple['fhirField']]
                        except KeyError:
                            return make_operation_outcome('not-found', f'The fhirField thats being returned in the CQL is not the the supporting resource type, this needs to be updated as more resources are added')
                        value_type = answer_tuple['valueType']
                        temp_uuid = str(uuid.uuid4())
                        temp_answer_obs = {
                            "resourceType": "Observation",
                            "id": temp_uuid,
                            "status": "final",
                            "category": [{
                                "coding": [{
                                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                    "code": "survey",
                                    "display": "Survey"
                                }]
                            }],
                            "code": {
                                "coding": [
                                    {
                                        "system": f"urn:gtri:heat:form:{form_name}",
                                        "code": linkId
                                    }
                                ]
                            },
                            "subject": {
                                "reference": f'Patient/{patient_resource_id}'
                            },
                            "focus": [{
                                "reference": supporting_resource_type +'/'+answer_tuple['fhirResourceId'].split('/')[-1]
                            }],
                            "note": [{
                                "text": answer_tuple['sourceNote']
                            }],
                            f'value{value_type}': answer_tuple['answerValue']
                        }
                        temp_answer_obs_entry = {
                            "fullUrl": f'Observation/{temp_uuid}',
                            "resource": temp_answer_obs
                        }
                        tuple_observations.append(temp_answer_obs_entry)

                        # Create focus reference from data
                        if supporting_resource_type == 'MedicationStatement':
                            supporting_resource = {
                                "resourceType": "MedicationStatement",
                                "id": answer_tuple['fhirResourceId'].split('/')[-1],
                                "identifier": [{
                                    "system": "https://gt-apps.hdap.gatech.edu/rc-api",
                                    "value": "MedicationStatement/"+answer_tuple['fhirResourceId'].split('/')[-1],
                                }],
                                "status": "active",
                                "medicationCodeableConcept": {
                                    "coding": [{
                                        "system": answer_value_split[1],
                                        "code": answer_value_split[2],
                                        "display": answer_value_split[3],
                                    }]
                                },
                                "effectiveDateTime": answer_value_split[0],
                                "subject": {
                                    "reference": f'Patient/{patient_resource_id}'
                                },
                                "dosage": [{
                                    "doseAndRate":[{
                                        "doseQuantity": {
                                            "value": answer_value_split[4],
                                            "unit": answer_value_split[5]
                                        }
                                    }]
                                }]
                            }
                            supporting_resource_bundle_entry = {
                                "fullUrl": 'MedicationStatement/'+supporting_resource["id"],
                                "resource": supporting_resource
                            }
                        elif supporting_resource_type == 'Observation':
                            supporting_resource = {
                                "resourceType": "Observation",
                                "id": answer_tuple['fhirResourceId'].split('/')[-1],
                                "identifier": [{
                                    "system": "https://gt-apps.hdap.gatech.edu/rc-api",
                                    "value": "Observation/"+answer_tuple['fhirResourceId'].split('/')[-1],
                                }],
                                "status": "final",
                                "code":{
                                    "coding": [{
                                        "system": answer_value_split[1],
                                        "code": answer_value_split[2],
                                        "display": answer_value_split[3],
                                    }]
                                },
                                "effectiveDateTime": answer_value_split[0],
                                "subject": {
                                    "reference": f'Patient/{patient_resource_id}'
                                },
                                "valueString": ' '.join(answer_value_split[4:])
                            }
                            supporting_resource_bundle_entry = {
                                "fullUrl": 'Observation/'+supporting_resource["id"],
                                "resource": supporting_resource
                            }
                        elif supporting_resource_type == 'Condition':
                            supporting_resource = {
                                "resourceType": "Condition",
                                "id": answer_tuple['fhirResourceId'].split('/')[-1],
                                "identifier": [{
                                    "system": "https://gt-apps.hdap.gatech.edu/rc-api",
                                    "value": "Observation/"+answer_tuple['fhirResourceId'].split('/')[-1],
                                }],
                                "code":{
                                    "coding": [{
                                        "system": answer_value_split[1],
                                        "code": answer_value_split[2],
                                        "display": answer_value_split[3],
                                    }]
                                },
                                "onsetDateTime": answer_value_split[0],
                                "subject": {
                                    "reference": f'Patient/{patient_resource_id}'
                                }
                            }
                            supporting_resource_bundle_entry = {
                                "fullUrl": 'Condition/'+supporting_resource["id"],
                                "resource": supporting_resource
                            }
                        tuple_observations.append(supporting_resource_bundle_entry)

            try:
                focus_test = answer_obs_bundle_item['resource']['focus']
            except KeyError:
                try:
                    value_test = answer_obs_bundle_item['resource']['valueString']
                except KeyError:
                    continue
            #Add items to return bundle entry list
            if tuple_flag == False:
                bundle_entries.append(answer_obs_bundle_item)
            else:
                bundle_entries.extend(tuple_observations)
            if supporting_resources is not None:
                bundle_entries.extend(supporting_resources)

    return_bundle_id = str(uuid.uuid4())
    return_bundle = {
        'resourceType': 'Bundle',
        'id': return_bundle_id,
        'type': 'collection',
        'entry': bundle_entries
    }

    delete_list = []
    for i, entry in enumerate(return_bundle['entry']):
        try:
            if entry['valueString'] == None:
                delete_list.append(i)
        except KeyError:
            pass

    for index in sorted(delete_list, reverse=True):
        del return_bundle['entry'][index]

    return return_bundle

def validate_cql(code: str):
    escaped_string_code = code.replace('"', '\"')
    cql_operation_data = {
        "resourceType": "Parameters",
        "parameter": [
            {
            "name": "patientId",
            "valueString": "1"
           },
           {
            "name": "context",
            "valueString": "Patient"
           },
           {
            "name": "code",
            "valueString": escaped_string_code
           }
       ]
    }
    r = requests.post(cqfr4_fhir+'$cql', json=cql_operation_data)
    if r.status_code != 200:
        logger.error(f'Trying to validate the CQL before creating library failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Trying to validate the CQL before creating library failed with status code {r.status_code}')
    validation_results = r.json()
    first_fullUrl = validation_results['entry'][0]['fullUrl']
    if first_fullUrl == 'Error':
        logger.error('There were errors in CQL validation. Compiling errors into an OperationOutcome')
        num_errors = len(validation_results['entry'])
        diagnostics_list = []
        combined_oo = {
            'resourceType': 'OperationOutcome',
            'issue': []
        }
        for i in range(0, num_errors):
            diagnostics = ': '.join([item['name']+' '+item['valueString'] for item in validation_results['entry'][i]['resource']['parameter']])
            diagnostics_list.append(diagnostics)
        for diagnostic in diagnostics_list:
            oo_item = {
                'severity': 'error',
                'code': 'invalid',
                'diagnostics': diagnostic,
            }
            combined_oo['issue'].append(oo_item)
        logger.error(f'There were a total of {num_errors} errors. The OperationOutcome will be returned to the client as well as logged below.')
        return combined_oo
    else:
        logger.info('CQL successfully validated!')
        return True

def validate_nlpql(code: str):
    code = code.encode(encoding='utf-8')
    r = requests.post(nlpaas_url+'job/validate_nlpql', data = code)
    if r.status_code != 200:
        logger.error(f'Trying to validate NLPQL against NLPAAS failed with status code {r.status_code}')
        return make_operation_outcome('transient', f'Trying to validate NLPQL against NLPAAS failed with status code {r.status_code}')
    validation_results = r.json()
    try:
        valid = validation_results['valid']
    except KeyError:
        logger.error('valid key not found in NLPAAS validation results, see NLPAAS response below to investigate any errors.')
        logger.error(validation_results)
        return make_operation_outcome('transient', 'Valid key not found in the NLPAAS validation results, see logs for full dump of NLPAAS validation response.')
    if valid:
        return True
    else:
        try:
            return make_operation_outcome('invalid', validation_results['reason'])
        except KeyError:
            logger.error('NLPQL validation did not succeed but there was no reason given in the response. See dump below of NLPAAS response.')
            logger.error(validation_results)
            return make_operation_outcome('invalid', 'Validation results were invalid but the reason was not given, see logs for full dump of NLPAAS response.')