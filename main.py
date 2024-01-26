'''Main application file'''

import logging
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from src.util.git import clone_repo_to_temp_folder
from src.routers import routers, webhook
from src.routers import smartchartui
from src.models.functions import make_operation_outcome

from src.util.settings import api_docs, knowledgebase_repo_url, log_level, docs_prepend_url, deploy_url

from src.models.models import CustomFormatter

title: str = 'SmartPacer Results Combining (RC) API'
version: str = '0.9.0'

logger = logging.getLogger('rcapi')
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

if log_level == "DEBUG":
    logger.setLevel(logging.DEBUG)
    ch.setLevel(logging.DEBUG)

# ================= FastAPI variable ===================================

if api_docs.lower() == 'true':
    app = FastAPI(title=title, version=version, include_in_schema=True, docs_url=None, redoc_url=None)
else:
    app = FastAPI(title=title, version=version, include_in_schema=False, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Routers inclusion from src directory ===============

app.include_router(routers.apirouter)
app.include_router(webhook.apirouter)
app.include_router(smartchartui.apirouter)

# ================= Invalid Request Exception Handling =================


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    '''Formats all invalidated requests to return as OperationOutcomes'''
    request = str(request)
    return JSONResponse(make_operation_outcome('invalid', str(exc)), status_code=400)

# ================== Custom OpenAPI ===========================


def custom_openapi():
    '''Defines the custom OpenAPI schema handling'''
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=title,
        version=version,
        description="This is a custom Open API Schema to align with SmartPacer's RC API.",
        routes=app.routes,
    )
    openapi_schema["servers"] = [{"url": deploy_url}]
    openapi_schema["paths"]["/forms/cql"]["post"]["requestBody"]["content"] = {"text/plain": {"schema": {}}}
    openapi_schema["paths"]["/forms/nlpql"]["post"]["requestBody"]["content"] = {"text/plain": {"schema": {}}}
    openapi_schema["paths"]["/forms/cql/{library_name}"]["put"]["requestBody"]["content"] = {"text/plain": {"schema": {}}}
    openapi_schema["paths"]["/forms/nlpql/{library_name}"]["put"]["requestBody"]["content"] = {"text/plain": {"schema": {}}}
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def startup_event():
    '''On startup, check for knowledgebase repo variables and if found, update the libraries on CQF Ruler'''
    # Check for private key and known hosts in secrets
    # Set these (required for both hook clone and startup clone)
    # setup_keys()
    # Check for repo ssh env
    # if present do startup
    # pre_load_scripts(ssh_url_from_env)
    if knowledgebase_repo_url:
        # TODO: Add error handling.
        logger.info("Knowledgebase Repo configuration detected.")
        logger.info("Loading libraries from Knowledgebase Repository...")
        clone_repo_to_temp_folder(knowledgebase_repo_url)
    else:
        logger.info("Knowledgebase Repo configured not detected.")
        logger.info("Skipping initial library load.")


if api_docs.lower() == 'true':
    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        '''Custom Swagger UI HTML'''
        return get_swagger_ui_html(
            openapi_url=docs_prepend_url + app.openapi_url, #type: ignore
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url="static/swagger-ui-bundle.js",
            swagger_css_url="static/swagger-ui.css",
        )

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False) #type: ignore
    async def swagger_ui_redirect():
        '''Custom Swagger UI Redirect'''
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html():
        '''Custom Redoc HTML'''
        return get_redoc_html(
            openapi_url=docs_prepend_url + app.openapi_url, #type: ignore
            title=app.title + " - ReDoc",
            redoc_js_url="static/redoc.standalone.js",
        )
