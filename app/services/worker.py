"""Dedicated, bounded worker pool for certificate generation.

Why not FastAPI BackgroundTasks? Sync background tasks run on the same thread
pool that serves sync endpoints, so a few large jobs could starve status
requests. A separate pool with a fixed size keeps the API responsive and caps
how much generation work runs at once; extra jobs simply wait in the queue.
"""

import logging
from concurrent.futures import Future, ThreadPoolExecutor

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self) -> None:
        self._executor: ThreadPoolExecutor | None = None

    def start(self, threads: int) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="cert-worker")

    def submit(self, job_id: str) -> Future:
        if self._executor is None:
            raise RuntimeError("Worker not started")
        from app.services.jobs import process_job

        future = self._executor.submit(process_job, job_id)
        future.add_done_callback(lambda f: self._log_crash(job_id, f))
        return future

    def shutdown(self) -> None:
        """Stop accepting work. Queued-but-unstarted jobs stay `pending` in the
        database and are picked up again on the next startup."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @staticmethod
    def _log_crash(job_id: str, future: Future) -> None:
        # process_job handles its own errors; this only catches truly unexpected ones
        # (otherwise an exception inside a Future is silently swallowed).
        if not future.cancelled() and future.exception() is not None:
            logger.error("Worker crashed on job %s", job_id, exc_info=future.exception())


worker = JobWorker()
