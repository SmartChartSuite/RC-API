import csv
import sys
import json
import re

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

        print("------------------------")
        title_row = next(csvFile)
        print("TITLE: ", title_row[2])
        version_row = next(csvFile)
        print("VERSION: ", version_row[2])
        description_row = next(csvFile)
        print("DESCRIPTION: ", description_row[2])
        print("------------------------")

        questionnaire['id'] = parse_title_to_id(title_row[2])
        questionnaire['url'] = server_base + questionnaire['url'] + questionnaire['id']
        questionnaire['name'] = questionnaire['id']
        questionnaire['title'] = title_row[2]
        questionnaire['version'] = version_row[2]
        questionnaire["description"] = description_row[2]

        header_row = next(csvFile)

        parsed_groups = []
        group_item_list = []
        group_sub_items_dict = {}

        for row in csvFile:
            if not row[2] in parsed_groups:
                parsed_groups.append(row[2])
                new_group_item = {
                    "linkId": row[2],
                    "type": "group",
                    "text": row[2],
                    "item": []
                }
                group_item_list.append(new_group_item)
                group_sub_items_dict[row[2]] = []
            
            row_as_q_item = {
                "linkId": row[0],
                "text": row[1],
                "type": row[5],
                "extension": []
            }
            if row[3] == "CQL":
                extension = get_extension_template(server_base + "cqlTask", row[4])
                row_as_q_item["extension"].append(extension)
            
            group_sub_items_dict[row[2]].append(row_as_q_item)
        
        for item in group_item_list:
            item['item'] = group_sub_items_dict[item['linkId']]
            extension = get_extension_template("form-task-group", parse_group_to_machine_readable(item['linkId']))
            questionnaire['extension'][0]['extension'].append(extension)
            questionnaire['item'].append(item)
        
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