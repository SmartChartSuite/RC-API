"""Webhook for Knowledge Base Integration"""
import logging

from fastapi import APIRouter, Request

from src.util.git import clone_repo_to_temp_folder

logger = logging.getLogger("rcapi.routers.webhook")

apirouter = APIRouter()


@apirouter.post("/webhook")
async def webhook(request: Request):
    """Webhook endpoint function"""
    message = await request.json()
    # TODO: Determine whether to use SSH or HTTPS from config
    clone_url = message["repository"]["clone_url"]
    ssh_url = message["repository"]["ssh_url"]
    logger.info(f"CLONE URL: {clone_url}")
    logger.info(f"SSH URL: {ssh_url}")
    clone_repo_to_temp_folder(ssh_url)
    # TODO: Add Error Handling
    return "Acknowledged"
