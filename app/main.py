"""FastAPI application entry point.  Run with:  uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app import database
from app.api import public_router, router
from app.config import settings
from app.database import SessionLocal, init_db
from app.services.jobs import requeue_interrupted_jobs
from app.services.worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    worker.start(settings.worker_threads)

    if settings.resume_jobs_on_startup:
        # The worker pool is in-process, so queued work is lost on shutdown; the
        # database is the durable record, and we re-queue from it here.
        with SessionLocal() as db:
            job_ids = requeue_interrupted_jobs(db)
        for job_id in job_ids:
            logger.info("Re-queuing unfinished job %s", job_id)
            worker.submit(job_id)

    yield
    worker.shutdown()


app = FastAPI(
    title="Bulk Certificate Generator",
    version="1.0.0",
    description=(
        "Submit a list of recipients in one request, track generation progress, "
        "and download the generated PDF certificates individually or as a ZIP."
    ),
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(public_router)


@app.get("/health", tags=["health"])
def health():
    with database.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
