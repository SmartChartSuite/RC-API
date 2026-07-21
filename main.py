from contextlib import asynccontextmanager
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from src.routers.batchjob import router as batchjob_router
from src.routers.config import router as config_router
from src.routers.group import router as group_router
from src.routers.jobpackage import router as jobpackage_router
from src.routers.library import router as library_router
from src.routers.patient import router as patient_router
from src.routers.response import router as response_router
from src.services.errorhandler import make_operation_outcome
from src.services.prompt_loader import initialize_prompt_source
from src.util.settings import log_level, root_path

LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[source_name]}</cyan>:<cyan>{extra[source_function]}</cyan>:<cyan>{extra[source_line]}</cyan> - <level>{message}</level>"
_NOISY_LOGGER_LEVELS = {"httpcore": logging.WARNING, "httpx": logging.WARNING, "LiteLLM": logging.WARNING, "urllib3": logging.WARNING, "openai": logging.WARNING, "asyncio": logging.WARNING}


def _patch_log_record(record) -> None:
    record["extra"].setdefault("source_name", record["name"])
    record["extra"].setdefault("source_function", record["function"])
    record["extra"].setdefault("source_line", record["line"])


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        logger.bind(
            source_name=record.name,
            source_function=record.funcName,
            source_line=record.lineno,
        ).opt(exception=record.exc_info).log(level, record.getMessage())


def _configure_noisy_loggers() -> None:
    for logger_name, level in _NOISY_LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(level)


def configure_logging() -> None:
    logger.remove()
    logger.configure(patcher=_patch_log_record)
    logger.add(sys.stderr, level=log_level, colorize=True, format=LOG_FORMAT)

    intercept_handler = InterceptHandler()
    logging.basicConfig(handlers=[intercept_handler], level=log_level, force=True)

    for logger_name in ("hypercorn.access", "hypercorn.error", "hypercorn", "LiteLLM"):
        stdlib_logger = logging.getLogger(logger_name)
        stdlib_logger.handlers = [intercept_handler]
        stdlib_logger.propagate = False

    _configure_noisy_loggers()


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_prompt_source()
    yield


# App
app = FastAPI(title="RC-API", description="SmartChart Suite Results Combining API — v1.0", version="1.0.0", root_path=root_path, lifespan=lifespan)

# CORS stuff
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return FHIR OperationOutcome for Pydantic validation failures (e.g. missing params)."""
    detail = "; ".join(err.get("msg", str(err)) for err in exc.errors())
    return JSONResponse(
        status_code=400,
        content=make_operation_outcome("structure", detail),
    )


# Routers
app.include_router(batchjob_router)
app.include_router(jobpackage_router)
app.include_router(config_router)
app.include_router(group_router)
app.include_router(library_router)
app.include_router(patient_router)
app.include_router(response_router)


# Health
@app.get("/health", tags=["Health"])
async def health():
    """Basic health check."""
    from src.util.settings import config_errors

    return {
        "status": "ok" if not config_errors else "degraded",
        "version": "1.0.0",
        "configErrors": list(config_errors.keys()) if config_errors else [],
    }
