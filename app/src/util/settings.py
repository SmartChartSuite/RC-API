import os
import gridfs
from pymongo import MongoClient

# ================= Creating necessary variables from Secrets ========================

cqfr4_fhir = os.environ["CQF_RULER_R4"]
external_fhir_server_url = os.environ["EXTERNAL_FHIR_SERVER_URL"]
external_fhir_server_auth = os.environ["EXTERNAL_FHIR_SERVER_AUTH"]