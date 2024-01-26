"""Functions module for helper functions being called by other files"""

import base64
import logging
import uuid
from concurrent.futures import Future
from datetime import datetime

import requests
from fhir.resources.documentreference import DocumentReference
from fhir.resources.fhirtypes import Id
from fhir.resources.observation import Observation
from fhir.resources.operationoutcome import OperationOutcome
from requests_futures.sessions import FuturesSession

from ..util.settings import cqfr4_fhir, deploy_url, external_fhir_server_auth, external_fhir_server_url, nlpaas_url

logger: logging.Logger = logging.getLogger("rcapi.models.functions")


def make_operation_outcome(code: str, diagnostics: str, severity: str = "error"):
    """Returns an OperationOutcome for a given code, diagnostics string, and a severity (Default of error)"""
    oo_template = {
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ]
    }
    return OperationOutcome(**oo_template).dict()  # type: ignore


def get_form(form_name: str, form_version: str | None = None):
    """Returns the Questionnaire from CQF Ruler based on form name"""

    if form_version:
        req = requests.get(cqfr4_fhir + f"Questionnaire?name:exact={form_name}&version={form_version}")
        if req.status_code != 200:
            logger.error(f"Getting Questionnaire from server failed with status code {req.status_code}")
            return make_operation_outcome("transient", f"Getting Questionnaire from server failed with code {req.status_code}")
    else:
        logger.info(f"No form version given, will be using most recently updated Questionnaire with name {form_name}")
        req = requests.get(cqfr4_fhir + f"Questionnaire?name:exact={form_name}&_sort=-_lastUpdated")
        if req.status_code != 200:
            logger.error(f"Getting Questionnaire from server failed with status code {req.status_code}")
            return make_operation_outcome("transient", f"Getting Questionnaire from server failed with code {req.status_code}")

    search_bundle = req.json()
    try:
        questionnaire = search_bundle["entry"][0]["resource"]
        logger.info(f"Found Questionnaire with name {form_name}, version {questionnaire['version']}, and form server ID {questionnaire['id']}")
        return questionnaire
    except KeyError:
        logger.error(f"Questionnaire with name {form_name} and version {form_version} not found") if form_version else logger.error(f"Questionnaire with name {form_name} not found")
        return make_operation_outcome("not-found", f"Questionnaire with name {form_name} and version {form_version} not found") if form_version else make_operation_outcome("not-found", f"Questionnaire with name {form_name} not found on the FHIR server.")


def run_cql(library_ids: list, parameters_post: dict):
    """Create an asynchrounous HTTP Request session for evaluting CQL Libraries"""

    session = FuturesSession()
    futures: list[Future] = []
    for library_id in library_ids:
        url = cqfr4_fhir + f"Library/{library_id}/$evaluate"
        future: Future = session.post(url, json=parameters_post)  # type: ignore
        futures.append(future)
    return futures


def run_nlpql(library_ids: list, patient_id: str, external_fhir_server_url_string: str, external_fhir_server_auth: str):
    """Create an asynchrounous HTTP Request session for evaluting NLPQL Libraries"""

    session = FuturesSession()
    futures: list[Future] = []

    nlpql_post_body = {"patient_id": patient_id, "fhir": {"service_url": external_fhir_server_url_string}}

    if external_fhir_server_auth:
        external_fhir_server_auth_split = external_fhir_server_auth.split(" ")
        nlpql_post_body["fhir"]["auth"] = {"auth_type": external_fhir_server_auth_split[0], "token": external_fhir_server_auth_split[1]}

    for library_id in library_ids:
        # Get text NLPQL from the Library in CQF Ruler
        req = requests.get(cqfr4_fhir + f"Library/{library_id}")

        library_resource = req.json()
        base64_nlpql = library_resource["content"][0]["data"]
        nlpql_bytes = base64.b64decode(base64_nlpql)
        nlpql_plain_text = nlpql_bytes.decode("utf-8")

        # Register NLPQL in NLPAAS
        try:
            req = requests.post(nlpaas_url + "job/register_nlpql", data=nlpql_plain_text, headers={"Content-Type": "text/plain"})
        except requests.exceptions.ConnectionError as error:
            logger.error(f"Trying to connect to NLPaaS failed with ConnectionError {error}")
            return make_operation_outcome("transient", "There was an issue connecting to NLPaaS, see the logs for the full HTTPS error. Most often, this means that the DNS name cannot be resolved.")
        if req.status_code not in [200, 201]:
            logger.error(f"Trying to register NLPQL with NLPaaS failed with status code {req.status_code}")
            logger.error(req.text)
            return make_operation_outcome("transient", f"Trying to register NLPQL with NLPaaS failed with code {req.status_code}")
        result = req.json()
        job_url = result["location"]
        if job_url[0] == "/":
            job_url = job_url[1:]

        # Start running jobs
        future: Future = session.post(nlpaas_url + job_url, json=nlpql_post_body)  # type: ignore
        futures.append(future)
    return futures


