import os
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
from src.util.git import cloneRepoToTempFolder


from src.routers.routers import apirouter
from src.models.functions import make_operation_outcome

from src.util.settings import api_docs


#------------------ FastAPI variable ----------------------------------
if api_docs=='True':
    app = FastAPI(title='SmartPacer Results Combining (RC) API',
        version='0.5.0', include_in_schema=True, docs_url=None, redoc_url=None)
else:
    app = FastAPI(title='SmartPacer Results Combining (RC) API',
        version='0.5.0', include_in_schema=False, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Routers inclusion from src directory ===============
app.include_router(apirouter)

# ================= Invalid Request Exception Handling =================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(make_operation_outcome('invalid', str(exc)), status_code=400)

#----------------------- Custom OpenAPI -------------------------------
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="SmartPacer Results Combining (RC) API",
        version="0.5.0",
        description="This is a custom Open API Schema to align with SmartPacer's RC API.",
        routes=app.routes,
    )
    #openapi_schema["info"]["x-logo"] = {
        #"url": "https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"
    #}
    openapi_schema["servers"] = [{"url":"https://gt-apps.hdap.gatech.edu/rc-api/"}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
def startup_event():
    # Check for private key and known hosts in secrets
      # Set these (required for both hook clone and startup clone)
    # setup_keys()
    # Check for repo ssh env
      # if present do startup
      # pre_load_scripts(ssh_url_from_env)
    print("Checking for initial files to load...")
    knowledgebase_repo = os.environ.get("KNOWLEDGEBASE_REPO", "")
    if knowledgebase_repo:
        cloneRepoToTempFolder(knowledgebase_repo)

if api_docs=='True':
    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url="/rc-api/"+app.openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url="static/swagger-ui-bundle.js",
            swagger_css_url="static/swagger-ui.css",
        )

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html():
        return get_redoc_html(
            openapi_url="/rc-api/"+app.openapi_url,
            title=app.title + " - ReDoc",
            redoc_js_url="static/redoc.standalone.js",
        )


