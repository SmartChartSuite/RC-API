import tempfile
from git import Repo
import os

# URL can be either HTTPS or Git SSH, the underlying git command does not change. If Git SSH, must provide appropriate keys.
def cloneRepoToTempFolder(clone_url):
    with tempfile.TemporaryDirectory() as temp:
        repo: Repo = Repo.clone_from(clone_url, temp)
        print(repo.description)
        print(temp)
        for filename in os.listdir(temp):
            # TODO: Placeholder to demonstrate functionality, switch to nlpql/cql and handle as needed.
            if filename.endswith(".md"):
                filepath = os.path.join(temp, filename)
                with open(filepath) as f:
                    lines = f.readlines()
                    print(lines)