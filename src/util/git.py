import tempfile
from git import Repo
import os
import logging

from ..services.libraryhandler import (create_cql, create_nlpql)

logger = logging.getLogger('rcapi.util.git')

# URL can be either HTTPS or Git SSH, the underlying git command does not change. If Git SSH, must provide appropriate keys.
def clone_repo_to_temp_folder(clone_url):
    logger.info("Attempting to clone repository.")
    with tempfile.TemporaryDirectory() as temp:
        repo: Repo = Repo.clone_from(clone_url, temp)
        logger.info(f"Repository Description: {repo.description}")
        for dirpath, dirs, files in os.walk(temp):
            for filename in files:
                if filename.endswith(".cql"):
                    filepath = os.path.join(dirpath, filename)
                    logger.info(f"Found CQL file at: {filepath}")
                    parse_cql_library(filepath)
                elif filename.endswith(".nlpql"):
                    filepath = os.path.join(dirpath, filename)
                    logger.info(f"Found NLPQL file at: {filepath}")
                    parse_nlpql_library(filepath)


def parse_cql_library(filepath):
    logger.info("Parsing CQL library...")
    with open(filepath) as f:
        body = f.read()
    create_cql(body)

def parse_nlpql_library(filepath):
    logger.info("Parsing NLPQL library...")
    with open(filepath) as f:
        body = f.read()
    create_nlpql(body)