def handle_cql_futures(cql_futures: list[Future], library_names: list[str], patient_id: str) -> list[dict]:
    results_cql: list[dict] = []
    for i, future in enumerate(cql_futures):
        pre_result: requests.Response = future.result()
        if pre_result.status_code == 504:
            logger.error(f"There was an upstream request timeout for library {library_names[i]}.cql with status_code 504")
            result_cql_tmp: dict = {}
        elif pre_result.status_code == 408:
            logger.error(f"There was a stream request timeout for library {library_names[i]}.cql with status code 408")
            result_cql_tmp = {}
        else:
            result_cql_tmp = pre_result.json()

        # Handles if theres an OperationOutcome and logs it, but moves on
        if result_cql_tmp["resourceType"] == "OperationOutcome":
            logger.error("There were errors in the CQL, see OperationOutcome below")
            logger.error(result_cql_tmp)
            result_cql_tmp = {}

        # Formats result into format for further processing and linking
        full_result = {"libraryName": library_names[i], "patientId": patient_id, "results": result_cql_tmp}
        logger.info(f"Got result for {library_names[i]}.cql")
        results_cql.append(full_result)

    return results_cql


def handle_nlpql_futures(nlpql_futures: list[Future], library_names: list[str], patient_id: str) -> list[dict]:
    results_nlpql: list[dict] = []
    for i, future in enumerate(nlpql_futures):
        pre_result: requests.Response = future.result()
        if pre_result.status_code == 504:
            logger.error(f"There was an upstream request timeout for library {library_names[i]}.nlpql with status_code 504")
            result_nlpql_tmp: dict = {}
        elif pre_result.status_code == 408:
            logger.error(f"There was an stream request timeout for library {library_names[i]}.nlpql with status_code 408")
            result_nlpql_tmp = {}
        elif pre_result.status_code in [200, 201]:
            result_nlpql_tmp = pre_result.json()
        else:
            logger.error(f"There was an error for library {library_names[i]}.nlpql with status_code {pre_result.status_code}")
            result_nlpql_tmp = {}

        # Formats result into format for further processing and linking
        full_result = {"libraryName": library_names[i], "patientId": patient_id, "results": result_nlpql_tmp}
        logger.info(f"Got result for {library_names[i]}.nlpql")
        results_nlpql.append(full_result)

    return results_nlpql


def get_results(futures: list[list[Future]], libraries: list[list[str]], patient_id: str, flags: list) -> tuple[list[dict], list[dict]]:
    """Get results from an async Futures Session"""
    results_cql: list[dict] = []
    results_nlpql: list[dict] = []

    # Get JSON result from the given future object, will wait until request is done to grab result (would be a blocker when passed multiple futures and one result isnt done)
    if flags[0] and flags[1] and nlpaas_url != "False":
        logger.debug("CQL and NLPQL Flag and NLPaaS URL is set")
        results_cql = handle_cql_futures(cql_futures=futures[0], library_names=libraries[0], patient_id=patient_id)
        results_nlpql = handle_nlpql_futures(nlpql_futures=futures[1], library_names=libraries[1], patient_id=patient_id)
    elif flags[0]:
        logger.debug("CQL Flag only")
        results_cql = handle_cql_futures(cql_futures=futures[0], library_names=(libraries[0] if isinstance(libraries[0], list) else libraries), patient_id=patient_id)  # type: ignore
    elif flags[1] and nlpaas_url != "False":
        logger.debug("NLPQL Flag and NLPaaS URL is not False")
        results_nlpql = handle_nlpql_futures(nlpql_futures=futures[0], library_names=libraries[0], patient_id=patient_id)

    return results_cql, results_nlpql


def flatten_results(results):
    """Converts results from CQF Ruler and NLPaaS to flat dictionaries for easier downstream processing"""
    flat_results = {}
    keys_to_delete = []
    for i, result in enumerate(results):
        if result["results"] == []:
            keys_to_delete.append(i)
            continue
        # library_name = result['libraryName']
        try:
            # This is trying to see if its a CQL result versus NLPAAS
            for resource_full in result["results"]["entry"]:
                job_name = resource_full["fullUrl"]
                value_list = [item for item in resource_full["resource"]["parameter"] if item.get("name") == "value"]
                value_dict = value_list[0]
                value_value_list = list(value_dict.values())
                try:
                    value = value_value_list[1]
                except IndexError:
                    value = "null"
                flat_results[job_name] = value
        except (TypeError, KeyError):
            # This goes through the NLPAAS outputs and "sorts" the result objects based on the nlpql_feature and adds to the flat results dictionary with a key of the
            # feature name and a value of the list of results that have that feature name
            job_names = []
            for result_dictionary in result["results"]:
                job_names.append(result_dictionary["nlpql_feature"])
            job_names = list(set(job_names))
            for job_name in job_names:
                temp_list = []
                for result_obj in result["results"]:
                    if result_obj["nlpql_feature"] == job_name:
                        temp_list.append(result_obj)
                flat_results[job_name] = temp_list

    return flat_results


def check_results(results: list[dict]):
    """Checks results for any errors returned from CQF Ruler or NLPaaS"""
    logger.info("Checking Results for Any Errors Returned by Services")
    for result in results:
        try:
            # This checks if the result is from NLPAAS and skips the CQL checking that comes next
            if "_id" in result["results"][0]:
                continue
            if "detail" in result["results"]:
                return make_operation_outcome("processing", result["results"]["detail"])
        except KeyError:
            pass
        except IndexError:
            continue
        try:
            try:
                if result["results"]["resourceType"] == "OperationOutcome":
                    issue = result["results"]["issue"]
                    return make_operation_outcome(issue[0]["code"], issue[0]["diagnostics"])
            except (KeyError, TypeError):
                pass
            if "entry" in result["results"]:
                pass
        except KeyError:
            issue = result["results"]["issue"]
            return make_operation_outcome(issue[0]["code"], issue[0]["diagnostics"])
        except IndexError:
            pass
    return None


