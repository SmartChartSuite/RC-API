"""Embedded durable consumer for database-backed batch jobs."""

import asyncio
from contextlib import suppress
import os
import socket
import uuid

from loguru import logger

from src.services.job_orchestrator import LeaseLostError, run_batch_job
from src.services.job_state import (
    ClaimedBatchJob,
    claim_next_batch_job,
    fail_batch_job_attempt,
    recover_expired_batch_job_leases,
    release_batch_job_attempt,
)
from src.util.settings import (
    batch_job_retry_delay_seconds,
    batch_worker_lease_seconds,
    batch_worker_poll_interval_seconds,
)


class EmbeddedBatchWorker:
    """Claim and execute durable batch jobs inside the API process."""

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._active_claim: ClaimedBatchJob | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=f"batch-worker:{self.worker_id}")
        logger.info(f"Started embedded batch worker {self.worker_id}")

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        logger.info(f"Stopped embedded batch worker {self.worker_id}")

    async def _wait_for_poll(self) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=batch_worker_poll_interval_seconds,
            )

    async def _release_active_claim(self) -> None:
        claim = self._active_claim
        if claim is None:
            return
        await asyncio.shield(
            asyncio.to_thread(
                release_batch_job_attempt,
                claim.batch_id,
                claim.worker_id,
                claim.attempt_count,
            )
        )

    async def _execute(self, claim: ClaimedBatchJob) -> None:
        if not claim.questionnaire_id:
            raise RuntimeError("Persisted batch job is missing questionnaire_id")
        await run_batch_job(
            claim.batch_id,
            claim.patient_id,
            claim.job_package,
            claim.questionnaire_id,
            claim.job_package_version,
            claim.requested_jobs,
            worker_id=claim.worker_id,
            attempt_count=claim.attempt_count,
            prior_bundle=claim.result_bundle,
        )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(
                    recover_expired_batch_job_leases,
                    batch_job_retry_delay_seconds,
                )
                claim = await asyncio.to_thread(
                    claim_next_batch_job,
                    self.worker_id,
                    batch_worker_lease_seconds,
                )
                if claim is None:
                    await self._wait_for_poll()
                    continue

                self._active_claim = claim
                try:
                    await self._execute(claim)
                except asyncio.CancelledError:
                    await self._release_active_claim()
                    raise
                except LeaseLostError:
                    logger.warning(f"Worker {self.worker_id} stopped batch {claim.batch_id} after losing attempt {claim.attempt_count}")
                except Exception as exc:
                    await asyncio.to_thread(
                        fail_batch_job_attempt,
                        claim.batch_id,
                        claim.worker_id,
                        claim.attempt_count,
                        str(exc),
                        batch_job_retry_delay_seconds,
                    )
                finally:
                    self._active_claim = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"Embedded batch worker {self.worker_id} loop failed: {exc}")
                await self._wait_for_poll()
