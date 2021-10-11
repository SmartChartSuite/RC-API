import json
import os

for filename in os.listdir(os.getcwd()+'/output-simple'):
    if filename.endswith(".nlpql"):
        with open('output/'+ filename, 'r') as f:
            data = f.read()

        output_json = {
            "name": filename,
            "content": data
        }
        with open(os.getcwd()+'/output-simple/asDict/'+filename+'.json', 'w') as f:
            json.dump(output_json, f)