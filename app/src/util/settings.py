import os
import gridfs
from pymongo import MongoClient

# ================= Creating necessary variables from Secrets ========================
#---------------------------CQF RULER R4 Endpoint-------------------------------------
cqfr4_fhir = os.environ["CQF_RULER_R4"]