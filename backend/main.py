from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.session import engine
from db.bootstrap import run_schema_bootstrap
from routes.chat import router as chat_router

run_schema_bootstrap(engine)

app = FastAPI(title="Voya AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)

@app.get("/health")
def health_check():
    return {"status": "ok", "project": "Voya AI"}