def create_linked_results(results_in: list, form_name: str, patient_id: str):
    """Creates the registry bundle from CQL and NLPQL results"""

    # Get form (using get_form from this API)
    form = get_form(form_name)
    results_cql = results_in[0]
    results_nlpql = results_in[1]

    return_bundle_cql = {}
    return_bundle_nlpql = {}
    target_library: str = ""

    if results_cql:
        bundle_entries = []
        result_length = len(results_cql)
        if result_length == 1:
            result = results_cql[0]
            target_library = result["libraryName"]

        results = flatten_results(results_cql)
        logger.info("Flattened CQL Results into the dictionary")
        logger.debug(results)

        try:
            patient_resource = results["Patient"]
            patient_bundle_entry = {"fullUrl": f"Patient/{patient_id}", "resource": patient_resource}
            bundle_entries.append(patient_bundle_entry)
        except KeyError:
            logger.error("Patient resource not found in results, results from CQF Ruler are logged below")
            logger.error(results)
            return make_operation_outcome("not-found", "Patient resource not found in results from CQF Ruler, see logs for more details")

        # For each group of questions in the form
        for group in form["item"]:
            # For each question in the group in the form
            for question in group["item"]:
                link_id = question["linkId"]
                logger.info(f"Working on question {link_id}")
                library_task: str = ""
                cardinality: str = ""
                # If the question has these extensions, get their values, if not, keep going
                try:
                    for extension in question["extension"]:
                        if extension["url"] == "http://gtri.gatech.edu/fakeFormIg/cqlTask":
                            library_task = extension["valueString"]
                        if extension["url"] == "http://gtri.gatech.edu/fakeFormIg/cardinality":
                            cardinality = extension["valueString"]
                    library, task = library_task.split(".")
                    logger.debug(f"CQL Processing: Using library {library} and task {task} for this question")
                except (KeyError, ValueError):
                    logger.info("No CQL Task found for this question, moving onto next question")
                    continue

                if result_length == 1 and library != target_library:
                    continue

                # Create answer observation for this question
                answer_obs_uuid = str(uuid.uuid4())
                answer_obs = {
                    "resourceType": "Observation",
                    "id": answer_obs_uuid,
                    "identifier": [{"system": deploy_url, "value": f"Observation/{answer_obs_uuid}"}],
                    "status": "final",
                    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "survey", "display": "Survey"}]}],
                    "code": {"coding": [{"system": f"urn:gtri:heat:form:{form_name}", "code": link_id}]},
                    "effectiveDateTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "focus": [],
                }
                answer_obs = Observation(**answer_obs)

                # Find the result in the CQL library run that corresponds to what the question has defined in its cqlTask extension
                # target_result = None
                single_return_value = None
                supporting_resources = None
                empty_single_return = False
                tuple_flag = False
                tuple_string = ""

                try:
                    value_return = results[task]
                except KeyError:
                    logger.error(
                        f"The task {task} was not found in the library {library}, please ensure your CQL or NLPQL is returning a result for this. Moving onto the next question to handle processsing timeouts."
                    )
                    continue
                try:
                    if value_return["resourceType"] == "Bundle":
                        supporting_resources = value_return["entry"]
                        # single_resource_flag = False
                        logger.info(f"Found task {task} and supporting resources")
                    else:
                        # resource_type = value_return['resourceType']
                        # single_resource_flag = True
                        logger.info(f"Found task {task} result")
                except (KeyError, TypeError):
                    single_return_value = value_return
                    logger.debug(f"Found single return value {single_return_value}")

                if single_return_value in ["[]", "null"]:
                    empty_single_return = True
                    logger.info("Empty single return")
                if not single_return_value:
                    empty_single_return = True
                if isinstance(single_return_value, str) and single_return_value[0:6] == "[Tuple":
                    tuple_flag = True
                    logger.info("Found Tuple in results")
                if supporting_resources is not None:
                    for resource in supporting_resources:
                        try:
                            focus_object = {"reference": resource["fullUrl"]}
                            answer_obs.focus.append(focus_object)  # type: ignore
                        except KeyError:
                            pass
                if empty_single_return and not supporting_resources:
                    continue

                answer_obs = answer_obs.dict()
                if isinstance(answer_obs["effectiveDateTime"], datetime):
                    answer_obs["effectiveDateTime"] = answer_obs["effectiveDateTime"].strftime("%Y-%m-%dT%H:%M:%SZ")
                try:
                    if answer_obs["focus"] == []:
                        logger.debug("Answer Observation does not have a focus, deleting field")
                        del answer_obs["focus"]
                except KeyError:
                    pass

                # If cardinality is a series, does the standard return body format
                if cardinality == "series" and not tuple_flag:
                    # Construct final answer object bundle before result bundle insertion
                    answer_obs_bundle_item = {"fullUrl": "Observation/" + answer_obs_uuid, "resource": answer_obs}

                # If cardinality is a single, does a modified return body to have the value in multiple places
                else:
                    single_answer = single_return_value
                    logger.debug(f"Single Answer: {single_answer}")

                    # value_key = 'value'+single_return_type
                    if tuple_flag is False:
                        answer_obs["valueString"] = single_answer
                        answer_obs_bundle_item = {"fullUrl": "Observation/" + answer_obs_uuid, "resource": answer_obs}
                    elif tuple_flag and isinstance(single_answer, str):
                        tuple_string = single_answer.strip("[]")
                        tuple_string = tuple_string.split("Tuple ")
                        tuple_string.remove("")
                        tuple_dict_list = []
                        for item in tuple_string:
                            new_item = item.strip(", ")
                            new_item = new_item.replace("\n", "").strip("{ }").replace('"', "")
                            new_item_list = new_item.split("\t")
                            new_item_list.remove("")
                            test_dict = {}
                            for new_item in new_item_list:
                                new_item_split = new_item.split(": ")
                                key = new_item_split[0]
                                value = ": ".join(new_item_split[1:])
                                test_dict[key] = value
                            tuple_dict_list.append(test_dict)
                        tuple_observations = []
                        for answer_tuple in tuple_dict_list:
                            answer_value_split = answer_tuple["answerValue"].split("^")
                            if answer_value_split[0] == "null":
                                logger.warning("Found a null in tuple results, please investigate for possible data error")
                                continue
                            logger.debug(f"Tuple found: {answer_value_split}")
                            if "." in answer_tuple["fhirField"]:
                                supporting_resource_type = answer_tuple["fhirField"].split(".")[0]
                            else:
                                supporting_resource_type_map = {"dosage": "MedicationStatement", "value": "Observation", "onset": "Condition", "code": "Observation", "Procedure.code": "Procedure"}
                                try:
                                    supporting_resource_type = supporting_resource_type_map[answer_tuple["fhirField"]]
                                except KeyError:
                                    return make_operation_outcome(
                                        "not-found",
                                        ("The fhirField thats being returned in the CQL is not a supported the supporting resource type, this needs to be updated as more resources are added"),
                                    )

                            value_type = answer_tuple["valueType"]
                            temp_uuid = str(uuid.uuid4())
                            supporting_resource_id = answer_tuple["fhirResourceId"].split("/")[-3] if "_history" in answer_tuple["fhirResourceId"] else answer_tuple["fhirResourceId"].split("/")[-1]

                            if len(answer_value_split) >= 3:
                                effective_datetime = answer_value_split[0]
                                if len(effective_datetime) == 19:  # case when UTC but no Z
                                    effective_datetime += "Z"
                            else:
                                effective_datetime = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                            temp_answer_obs = {
                                "resourceType": "Observation",
                                "id": temp_uuid,
                                "identifier": [{"system": deploy_url, "value": f"Observation/{answer_obs_uuid}"}],
                                "status": "final",
                                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "survey", "display": "Survey"}]}],
                                "code": {"coding": [{"system": f"urn:gtri:heat:form:{form_name}", "code": link_id}]},
                                "effectiveDateTime": effective_datetime,
                                "subject": {"reference": f"Patient/{patient_id}"},
                                "focus": [{"reference": supporting_resource_type + "/" + supporting_resource_id}],
                                "note": [{"text": answer_tuple["sourceNote"]}],
                                "valueString": answer_tuple["answerValue"],
                            }
                            temp_answer_obs_entry = {"fullUrl": f"Observation/{temp_uuid}", "resource": temp_answer_obs}
                            tuple_observations.append(temp_answer_obs_entry)

                            # Create focus reference from data
                            if supporting_resource_type == "MedicationStatement":
                                supporting_resource = {
                                    "resourceType": "MedicationStatement",
                                    "id": supporting_resource_id,
                                    "identifier": [
                                        {
                                            "system": deploy_url,
                                            "value": "MedicationStatement/" + supporting_resource_id,
                                        }
                                    ],
                                    "status": "active",
                                    "medicationCodeableConcept": {
                                        "coding": [
                                            {
                                                "system": answer_value_split[1],
                                                "code": answer_value_split[2],
                                                "display": answer_value_split[3],
                                            }
                                        ]
                                    },
                                    "effectiveDateTime": effective_datetime,
                                    "subject": {"reference": f"Patient/{patient_id}"},
                                    "dosage": [{"doseAndRate": [{"doseQuantity": {"value": answer_value_split[4], "unit": answer_value_split[5]}}]}],
                                }
                                # try:
                                #     if external_fhir_server_auth:
                                #         supporting_resource_req = requests.get(external_fhir_server_url+"MedicationStatement/"+supporting_resource_id,
                                #                                                          headers={'Authorization': external_fhir_server_auth})
                                #         supporting_resource = supporting_resource_req.json()
                                #     else:
                                #         supporting_resource_req  = requests.get(external_fhir_server_url+"MedicationStatement/"+supporting_resource_id)
                                #         supporting_resource = supporting_resource_req.json()
                                # except requests.exceptions.JSONDecodeError:
                                #     logger.debug(f'Trying to find supporting resource with id MedicationStatement/{supporting_resource_id} '
                                #                  f'failed with status code {supporting_resource_req.status_code}') #type: ignore
                                supporting_resource_bundle_entry = {"fullUrl": "MedicationStatement/" + supporting_resource["id"], "resource": supporting_resource}
                            elif supporting_resource_type == "MedicationRequest":
                                supporting_resource = {
                                    "resourceType": "MedicationRequest",
                                    "id": supporting_resource_id,
                                    "identifier": [
                                        {
                                            "system": deploy_url,
                                            "value": "MedicationRequest/" + supporting_resource_id,
                                        }
                                    ],
                                    "status": "active",
                                    "intent": "order",
                                    "medicationCodeableConcept": {
                                        "coding": [
                                            {
                                                "system": answer_value_split[1],
                                                "code": answer_value_split[2],
                                                "display": answer_value_split[3],
                                            }
                                        ]
                                    },
                                    "authoredOn": effective_datetime,
                                    "subject": {"reference": f"Patient/{patient_id}"},
                                    "dosageInstruction": [{"doseAndRate": [{"doseQuantity": {"value": answer_value_split[4], "unit": answer_value_split[5]}}]}],
                                }
                                # try:
                                #     if external_fhir_server_auth:
                                #         supporting_resource_req = requests.get(external_fhir_server_url+"MedicationRequest/"+supporting_resource_id,
                                #                                                          headers={'Authorization': external_fhir_server_auth})
                                #         supporting_resource = supporting_resource_req.json()
                                #     else:
                                #         supporting_resource_req  = requests.get(external_fhir_server_url+"MedicationRequest/"+supporting_resource_id)
                                #         supporting_resource = supporting_resource_req.json()
                                # except requests.exceptions.JSONDecodeError:
                                #     logger.debug(f'Trying to find supporting resource with id MedicationRequest/{supporting_resource_id} '
                                #                  f'failed with status code {supporting_resource_req.status_code}') #type: ignore
                                supporting_resource_bundle_entry = {"fullUrl": "MedicationRequest/" + supporting_resource["id"], "resource": supporting_resource}
                            elif supporting_resource_type == "Observation":
                                supporting_resource = {
                                    "resourceType": "Observation",
                                    "id": supporting_resource_id,
                                    "identifier": [
                                        {
                                            "system": deploy_url,
                                            "value": "Observation/" + supporting_resource_id,
                                        }
                                    ],
                                    "status": "final",
                                    "code": {
                                        "coding": [
                                            {
                                                "system": answer_value_split[1],
                                                "code": answer_value_split[2],
                                                "display": answer_value_split[3],
                                            }
                                        ]
                                    },
                                    "effectiveDateTime": effective_datetime,
                                    "subject": {"reference": f"Patient/{patient_id}"},
                                }

                                match value_type:
                                    case "Quantity":
                                        if len(answer_value_split) == 6:
                                            supporting_resource["valueQuantity"] = {"value": answer_value_split[4], "unit": answer_value_split[5]}
                                        else:
                                            supporting_resource["valueQuantity"] = {"value": answer_value_split[4]}
                                    case "String":
                                        supporting_resource["valueString"] = answer_value_split[4]
                                    case "Ratio":
                                        ratio_numerator, ratio_denominator = answer_value_split[4].split(":")
                                        supporting_resource["valueRatio"] = {"numerator": {"value": ratio_numerator}, "denominator": {"value": ratio_denominator}}
                                    case "CodeableConcept":
                                        supporting_resource["valueCodeableConcept"] = {"coding": [{"system": answer_value_split[4], "code": answer_value_split[5], "display": answer_value_split[6]}]}
                                    case "Integer":
                                        supporting_resource["valueInteger"] = int(answer_value_split[4])
                                    case _:
                                        supporting_resource["valueString"] = f"value[x] type of {value_type} not being handled in RC-API or CQL"

                                # try:
                                #     if external_fhir_server_auth:
                                #         supporting_resource_req = requests.get(external_fhir_server_url+"Observation/"+supporting_resource_id,
                                #                                                          headers={'Authorization': external_fhir_server_auth})
                                #         supporting_resource = supporting_resource_req.json()
                                #     else:
                                #         supporting_resource_req  = requests.get(external_fhir_server_url+"Observation/"+supporting_resource_id)
                                #         supporting_resource = supporting_resource_req.json()
                                # except requests.exceptions.JSONDecodeError:
                                #     logger.debug(f'Trying to find supporting resource with id Observation/{supporting_resource_id} '
                                #                  f'failed with status code {supporting_resource_req.status_code}') #type: ignore
                                supporting_resource_bundle_entry = {"fullUrl": "Observation/" + supporting_resource["id"], "resource": supporting_resource}
                            elif supporting_resource_type == "Condition":
                                supporting_resource = {
                                    "resourceType": "Condition",
                                    "id": supporting_resource_id,
                                    "identifier": [
                                        {
                                            "system": deploy_url,
                                            "value": "Condition/" + supporting_resource_id,
                                        }
                                    ],
                                    "code": {
                                        "coding": [
                                            {
                                                "system": answer_value_split[1],
                                                "code": answer_value_split[2],
                                                "display": answer_value_split[3],
                                            }
                                        ]
                                    },
                                    "onsetDateTime": effective_datetime,
                                    "subject": {"reference": f"Patient/{patient_id}"},
                                }
                                # try:
                                #     if external_fhir_server_auth:
                                #         supporting_resource_req = requests.get(external_fhir_server_url+"Condition/"+supporting_resource_id,
                                #                                                          headers={'Authorization': external_fhir_server_auth})
                                #         supporting_resource = supporting_resource_req.json()
                                #     else:
                                #         supporting_resource_req  = requests.get(external_fhir_server_url+"Condition/"+supporting_resource_id)
                                #         supporting_resource = supporting_resource_req.json()
                                # except requests.exceptions.JSONDecodeError:
                                #     logger.debug(f'Trying to find supporting resource with id Condition/{supporting_resource_id} '
                                #                  f'failed with status code {supporting_resource_req.status_code}') #type: ignore
                                supporting_resource_bundle_entry = {"fullUrl": "Condition/" + supporting_resource["id"], "resource": supporting_resource}
                            elif supporting_resource_type == "Procedure":
                                supporting_resource = {
                                    "resourceType": "Procedure",
                                    "id": supporting_resource_id,
                                    "identifier": [
                                        {
                                            "system": deploy_url,
                                            "value": "Procedure/" + supporting_resource_id,
                                        }
                                    ],
                                    "code": {
                                        "coding": [
                                            {
                                                "system": answer_value_split[1],
                                                "code": answer_value_split[2],
                                                "display": answer_value_split[3],
                                            }
                                        ]
                                    },
                                    "performedDateTime": effective_datetime,
                                    "subject": {"reference": f"Patient/{patient_id}"},
                                }
                                # try:
                                #     if external_fhir_server_auth:
                                #         supporting_resource_req = requests.get(external_fhir_server_url+"Procedure/"+supporting_resource_id,
                                #                                                          headers={'Authorization': external_fhir_server_auth})
                                #         supporting_resource = supporting_resource_req.json()
                                #     else:
                                #         supporting_resource_req  = requests.get(external_fhir_server_url+"Procedure/"+supporting_resource_id)
                                #         supporting_resource = supporting_resource_req.json()
                                # except requests.exceptions.JSONDecodeError:
                                #     logger.debug(f'Trying to find supporting resource with id Procedure/{asupporting_resource_id} '
                                #                  f'failed with status code {supporting_resource_req.status_code}') #type: ignore
                                supporting_resource_bundle_entry = {"fullUrl": "Procedure/" + supporting_resource["id"], "resource": supporting_resource}

                            else:
                                supporting_resource_bundle_entry = {}

                            tuple_observations.append(supporting_resource_bundle_entry)

                if not tuple_flag and not any(key in answer_obs_bundle_item["resource"] for key in ["focus", "valueString"]):  # type: ignore
                    continue

                # Add items to return bundle entry list
                if not tuple_flag:
                    if "valueString" in answer_obs_bundle_item["resource"] and not answer_obs_bundle_item["resource"]["valueString"]:  # type: ignore
                        del answer_obs_bundle_item["resource"]["valueString"]  # type: ignore
                    bundle_entries.append(answer_obs_bundle_item)  # type: ignore
                else:
                    bundle_entries.extend(tuple_observations)  # type: ignore
                if supporting_resources is not None:
                    bundle_entries.extend(supporting_resources)

        return_bundle_id = str(uuid.uuid4())
        return_bundle = {"resourceType": "Bundle", "id": return_bundle_id, "type": "collection", "entry": bundle_entries}

        delete_list = []
        for i, entry in enumerate(return_bundle["entry"]):
            try:
                if not entry["valueString"] and not entry["focus"]:
                    delete_list.append(i)
            except KeyError:
                pass

        for index in sorted(delete_list, reverse=True):
            del return_bundle["entry"][index]

        return_bundle_cql = return_bundle

    if results_nlpql:
        bundle_entries = []
        result_length = len(results_nlpql)
        if result_length == 1:
            result = results_nlpql[0]
            target_library = result["libraryName"]

        patient_resource_id = results_nlpql[0]["patientId"]

        results = flatten_results(results_nlpql)
        logger.info("Flattened NLPQL Results into the dictionary")
        logger.debug(results)

        if not results_cql:  # If there are only NLPQL results, there needs to be a Patient resource in the Bundle
            if external_fhir_server_auth:
                patient_resource = requests.get(external_fhir_server_url + f"Patient/{patient_id}", headers={"Authorization": external_fhir_server_auth}).json()
            else:
                patient_resource = requests.get(external_fhir_server_url + f"Patient/{patient_id}").json()
            patient_bundle_entry = {"fullUrl": f"Patient/{patient_id}", "resource": patient_resource}
            bundle_entries.append(patient_bundle_entry)

        # For each group of questions in the form
        for group in form["item"]:
            # For each question in the group in the form
            for question in group["item"]:
                link_id = question["linkId"]
                logger.info(f"Working on question {link_id}")
                library_task = "."
                # If the question has these extensions, get their values, if not, keep going
                try:
                    for extension in question["extension"]:
                        if extension["url"] == "http://gtri.gatech.edu/fakeFormIg/nlpqlTask":
                            library_task = extension["valueString"]
                        if extension["url"] == "http://gtri.gatech.edu/fakeFormIg/cardinality":
                            cardinality = extension["valueString"]
                    library, task = library_task.split(".")
                except KeyError:
                    logger.debug("This question did not have a task extension, moving onto next question")
                    continue
                if library == "":
                    logger.debug("No NLQPL Task found for this question, moving onto next question")
                    continue
                logger.debug(f"NLPQL Processing: Using library {library} and task {task} for this question")

                try:
                    task_result = results[task]
                except KeyError:
                    logger.info(f"There were no results for NLPQL task {task}, moving onto next question")
                    continue

                answer_obs_template = {
                    "resourceType": "Observation",
                    "status": "final",
                    "identifier": [{"system": deploy_url, "value": "Observation/"}],
                    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "survey", "display": "Survey"}]}],
                    "code": {"coding": [{"system": f"urn:gtri:heat:form:{form_name}", "code": link_id}]},
                    "focus": [],
                    "subject": {"reference": f"Patient/{patient_resource_id}"},
                }
                answer_obs_template = Observation(**answer_obs_template)

                doc_ref_template = {"resourceType": "DocumentReference", "status": "current", "type": {}, "subject": {"reference": f"Patient/{patient_resource_id}"}, "content": []}

                tuple_observations = []
                supporting_doc_refs = []
                for result in task_result:
                    temp_answer_obs = answer_obs_template
                    temp_answer_obs_uuid = str(uuid.uuid4())
                    temp_answer_obs.id = Id(temp_answer_obs_uuid)
                    temp_answer_obs.identifier[0].value = f"Observation/{temp_answer_obs_uuid}"  # type: ignore
                    try:
                        tuple_str = result["tuple"]
                    except KeyError:
                        logger.debug("No tuple result in this NLPQL result, moving to next result in list for task")
                        continue

                    logger.debug(f"Found tuple in NLPQL results: {tuple_str}")
                    tuple_dict = {}
                    tuple_str_list = tuple_str.split('"')
                    if len(tuple_str_list) > 32:
                        tuple_str_list[31] = "".join(tuple_str_list[31:])
                        del tuple_str_list[32:]
                    for i in range(3, len(tuple_str_list), 8):
                        key_name = tuple_str_list[i]
                        value_name = tuple_str_list[i + 4]
                        tuple_dict[key_name] = value_name

                    # TODO: Assert that tuples should have all 4 keys to work
                    temp_answer_obs.focus = [{"reference": f'DocumentReference/{result["report_id"]}'}]  # type: ignore
                    temp_answer_obs.note = [{"text": tuple_dict["sourceNote"]}]  # type: ignore
                    temp_answer_obs.valueString = tuple_dict["answerValue"]
                    temp_answer_obs.effectiveDateTime = datetime.strptime(result["report_date"], "%Y-%m-%d").strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore

                    # Check if an existing match exists to remove duplicates
                    is_duplicate = False
                    for obs in tuple_observations:
                        if all(key in obs for key in ["focus", "valueString"]) and (
                            obs["focus"] == [{"reference": f'DocumentReference/{result["report_id"]}'}] and obs["valueString"].lower() == tuple_dict["answerValue"].lower()
                        ):
                            is_duplicate = True

                    if not is_duplicate:
                        temp_answer_obs_dict = temp_answer_obs.dict()
                        if isinstance(temp_answer_obs_dict["effectiveDateTime"], datetime):
                            temp_answer_obs_dict["effectiveDateTime"] = temp_answer_obs_dict["effectiveDateTime"].strftime("%Y-%m-%dT%H:%M:%SZ")
                        tuple_observations.append(temp_answer_obs_dict)

                    # Queries for original DocumentReference, adds it to the supporting resources if its not already there or creating a DocumentReference with data from the NLPaaS Return
                    if result["report_id"] in [doc_ref["id"] for doc_ref in supporting_doc_refs]:  # Indicates a DocumentReference is already in there
                        continue

                    # try:
                    #     if external_fhir_server_auth:
                    #         supporting_resource_req = requests.get(external_fhir_server_url+"DocumentReference/"+result['report_id'], headers={'Authorization': external_fhir_server_auth})
                    #     else:
                    #         supporting_resource_req  = requests.get(external_fhir_server_url+"DocumentReference/"+result['report_id'])
                    #     supporting_resource_obj = supporting_resource_req.json()
                    #     supporting_resource_obj['content'] = [content for content in supporting_resource_obj['content'] if 'contentType' in content['attachment'] and content['attachment']['contentType']=='text/plain']
                    #     supporting_resource = DocumentReference(**supporting_resource_req.json())
                    # except requests.exceptions.JSONDecodeError:

                    # logger.debug(f'Trying to find supporting resource with id DocumentReference/{result["report_id"]} '
                    #             f'failed with status code {supporting_resource_req.status_code}, continuing to create one for the Bundle') #type: ignore

                    temp_doc_ref = doc_ref_template
                    temp_doc_ref["id"] = result["report_id"]
                    temp_doc_ref["date"] = result["report_date"]
                    temp_doc_ref["identifier"] = [
                        {
                            "system": deploy_url,
                            "value": "DocumentReference/" + result["report_id"],
                        }
                    ]
                    report_type_map = {
                        "Radiology Note": {"system": "http://loinc.org", "code": "75490-3", "display": "Radiology Note"},
                        "Discharge summary": {"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"},
                        "Hospital Note": {"system": "http://loinc.org", "code": "34112-3", "display": "Hospital Note"},
                        "Pathology consult note": {"system": "http://loinc.org", "code": "60570-9", "display": "Pathology Consult note"},
                        "Ancillary eye tests Narrative": {"system": "http://loinc.org", "code": "70946-9", "display": "Ancillary eye tests Narrative"},
                        "Nursing notes": {"system": "http://loinc.org", "code": "46208-5", "display": "Nursing notes"},
                        "Note": {"system": "http://loinc.org", "code": "34109-9", "display": "Note"},
                    }

                    try:
                        temp_doc_ref["type"]["coding"] = [report_type_map[result["report_type"]]]
                    except KeyError:
                        temp_doc_ref["type"]["coding"] = [report_type_map["Note"]]

                    doc_bytes = result["report_text"].encode("utf-8")
                    base64_bytes = base64.b64encode(doc_bytes)
                    base64_doc = base64_bytes.decode("utf-8")

                    temp_doc_ref["content"] = [{"attachment": {"contentType": "text/plain", "language": "en-US", "data": base64_doc}}]

                    if len(temp_doc_ref["date"]) == 10:  # Handles just date and no time for validation
                        temp_doc_ref["date"] = datetime.strptime(temp_doc_ref["date"], "%Y-%m-%d").strftime("%Y-%m-%dT%H:%M:%SZ")
                    else:
                        temp_doc_ref["date"] = datetime.strptime(temp_doc_ref["date"], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")
                    supporting_resource = DocumentReference(**temp_doc_ref)

                    supporting_res_dict = supporting_resource.dict()
                    if isinstance(supporting_res_dict["date"], datetime):
                        supporting_res_dict["date"] = supporting_res_dict["date"].strftime("%Y-%m-%dT%H:%M:%SZ")

                    supporting_doc_refs.append(supporting_res_dict)

                for tuple_observation in tuple_observations:
                    tuple_bundle_entry = {"fullUrl": f'Observation/{tuple_observation["id"]}', "resource": tuple_observation}
                    bundle_entries.append(tuple_bundle_entry)

                for doc_ref in supporting_doc_refs:
                    doc_bundle_entry = {"fullUrl": f'DocumentReference/{doc_ref["id"]}', "resource": doc_ref}
                    bundle_entries.append(doc_bundle_entry)

        return_bundle_nlpql = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "entry": bundle_entries}

    if return_bundle_cql and return_bundle_nlpql:
        return_bundle = return_bundle_cql
        return_bundle["entry"].extend(return_bundle_nlpql["entry"])
    elif return_bundle_cql:
        return_bundle = return_bundle_cql
    elif return_bundle_nlpql:
        return_bundle = return_bundle_nlpql
    else:
        logger.error("Something went wrong! Return bundles were not created.")
        return make_operation_outcome("transient", "Something went wrong and theres an empty return bundle. This shouldnt happen but this is here just in case.")
    return_bundle["total"] = len(return_bundle["entry"])
    return return_bundle


