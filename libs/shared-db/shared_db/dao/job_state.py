"""DAO for job_state operations."""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from shared_db.models import JobState


def get_last_run(session: Session, job_name: str) -> Optional[datetime]:
    """
    Get last run timestamp for a job.
    
    Args:
        session: SQLAlchemy session
        job_name: Name of the job
        
    Returns:
        Last run datetime or None if job has never run
    """
    job_state = session.query(JobState).filter(
        JobState.job_name == job_name
    ).first()
    
    if job_state:
        return job_state.last_run_at
    return None


def set_last_run(session: Session, job_name: str, ts: datetime) -> None:
    """
    Set last run timestamp for a job.
    
    Args:
        session: SQLAlchemy session
        job_name: Name of the job
        ts: Timestamp to set
    """
    job_state = session.query(JobState).filter(
        JobState.job_name == job_name
    ).first()
    
    if job_state:
        job_state.last_run_at = ts
    else:
        new_job_state = JobState(
            job_name=job_name,
            last_run_at=ts,
        )
        session.add(new_job_state)
