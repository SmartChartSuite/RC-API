"""Forms module for all form/Questionnaire operations called by other files"""

import csv
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Literal, overload

from fhir.resources.R4B.questionnaire import Questionnaire

from src.services.errorhandler import make_operation_outcome
from src.util.settings import cqfr4_fhir, session
from static.diagnostic_questionnaire import diagnostic_questionnaire

logger: logging.Logger = logging.getLogger("rcapi.models.forms")

jobpackage_server_base = "http://gtri.gatech.edu/fakeFormIg/"

questionnaire_template = {
    "resourceType": "Questionnaire",
    "meta": {"profile": ["http://gtri.gatech.edu/fakeFormIg/StructureDefinition/smartchart-form"]},
    "id": "",
    "url": "Questionnaire/",
    "name": "",
    "version": "",
    "title": "",
    "status": "draft",
    "experimental": True,
    "publisher": "GTRI",
    "description": "",
    "subjectType": ["Patient"],
    "extension": [{"url": "http://gtri.gatech.edu/fakeFormIg/cql-form-job-list", "extension": []}, {"url": "http://gtri.gatech.edu/fakeFormIg/nlpql-form-job-list", "extension": []}],
    "item": [],
}


@overload
def get_form(form_name: str, form_version: str | None, return_Questionnaire_class_obj: Literal[False]) -> dict:
    pass


@overload
def get_form(form_name: str, form_version: str | None, return_Questionnaire_class_obj: Literal[True]) -> Questionnaire:
    pass


def get_form(form_name: str, form_version: str | None = None, return_Questionnaire_class_obj: bool = False) -> dict | Questionnaire:
    """Returns the Questionnaire from CQF Ruler based on form name"""

    if form_name.lower() == "diagnostic":
        return diagnostic_questionnaire

    if form_version:
        req = session.get(cqfr4_fhir + f"Questionnaire?name:exact={form_name}&version={form_version}")
        if req.status_code != 200:
            logger.error(f"Getting Questionnaire from server failed with status code {req.status_code}")
            return make_operation_outcome("transient", f"Getting Questionnaire from server failed with code {req.status_code}")
    else:
        req = session.get(cqfr4_fhir + f"Questionnaire?name:exact={form_name}&_sort=-_lastUpdated")
        if req.status_code != 200:
            logger.error(f"Getting Questionnaire from server failed with status code {req.status_code}")
            return make_operation_outcome("transient", f"Getting Questionnaire from server failed with code {req.status_code}")

    search_bundle = req.json()
    try:
        questionnaire = search_bundle["entry"][0]["resource"]
        logger.info(f"Found Questionnaire with name {form_name}, version {questionnaire['version']}, and form server ID {questionnaire['id']}")
        return questionnaire if not return_Questionnaire_class_obj else Questionnaire.parse_obj(questionnaire)
    except KeyError:
        logger.error(f"Questionnaire with name {form_name} and version {form_version} not found") if form_version else logger.error(f"Questionnaire with name {form_name} not found")
        return (
            make_operation_outcome("not-found", f"Questionnaire with name {form_name} and version {form_version} not found")
            if form_version
            else make_operation_outcome("not-found", f"Questionnaire with name {form_name} not found on the FHIR server.")
        )


def run_diagnostic_questionnaire(run_all_jobs: bool, libs_to_run: list, form: dict) -> dict:
    return_bundle = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "entry": []}
    return_bundle["entry"].append({"fullUrl": "Patient/0", "resource": {"resourceType": "Patient", "id": "0"}})
    if run_all_jobs:
        logger.warning("The diagnostic Questionnaire is not supported for running every job, this will return a Bundle with only a minimal Patient and nothing else")
        return_bundle["total"] = "1"
        return return_bundle

    library = libs_to_run[0]
    full_job_list = [item["valueString"] for item in form["extension"][0]["extension"]]
    library_index = full_job_list.index(library)
    sleep_time = 30  # (library_index + 1) * 30 this is commented out due to blocking method making things go sequentially when this happens
    logger.info(f"Running {library.strip('.cql')} and will be sleeping for {sleep_time} seconds")
    time.sleep(sleep_time)

    obs_id = str(uuid.uuid4())

    test_obs = {
        "resourceType": "Observation",
        "id": obs_id,
        "identifier": [{"system": "https://smartchartsuite.dev.heat.icl.gtri.org/rc-api/", "value": f"Observation/{obs_id}"}],
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "survey", "display": "Survey"}]}],
        "code": {"coding": [{"system": "urn:gtri:heat:form:Diagnostic", "code": str(library_index + 1)}]},
        "subject": {"reference": "Patient/0"},
        "effectiveDateTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valueString": f"Test String Output for Job {str(library_index + 1)}",
    }

    return_bundle["entry"].append({"fullUrl": f"Observation/{obs_id}", "resource": test_obs})
    return_bundle["total"] = len(return_bundle["entry"])
    return return_bundle


