import os
import gridfs
from dotenv import load_dotenv
from pymongo import MongoClient

if os.environ.get('IS_DOCKER')=='False':
    print ("Setting loadded from docker-compose")
else:
    load_dotenv()
    print ("Setting loadded from local env")

# ================= Creating necessary variables ========================
#------------------ Token, authentication variables ---------------------
APP_NAME = os.environ["PROJECT"]


#----------------- Database variables (MongoDB) --------------------------
client = MongoClient( "mongodb://mongodb/" + os.environ["PROJECT"])
db = client[os.environ["PROJECT"]]
file_storage = gridfs.GridFS(db)


#-----------FORM Database ----------------------------
formsclient = MongoClient(os.environ["MONGO_FORM_URL"])
formsdb = formsclient["SmartChartForms"]

#-----------CQF RULER R4 Endpoint------------------------------------------
cqfr4_fhir = os.environ["CQF_RULER_R4"]