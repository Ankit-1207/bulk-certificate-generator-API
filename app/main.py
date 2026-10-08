"""FastAPI application entry point.  Run with:  uvicorn app.main:app --reload"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import settings
from app.database import init_db
from app.services.jobs import resume_unfinished_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    # In-process background tasks don't survive a restart; pick up anything left over.
    if settings.resume_jobs_on_startup:
        threading.Thread(target=resume_unfinished_jobs, name="resume-jobs", daemon=True).start()
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    version="1.0.0",
    description="Submit a list of recipients, track generation progress, download certificates.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
