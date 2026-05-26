import importlib

import sqlalchemy
from sqlalchemy import MetaData

import src.services.job_state as job_state
import src.util.settings as settings


def test_metadata_schema_uses_non_sqlite_schema():
    assert job_state._metadata_schema("postgresql+psycopg://user:pass@host/db", "custom_schema") == "custom_schema"
    assert job_state._metadata_schema("sqlite+pysqlite:///rcapi_jobs.sqlite", "custom_schema") is None


def test_models_apply_configured_schema_for_named_schema_backends(monkeypatch):
    original_connection_string = settings.db_connection_string
    original_schema = settings.db_schema
    executed_statements = []
    create_all_calls = []

    class _FakeConnection:
        def execute(self, statement):
            executed_statements.append(statement)

    class _FakeBegin:
        def __enter__(self):
            return _FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBegin()

    monkeypatch.setattr(settings, "db_connection_string", "postgresql+psycopg://user:pass@host/db")
    monkeypatch.setattr(settings, "db_schema", "custom_schema")
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: _FakeEngine())
    monkeypatch.setattr(MetaData, "create_all", lambda self, engine: create_all_calls.append((self.schema, engine)))

    reloaded = importlib.reload(job_state)

    assert reloaded.Base.metadata.schema == "custom_schema"
    assert reloaded.BatchJobs.__table__.schema == "custom_schema"
    assert reloaded.Jobs.__table__.schema == "custom_schema"
    assert reloaded.QuestionnaireResponses.__table__.schema == "custom_schema"
    assert len(executed_statements) == 1
    assert getattr(executed_statements[0], "element", None) == "custom_schema"
    assert create_all_calls == [("custom_schema", reloaded.db_engine)]

    monkeypatch.setattr(settings, "db_connection_string", original_connection_string)
    monkeypatch.setattr(settings, "db_schema", original_schema)
    importlib.reload(reloaded)
