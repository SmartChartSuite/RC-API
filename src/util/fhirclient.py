import base64

from requests.auth import HTTPBasicAuth

from src.util.settings import session


class FhirClient:
    server_base: str
    basic_auth: HTTPBasicAuth | None
    content_type_header: dict[str, str]

    def __init__(self, server_base, basic_auth=None):
        self.server_base = server_base
        self.basic_auth = basic_auth
        self.content_type_header = {"Content-type": "application/fhir+json"}

    def createResource(self, resource_type: str, resource):
        response = session.post(f"{self.server_base}/{resource_type}", resource, auth=self.basic_auth, headers=self.content_type_header).json()
        return response

    def updateResource(self, resource_type, id, resource):
        return {}

    def readResource(self, resource_type: str, id: str):
        headers = {}
        if self.basic_auth:
            basic_auth_str = self.basic_auth.username + ":" + self.basic_auth.password  # type: ignore
            headers["Authorization"] = f"Basic {base64.b64encode(basic_auth_str)}"
        response = session.get(f"{self.server_base}/{resource_type}/{id}", headers=headers).json()
        return response

    def searchResource(self, resource_type: str, parameters=None, flatten=False):
        searchset = session.get(f"{self.server_base}/{resource_type}", auth=self.basic_auth).json()
        if flatten:
            resource_list = []
            for entry in searchset["entry"]:
                resource_list.append(entry["resource"])
            return resource_list
        return searchset