def validate_cql(code: str):
    """Validates CQL using CQF Ruler before persisting as a Library resource"""
    escaped_string_code = code.replace('"', '"')
    cql_operation_data = {
        "resourceType": "Parameters",
        "parameter": [{"name": "patientId", "valueString": "1"}, {"name": "context", "valueString": "Patient"}, {"name": "code", "valueString": escaped_string_code}],
    }
    req = requests.post(cqfr4_fhir + "$cql", json=cql_operation_data)
    if req.status_code != 200:
        logger.error(f"Trying to validate the CQL before creating library failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Trying to validate the CQL before creating library failed with status code {req.status_code}")
    validation_results = req.json()
    first_full_url = validation_results["entry"][0]["fullUrl"]
    if first_full_url == "Error":
        logger.error("There were errors in CQL validation. Compiling errors into an OperationOutcome")
        num_errors = len(validation_results["entry"])
        diagnostics_list = []
        combined_oo = {"resourceType": "OperationOutcome", "issue": []}
        for i in range(0, num_errors):
            diagnostics = ": ".join([item["name"] + " " + item["valueString"] for item in validation_results["entry"][i]["resource"]["parameter"]])
            diagnostics_list.append(diagnostics)
        for diagnostic in diagnostics_list:
            oo_item = {
                "severity": "error",
                "code": "invalid",
                "diagnostics": diagnostic,
            }
            combined_oo["issue"].append(oo_item)
        logger.error(f"There were a total of {num_errors} errors. The OperationOutcome will be returned to the client as well as logged below.")
        logger.error(combined_oo)
        return combined_oo
    else:
        logger.info("CQL successfully validated!")
        return True


