import typing

import httpx

from src.util.settings import httpx_client as client


class FhirClient:
    server_base: str
    basic_auth: httpx.BasicAuth | None
    content_type_header: dict[str, str]

    def __init__(self, server_base, basic_auth=None):
        self.server_base = server_base
        self.basic_auth = basic_auth
        self.content_type_header = {"Content-type": "application/fhir+json"}

    def __http_request__(self, method: str, url_path: str, json_body: dict | None = None, headers: dict | None = None, params: dict | None = None) -> httpx.Response:
        url = f"{self.server_base}/{url_path}"
        request_args: dict[str, typing.Any] = {"method": method, "url": url}
        if headers is not None:
            request_args["headers"] = headers
        if json_body is not None:
            request_args["json"] = json_body
        if self.basic_auth is not None:
            request_args["auth"] = self.basic_auth
        if params is not None:
            request_args["params"] = params
        return client.request(**request_args)

    def createResource(self, resource_type: str, resource) -> httpx.Response:
        return self.__http_request__(method="POST", url_path=f"{resource_type}", json_body=resource)

    def updateResource(self, resource_type, id, resource) -> httpx.Response:
        return self.__http_request__(method="PUT", url_path=f"{resource_type}/{id}", json_body=resource)

    def readResource(self, resource_type: str, id: str) -> httpx.Response:
        return self.__http_request__(method="GET", url_path=f"{resource_type}/{id}")

    @typing.overload
    def searchResource(self, resource_type: str, parameters: dict | None = None, flatten: typing.Literal[False] = False) -> dict:
        pass

    @typing.overload
    def searchResource(self, resource_type: str, parameters: dict | None = None, flatten: typing.Literal[True] = True) -> list:
        pass

    def searchResource(self, resource_type: str, parameters: dict | None = None, flatten: bool = False) -> list | dict:
        searchset: dict = self.__http_request__(method="GET", url_path=f"{resource_type}", params=parameters).json()
        if flatten:
            resource_list = []
            for entry in searchset["entry"]:
                resource_list.append(entry["resource"])
            return resource_list
        return searchset
