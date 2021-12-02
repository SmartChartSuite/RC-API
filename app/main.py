import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)

from src.routers.forms import formsrouter


#------------------ FastAPI variable ----------------------------------
if os.environ.get('INCLUDE_SCHEME')=='True':
    app = FastAPI(title=os.environ["PROJECT"],
        version=os.environ["VERSION"], include_in_schema=True, docs_url=None, redoc_url=None)
else:
    app = FastAPI(title=os.environ["PROJECT"],
        version=os.environ["VERSION"], include_in_schema=False, docs_url=None, redoc_url=None)

# TODO: check with ellie to see if this should be in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Routers inclusion from src directory ===============
app.include_router(formsrouter)

#----------------------- Custom OpenAPI -------------------------------
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="FormsAPI",
        version="0.1.0",
        description="This is a very custom OpenAPI schema",
        routes=app.routes,
    )
    openapi_schema["info"]["x-logo"] = {
        "url": "https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
app.mount("/static", StaticFiles(directory="static"), name="static")

# TODO: Switch to env vars
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
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
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url="static/redoc.standalone.js",
    )