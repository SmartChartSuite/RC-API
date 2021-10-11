import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.routers.forms import formsrouter


#------------------ FastAPI variable ----------------------------------
if os.environ.get('INCLUDE_SCHEME')=='True':
    app = FastAPI(title=os.environ["PROJECT"],  
        version=os.environ["VERSION"], include_in_schema=True)
else:         
    app = FastAPI(title=os.environ["PROJECT"],  
        version=os.environ["VERSION"], include_in_schema=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Routers inclusion from src directory ===============
app.include_router(formsrouter)