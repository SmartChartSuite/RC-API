import sys

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from src.routers.batchjob import router as batchjob_router
from src.routers.group import router as group_router
from src.routers.jobpackage import router as jobpackage_router
from src.routers.library import router as library_router
from src.routers.response import router as response_router
from src.services.errorhandler import make_operation_outcome
from src.util.settings import api_docs, log_level

logger.remove()
logger.add(sys.stderr, level=log_level, colorize=True)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RC-API",
    description="SmartChart Suite Results Combining API — v1.0",
    version="1.0.0",
    docs_url="/docs" if api_docs.lower() != "false" else None,
    redoc_url="/redoc" if api_docs.lower() != "false" else None,
)

# ── Exception handlers ─────────────────────────────────────────────────────────


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return FHIR OperationOutcome for Pydantic validation failures (e.g. missing params)."""
    detail = "; ".join(err.get("msg", str(err)) for err in exc.errors())
    return JSONResponse(
        status_code=400,
        content=make_operation_outcome("structure", detail),
    )


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(batchjob_router)
app.include_router(jobpackage_router)
app.include_router(group_router)
app.include_router(library_router)
app.include_router(response_router)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["Health"])
async def health():
    """Basic health check."""
    from src.util.settings import config_errors

    return {
        "status": "ok" if not config_errors else "degraded",
        "version": "1.0.0",
        "configErrors": list(config_errors.keys()) if config_errors else [],
    }
