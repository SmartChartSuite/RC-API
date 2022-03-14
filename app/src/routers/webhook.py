from fastapi import APIRouter, Body, Request

from ..util.git import clone_repo_to_temp_folder
import logging
logger = logging.getLogger('rcapi.routers.webhook')

apirouter = APIRouter()

@apirouter.post("/webhook")
def webhook(request: Request):
    message = request.json
    # TODO: Determine whether to use SSH or HTTPS from config
    clone_url = message['repository']['clone_url']
    ssh_url = message['repository']['ssh_url']
    print("CLONE URL: ", clone_url)
    print("SSH URL: ", ssh_url)
    clone_repo_to_temp_folder(ssh_url)
    # TODO: Add Error Handling
    return "Acknowledged"