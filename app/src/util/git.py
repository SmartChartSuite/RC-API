import tempfile
from git import Repo
import os
import logging

logger = logging.getLogger("rcapi.git")

# URL can be either HTTPS or Git SSH, the underlying git command does not change. If Git SSH, must provide appropriate keys.
def cloneRepoToTempFolder(clone_url):
    logger.info("Attempting to clone repository.")
    with tempfile.TemporaryDirectory() as temp:
        repo: Repo = Repo.clone_from(clone_url, temp)
        print(repo.description)
        print(temp)
        for filename in os.listdir(temp):
            # TODO: Placeholder to demonstrate functionality, switch to nlpql/cql and handle as needed.
            if filename.endswith(".cql"):
                filepath = os.path.join(temp, filename)
                parseLibrary(filepath)


def parseLibrary(filepath):
    logger.info("Parsing library...")
    with open(filepath) as f:
        lines = f.readlines()
        print(lines)