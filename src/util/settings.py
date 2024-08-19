"""Settings file for importing environmental variables"""

import os
import logging

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


logger: logging.Logger = logging.getLogger("rcapi.util.settings")

# ================= Creating necessary variables from Secrets ========================
cqfr4_fhir = os.environ["CQF_RULER_R4"]
external_fhir_server_url = os.environ["EXTERNAL_FHIR_SERVER_URL"]
external_fhir_server_auth = os.environ.get("EXTERNAL_FHIR_SERVER_AUTH", "")
nlpaas_url = os.environ.get("NLPAAS_URL", "False")
log_level = os.environ.get("LOG_LEVEL", "info")
api_docs = os.environ.get("API_DOCS", "true")
knowledgebase_repo_url = os.environ.get("KNOWLEDGEBASE_REPO_URL", "")
docs_prepend_url = os.environ.get("DOCS_PREPEND_URL", "")
deploy_url = os.environ.get("DEPLOY_URL", "http://example.org/")
db_connection_string = os.environ.get("DB_CONNECTION_STRING", "sqlite+pysqlite:///rcapi_jobs.sqlite")
db_schema = os.environ.get("DB_SCHEMA", "rcapi")

if cqfr4_fhir[-1] != "/":
    cqfr4_fhir += "/"

if external_fhir_server_url[-1] != "/":
    external_fhir_server_url += "/"

if deploy_url[-1] != "/":
    deploy_url += "/"

if nlpaas_url[-1] != "/":
    nlpaas_url += "/"

session: Session = Session()
retries: Retry = Retry(total=5, allowed_methods={"GET", "POST", "PUT", "DELETE"}, status_forcelist=[500])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))
