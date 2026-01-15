from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import asyncio

@dataclass
class JobState:
    job_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    status: str = "running"  # running | done | error
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

JOBS: Dict[str, JobState] = {}

def get_job(job_id: str) -> Optional[JobState]:
    return JOBS.get(job_id)

def create_job(job_id: str, options: Optional[Dict[str, Any]] = None) -> JobState:
    job = JobState(job_id=job_id, options=options)
    JOBS[job_id] = job
    return job

def finish_job(job_id: str, result: Dict[str, Any]) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    job.status = "done"
    job.result = result

def fail_job(job_id: str, error: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    job.status = "error"
    job.error = error
