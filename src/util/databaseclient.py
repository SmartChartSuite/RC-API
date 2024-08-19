"""Handler for all database functions"""

import logging
from datetime import datetime

from psycopg2 import DataError
from sqlalchemy import JSON, Connection, Executable, ForeignKey, Inspector, MetaData, Row, create_engine, inspect, text
from sqlalchemy.engine import Compiled, Engine
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import DDLElement, DefaultGenerator
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.functions import FunctionElement

from src.util.settings import db_connection_string, db_schema

logger: logging.Logger = logging.getLogger("rcapi.util.databaseclient")
db_engine: Engine = create_engine(db_connection_string)


def startup_connect() -> None:
    """Connect to databases"""

    logger.info(f"Using DB of type <{db_connection_string.split(':')[0]}> and schema <{db_schema}>")

    logger.info("Connecting to database...")

    logger.info("Checking if all required tables exist...")
    conn: Connection = db_engine.connect()
    existence = check_existence_of_tables(conn=conn)
    if all(existence.values()):
        logger.info("Connected to database and all required tables exist!")
    else:
        missing_tables = [pair[0] for pair in list(existence.items()) if not pair[1]]
        logger.error(f"The following required tables are missing from your database: {missing_tables}")
        raise Exception(f"Missing required database tables {missing_tables}")
    conn.close()


def create_connection(db_conn_string: str, return_engine: bool = False) -> Engine | Connection | str:
    """Create a connection and return either the connection or the engine"""
    engine: Engine = create_engine(db_conn_string)
    try:
        connection: Connection = engine.connect()
        if return_engine:
            return engine
        else:
            return connection
    except OperationalError as e:
        return str(e)


def get_next_id_from_table(engine: Engine, table: str, column: str) -> int:
    with Session(engine) as session:
        stmt: str = f"SELECT {column} id FROM {table} order by id desc limit 1;"
        result: Row | None = session.execute(text(stmt)).first()
        if not result:
            last_id: int = 0
        else:
            last_id = result[0]
    return last_id + 1


def save_object(engine: Engine, object) -> str | None:
    try:
        with Session(engine) as session:
            session.add(object)
            session.commit()
    except (Exception, DataError) as e:
        return str(e)
    return None


def execute_orm_query(engine: Engine, stmt: Executable) -> list:
    """Execute an ORM SQLAlchemy Query to return a list of objects versus a list of Rows"""
    results: list = []
    with Session(engine) as session:
        try:
            if " join " in str(stmt).lower():
                for row in session.execute(stmt):
                    results.append(row)
            else:
                for row in session.scalars(stmt):
                    results.append(row)
        except ProgrammingError as excep:
            logger.error(f"An exception occurred while trying to run a query: {excep}")
    return results


def execute_orm_no_return(engine: Engine, stmt: ClauseElement | FunctionElement | DDLElement | DefaultGenerator | Compiled) -> None | dict:
    """Executes an ORM SQLAlchemy statement that doesnt return rows (but could return an error)"""
    try:
        with engine.connect() as conn:
            conn.execute(stmt)  # type: ignore
            conn.commit()
    except IntegrityError as ie:
        logger.error("There was an integrity error when trying to perform the operation. See below for exception:")
        logger.error(ie)
        detail_str: str = str(ie.__dict__["orig"]).split(":")[-1].lstrip(" ").rstrip("\n")
        return {"detail": f"{detail_str} For now, you must manually delete this from the database."}


def sqlalc_to_dict(object) -> dict:
    return {col.name: getattr(object, col.name) for col in object.__table__.columns if getattr(object, col.name)}


class BaseRCAPI(DeclarativeBase):
    __allow_unmapped__ = True
    type_annotation_map = {dict: JSON}
    metadata = MetaData(schema=db_schema)


class BatchJobs(BaseRCAPI):
    __tablename__ = "batch_jobs"

    batch_job_id: Mapped[str] = mapped_column(primary_key=True)
    batch_job: Mapped[dict]


class Jobs(BaseRCAPI):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(primary_key=True)
    patient_id_type: Mapped[str]
    patient_id: Mapped[str]
    job_package: Mapped[str]
    job_start_datetime: Mapped[datetime]
    job_status: Mapped[str]
    job: Mapped[dict | None]
    parent_batch_job_id: Mapped[str | None] = mapped_column(ForeignKey("batch_jobs.batch_job_id", ondelete="CASCADE", onupdate="CASCADE"))


def check_existence_of_tables(conn: Connection) -> dict[str, bool]:
    """
    Uses a list of classes defined in this file to determine that all required tables exist in the target database
    """

    table_list: list[type[BaseRCAPI]] = [BatchJobs, Jobs]
    output_dict: dict[str, bool] = {}

    insp: Inspector = inspect(conn)

    for table in table_list:
        tablename = table.__tablename__
        output_dict[tablename] = insp.has_table(table_name=tablename)

    if not all(output_dict.values()):
        logger.info("Creating tables if missing...")
        BaseRCAPI.metadata.create_all(conn)
        conn.commit()
        for table in table_list:
            tablename = table.__tablename__
            output_dict[tablename] = True

    return output_dict
