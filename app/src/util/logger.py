import logging
from ..models.models import QuestionsJSON, bundle_template, CustomFormatter
from ..util.settings import log_level

logger = logging.getLogger("rcapi")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

if log_level == "DEBUG":
    logger.setLevel(logging.DEBUG)
    ch.setLevel(logging.DEBUG)