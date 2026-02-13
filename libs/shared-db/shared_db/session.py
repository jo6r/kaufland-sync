"""Database session management."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from shared_db.config import get_db_dsn

# Create engine with MariaDB support
_engine = create_engine(
    get_db_dsn(),
    pool_pre_ping=True,
    echo=False,
    # MariaDB specific settings
    connect_args={"charset": "utf8mb4"},
)

# Session factory
session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

# Export engine
engine = _engine


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Context manager for database session.
    
    Provides a database session with automatic commit on success
    and rollback on exception.
    
    Yields:
        SQLAlchemy Session object.
        
    Example:
        with session_scope() as session:
            # Use session here
            pass
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