def validate_nlpql(code_in: str):
    """Validates NLPQL using NLPaaS before persisting in CQF Ruler as a Library resource"""
    code = code_in.encode(encoding="utf-8")
    try:
        req = requests.post(nlpaas_url + "job/validate_nlpql", data=code, headers={"Content-Type": "text/plain"})
    except requests.exceptions.ConnectionError as error:
        logger.error(f"Error when trying to connect to NLPaaS {error}")
        return make_operation_outcome("transient", "Error when connecting to NLPaaS, see full error in logs. This normally happens due to a DNS name issue.")

    if req.status_code != 200:
        logger.error(f"Trying to validate NLPQL against NLPAAS failed with status code {req.status_code}")
        return make_operation_outcome("transient", f"Trying to validate NLPQL against NLPAAS failed with status code {req.status_code}")
    validation_results = req.json()
    try:
        valid = validation_results["valid"]
    except KeyError:
        logger.error("valid key not found in NLPAAS validation results, see NLPAAS response below to investigate any errors.")
        logger.error(validation_results)
        return make_operation_outcome("transient", "Valid key not found in the NLPAAS validation results, see logs for full dump of NLPAAS validation response.")
    if valid:
        return True
    else:
        try:
            return make_operation_outcome("invalid", validation_results["reason"])
        except KeyError:
            logger.error("NLPQL validation did not succeed but there was no reason given in the response. See dump below of NLPAAS response.")
            logger.error(validation_results)
            return make_operation_outcome("invalid", "Validation results were invalid but the reason was not given, see logs for full dump of NLPAAS response.")
