'''Git module for knowledge base integration support'''
import os
import logging
import tempfile
from git import Repo


from ..services.libraryhandler import (create_cql, create_nlpql)

logger = logging.getLogger('rcapi.util.git')


# URL can be either HTTPS or Git SSH, the underlying git command does not change. If Git SSH, must provide appropriate keys.
def clone_repo_to_temp_folder(clone_url):
    '''Clones knowledgebase repo to a temporary folder'''
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
                else:
                    print(dirs)


def parse_cql_library(filepath):
    '''Parse CQL Library'''
    logger.info("Parsing CQL library...")
    with open(filepath, encoding='utf-8') as temp_file:
        body = temp_file.read()
    create_cql(body)


def parse_nlpql_library(filepath):
    '''Parse NLPQL Library'''
    logger.info("Parsing NLPQL library...")
    with open(filepath, encoding='utf-8') as temp_file:
        body = temp_file.read()
    create_nlpql(body)
