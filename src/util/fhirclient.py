import requests
from requests.auth import HTTPBasicAuth
import base64

class FhirClient():
    server_base: str
    basic_auth: HTTPBasicAuth
    content_type_header: any

    def __init__(self, server_base, basic_auth = None):
        self.server_base = server_base
        self.basic_auth = basic_auth
        self.content_type_header = {'Content-type': 'application/fhir+json'}

    def createResource(self, resource_type, resource):
        response = requests.post(f'{self.server_base}/{resource_type}', resource, auth=self.basic_auth, headers=self.content_type_header).json()
        return response

    def updateResource(self, resource_type, id, resource):
        return {}

    def readResource(self, resource_type, id):
        headers = {}
        if self.basic_auth is not None:
            headers["Authorization"] = f"Basic {base64.encode(self.basic_auth)}"
        response = requests.get(f'{self.server_base}/{resource_type}/{id}', headers=headers).json()
        return response

    def searchResource(self, resource_type, parameters = None, flatten = False):
        searchset = requests.get(f'{self.server_base}/{resource_type}', auth=self.basic_auth).json()
        if flatten:
            resource_list = []
            for entry in searchset['entry']:
                resource_list.append(entry["resource"])
            return resource_list
        return searchset