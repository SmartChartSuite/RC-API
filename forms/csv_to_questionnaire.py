import csv
import sys
import json
import re
from collections import defaultdict

# To define a server base for Questionnaire URL elements, set it here.
server_base = 'http://gtri.gatech.edu/fakeFormIg/'

def get_extension_template(url, valueString):
    return { "url": url, "valueString": valueString }

def get_questionnaire_template():
    questionnaire_file = open("questionnaire_template.json", "r")
    questionnaire_file_contents = questionnaire_file.read()
    questionnaire_template = json.loads(questionnaire_file_contents)
    return questionnaire_template

def parse_title_to_id(title):
    return re.sub(r'[^a-zA-Z0-9]', '', title)

def parse_group_to_machine_readable(group):
    group = group.lower()
    group = group.replace(" ", "_")
    return group

def convert_csv_to_questionnaire(csv_file_path = '', delimiter=","):
    print("\nCONVERTING CSV TO QUESTIONNAIRE... Hopefully.\n")
    with open(csv_file_path) as csvfile:
        csvFile = csv.reader(csvfile)

        questionnaire = get_questionnaire_template()

        ## Print Header information.
        print("------------------------")
        title_row = next(csvFile)
        print("TITLE: ", title_row[1])
        version_row = next(csvFile)
        print("VERSION: ", version_row[1])
        description_row = next(csvFile)
        print("DESCRIPTION: ", description_row[1])
        print("------------------------")

        ## Set root "header" elements of the resource.
        questionnaire['id'] = parse_title_to_id(title_row[1])
        questionnaire['url'] = server_base + questionnaire['url'] + questionnaire['id']
        questionnaire['name'] = questionnaire['id']
        questionnaire['title'] = title_row[1]
        questionnaire['version'] = version_row[1]
        questionnaire["description"] = description_row[1]

        ## The csv table header row that isn't actually parsed.
        header_row = next(csvFile)
        ## Columns: 1 Question Text | 2 Group | 3 LinkID | 4 Task Type | 5 CQL Library | 6 CQL Task | 7 Cardinality | 8 Item/Answer Type | 9 AnswerOptions

        parsed_groups = [] ## Tracks root level group items by name. TODO: Refactor, shouldn't be needed.
        group_item_list = [] ## Root level group items.
        group_sub_items_dict = {} ## Sub items divided by key equivalent to group.
        cql_job_list = []
        nlpql_job_list = []

        for row in csvFile:

            ## Check if group has been previously observed/parsed. If not, create a new root level group item.
            if not row[1] in parsed_groups: ## Group - Column 2
                parsed_groups.append(row[1]) ## Group - Column 2
                new_group_item = {
                    "linkId": row[1], ## Group - Column 2
                    "type": "group",
                    "text": row[1], ## Group - Column 2
                    "item": []
                }
                group_item_list.append(new_group_item)
                group_sub_items_dict[row[1]] = []

            ## Parse the row into a questionnaire Item.
            row_as_q_item = {
                "linkId": row[2], ## LinkID - Column 3
                "text": row[0], ## Question Text - Column 1
                "type": row[7] ## Item/Question Type - Column 8
            }

            ## Set the answer choices for items with type choice.
            if row_as_q_item["type"] == "choice":
                row_as_q_item["answerOption"] = [] # Initialize list of answerOption.
                answerOption = row[8].split("|")
                for answer in answerOption:
                    row_as_q_item["answerOption"].append({"valueString": answer.strip()})

            ## If type is not display, set the cardinality of the expected answer and extensions.
            if not row_as_q_item["type"] == "display":
                row_as_q_item["extension"] = [] ## Initialize the Extension list if not type display.
                cardinality_extension = get_extension_template(server_base + "cardinality", row[6]) ## Cardinality - Column 7
                row_as_q_item["extension"].append(cardinality_extension)

                ## If the row has a task type of CQL, add the CQL extension.
                if row[3] == "CQL": ## Task Type - Column 4
                    ## If the library has not yet been observed and added to the job list, do so.
                    if not (row[4]+'.cql') in cql_job_list: ## CQL Library - Column 5
                        cql_job_list.append('.'.join([row[4], 'cql'])) ## CQL Library - Column 5
                    cql_task_extension = get_extension_template(server_base + "cqlTask", row[4] + "." + row[5]) ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                    row_as_q_item["extension"].append(cql_task_extension)

                elif row[3] == "NLPQL": ## Task Type - Column 4
                    if not (row[4]+'.nlpql') in nlpql_job_list: ## NLPQL Library - Column 5
                        nlpql_job_list.append('.'.join([row[4], 'nlpql'])) ## NLPQL Library - Column 5
                    nlpql_task_extension = get_extension_template(server_base + "nlpqlTask", row[4] + "." + row[5]) ## CQL Library - Column 5 + CQL Task - Column 6 combined as string for value.
                    row_as_q_item["extension"].append(nlpql_task_extension)
                elif not row_as_q_item["type"] == "display":
                    print("No task type found, all items which aren't type display must include valid task type.")

            ## After parsing the row, add it to the list of items associated with the group by key.
            group_sub_items_dict[row[1]].append(row_as_q_item) ## Group - Column 2

        for item in group_item_list:
            item['item'] = group_sub_items_dict[item['linkId']]
            questionnaire['item'].append(item)

        for job in cql_job_list:
            extension = get_extension_template("form-job", job)
            questionnaire['extension'][0]['extension'].append(extension)
        for job in nlpql_job_list:
            extension = get_extension_template('form-job', job)
            questionnaire['extension'][1]['extension'].append(extension)

        save_file(questionnaire)


def save_file(questionnaire):
    # print(json.dumps(questionnaire, indent=4))
    output_file_name = questionnaire['name'] + '.json'
    print("\nSAVING AS...", output_file_name)
    print()
    with open(output_file_name, 'w') as out:
        json.dump(questionnaire, out, indent=4)


def print_usage():
    print("Usage: 'python csv_to_questionnaire.json -f filename.csv'")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Invalid number of Arguments. See Usage.")
        print_usage()
    elif not sys.argv[1] == "-f":
        print("File flag (-f) not specified. See Usage.")
        print_usage()
    elif not sys.argv[2].endswith(".csv"):
        print("CSV file not specified as argument following -f flag. Ensure proper .csv file extension.")
        print_usage()
    else:
        convert_csv_to_questionnaire(csv_file_path = sys.argv[2])