def get_extension_template(url, valueString) -> dict[str, str]:
    return {"url": url, "valueString": valueString}


def parse_title_to_id(title: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", title)


def parse_group_to_machine_readable(group: str) -> str:
    group = group.lower()
    group = group.replace(" ", "_")
    return group


def modify_item_in_questionnaire_item(top_level_item: dict):
    top_level_item["item"] = [remove_nlpql_task_from_questionnaire_item(item) for item in top_level_item["item"]]
    return top_level_item


def remove_nlpql_task_from_questionnaire_item(question: dict):
    if "extension" in question:
        question["extension"] = [ext for ext in question["extension"] if ext["url"] != jobpackage_server_base + "nlpqlTask"]
    return question


def return_cql_only_questionnaire(questionnaire: dict) -> dict:
    logger.info("Returning CQL Only Questionnaire")

    questionnaire["id"] += "CQLOnly"
    questionnaire["url"] += "CQLOnly"
    questionnaire["name"] += "CQLOnly"
    questionnaire["title"] += "-CQLOnly"
    questionnaire["description"] += " (CQL Only)"
    questionnaire["extension"] = [ext for ext in questionnaire["extension"] if ext["url"] == jobpackage_server_base + "cql-form-job-list"]
    questionnaire["item"] = [modify_item_in_questionnaire_item(item) for item in questionnaire["item"]]

    return questionnaire


def convert_jobpackage_csv_to_questionnaire(jobpackage_csv: str, cql_only: bool, smartchart: bool):
    logger.info("Converting Job Package CSV to Questionnaire...")
    csvfile = jobpackage_csv.split("\n")
    csvFile = csv.reader(csvfile)

    questionnaire = dict(questionnaire_template)

    ## Print Header information.
    title_row: list[str] = next(csvFile)
    logger.info(f"Job Package Title: {title_row[1]}")
    version_row: list[str] = next(csvFile)
    logger.info(f"Job Package Version: {version_row[1]}")
    description_row: list[str] = next(csvFile)
    logger.info(f"Job Package Description: {description_row[1]}")

    ## Set root "header" elements of the resource.
    questionnaire["id"] = parse_title_to_id(title_row[1])
    questionnaire["url"] = jobpackage_server_base + questionnaire["url"] + questionnaire["id"]
    questionnaire["name"] = questionnaire["id"]
    questionnaire["title"] = title_row[1]
    questionnaire["version"] = version_row[1]
    questionnaire["description"] = description_row[1]

    if smartchart:
        questionnaire["useContext"] = [
            {
                "code": {"system": "http://terminology.hl7.org/CodeSystem/usage-context-type", "code": "workflow", "display": "Workflow Setting"},
                "valueCodeableConcept": {"coding": [{"system": "http://gtri.gatech.edu/fakeFormIg/CodeSystem/custom", "code": "smartchartui", "display": "SmartChart UI"}]},
            }
        ]

    ## The csv table header row that isn't actually parsed.
    next(csvFile)

    ## Columns: 1 Question Text | 2 Group | 3 LinkID | 4 Task Type | 5 CQL Library | 6 CQL Task | 7 Cardinality | 8 Item/Answer Type | 9 AnswerOptions

    parsed_groups = []  ## Tracks root level group items by name. TODO: Refactor, shouldn't be needed.
    group_item_list = []  ## Root level group items.
    group_sub_items_dict = {}  ## Sub items divided by key equivalent to group.
    cql_job_list = []
    nlpql_job_list = []

    for row in csvFile:
        ## Check if group has been previously observed/parsed. If not, create a new root level group item.
        if row[1] not in parsed_groups:  ## Group - Column 2
            parsed_groups.append(row[1])  ## Group - Column 2
            new_group_item = {
                "linkId": row[1],  ## Group - Column 2
                "type": "group",
                "text": row[1],  ## Group - Column 2
                "item": [],
            }
            group_item_list.append(new_group_item)
            group_sub_items_dict[row[1]] = []

        ## Parse the row into a questionnaire Item.
        row_as_q_item: dict[str, str | list] = {
            "linkId": row[2],  ## LinkID - Column 3
            "text": row[0],  ## Question Text - Column 1
            "type": row[7],  ## Item/Question Type - Column 8
        }

        ## Check to see if this is a "new" question in the job package
        current_link_ids = []
        try:
            for item in group_sub_items_dict[row[1]]:
                current_link_ids.append(item["linkId"])
        except KeyError:
            pass  # This is a new group, therefore current_link_ids will stay empty because there aren't any in the group

        # This would be a new linkId
        if row[2] not in current_link_ids:
            ## Set the answer choices for items with type choice.
            if row_as_q_item["type"] == "choice":
                row_as_q_item["answerOption"] = []  # Initialize list of answerOption.
                answerOption = row[8].split("|")
                for answer in answerOption:
                    row_as_q_item["answerOption"].append({"valueString": answer.strip()})

            ## If type is not display, set the cardinality of the expected answer and extensions.
            if not row_as_q_item["type"] == "display":
                row_as_q_item["extension"] = []  ## Initialize the Extension list if not type display.
                cardinality_extension = get_extension_template(jobpackage_server_base + "cardinality", row[6])  ## Cardinality - Column 7
                row_as_q_item["extension"].append(cardinality_extension)

                ## If the row has a task type of CQL, add the CQL extension.
                if row[3] == "CQL":  ## Task Type - Column 4
                    ## If the library has not yet been observed and added to the job list, do so.
                    if (row[4] + ".cql") not in cql_job_list:  ## CQL Library - Column 5
                        cql_job_list.append(".".join([row[4], "cql"]))  ## CQL Library - Column 5
                    cql_task_extension = get_extension_template(
                        jobpackage_server_base + "cqlTask", row[4] + "." + row[5]
                    )  ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                    row_as_q_item["extension"].append(cql_task_extension)

                elif row[3] == "NLPQL":  ## Task Type - Column 4
                    if (row[4] + ".nlpql") not in nlpql_job_list:  ## NLPQL Library - Column 5
                        nlpql_job_list.append(".".join([row[4], "nlpql"]))  ## NLPQL Library - Column 5
                    nlpql_task_extension = get_extension_template(
                        jobpackage_server_base + "nlpqlTask", row[4] + "." + row[5]
                    )  ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                    row_as_q_item["extension"].append(nlpql_task_extension)
                elif not row_as_q_item["type"] == "display":
                    pass

            ## After parsing the row, add it to the list of items associated with the group by key.
            group_sub_items_dict[row[1]].append(row_as_q_item)  ## Group - Column 2
        else:
            prev_item = group_sub_items_dict[row[1]][-1]  # Get the previous item
            assert prev_item["linkId"] == row[2]  # TODO: make this so that the linkIds dont need to be sequential to work
            if row[3] == "CQL":
                # If the library has not yet been observed and added to the job list, do so.
                if (row[4] + ".cql") not in cql_job_list:  ## CQL Library - Column 5
                    cql_job_list.append(".".join([row[4], "cql"]))  ## CQL Library - Column 5
                cql_task_extension = get_extension_template(jobpackage_server_base + "cqlTask", row[4] + "." + row[5])  ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                prev_item["extension"].append(cql_task_extension)
            elif row[3] == "NLPQL":
                if (row[4] + ".nlpql") not in nlpql_job_list:  ## NLPQL Library - Column 5
                    nlpql_job_list.append(".".join([row[4], "nlpql"]))  ## NLPQL Library - Column 5
                nlpql_task_extension = get_extension_template(
                    jobpackage_server_base + "nlpqlTask", row[4] + "." + row[5]
                )  ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                prev_item["extension"].append(nlpql_task_extension)

            group_sub_items_dict[row[1]][-1] = prev_item
    for item in group_item_list:
        item["item"] = group_sub_items_dict[item["linkId"]]
        questionnaire["item"].append(item)

    for job in cql_job_list:
        extension = get_extension_template("form-job", job)
        questionnaire["extension"][0]["extension"].append(extension)
    for job in nlpql_job_list:
        extension = get_extension_template("form-job", job)
        questionnaire["extension"][1]["extension"].append(extension)

    return questionnaire if not cql_only else return_cql_only_questionnaire(questionnaire)
