"""Settings file for importing environmental variables"""

import logging
import os

import httpx
from loguru import logger
from pydantic import BaseModel


class ConfigEndpointPrimaryIdentifier(BaseModel):
    label: str | None = None
    system: str | None = None


class ConfigEndpointModel(BaseModel):
    primaryIdentifier: ConfigEndpointPrimaryIdentifier | None = None


# ================= Creating necessary variables from Secrets ========================
cqfr4_fhir = os.environ["CQF_RULER_R4"]
external_fhir_server_url = os.environ["EXTERNAL_FHIR_SERVER_URL"]
external_fhir_server_auth = os.environ.get("EXTERNAL_FHIR_SERVER_AUTH", "")
nlpaas_url = os.environ.get("NLPAAS_URL", "False")
log_level = os.environ.get("LOG_LEVEL", "info").upper()
api_docs = os.environ.get("API_DOCS", "true")
knowledgebase_repo_url = os.environ.get("KNOWLEDGEBASE_REPO_URL", "")
docs_prepend_url = os.environ.get("DOCS_PREPEND_URL", "")
deploy_url = os.environ.get("DEPLOY_URL", "http://example.org/")
db_connection_string = os.environ.get("DB_CONNECTION_STRING", "sqlite+pysqlite:///rcapi_jobs.sqlite")
db_schema = os.environ.get("DB_SCHEMA", "rcapi")

primary_identifier_system = os.environ.get("PRIMARYIDENTIFIER_SYSTEM")
primary_identifier_label = os.environ.get("PRIMARYIDENTIFIER_LABEL")

if cqfr4_fhir[-1] != "/":
    cqfr4_fhir += "/"

if external_fhir_server_url[-1] != "/":
    external_fhir_server_url += "/"

if deploy_url[-1] != "/":
    deploy_url += "/"

if nlpaas_url and nlpaas_url.lower() != "false" and nlpaas_url[-1] != "/":
    nlpaas_url += "/"
elif nlpaas_url.lower() == "false":
    nlpaas_url = ""

transport: httpx.HTTPTransport = httpx.HTTPTransport(retries=5)
httpx_client: httpx.Client = httpx.Client(transport=transport)

config_endpoint: ConfigEndpointModel | dict = (
    ConfigEndpointModel.model_validate({"primaryIdentifier": {"system": primary_identifier_system, "label": primary_identifier_label}}) if primary_identifier_system else {}
)


# ================= Logging setup ========================
os.environ["LOGURU_LEVEL"] = log_level

# Remove existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# Intercept standard logging
logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)

loggers = (
    "hypercorn",
    "hypercorn.access",
    "hypercorn.error",
    "fastapi",
    "asyncio",
    "starlette",
)

for logger_name in loggers:
    logging_logger = logging.getLogger(logger_name)
    logging_logger.handlers = []
    logging_logger.propagate = True
