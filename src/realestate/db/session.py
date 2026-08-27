"""Engine and session factory, built from Settings.database_url."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from realestate.config import get_settings


def make_engine(url: str | None = None, **kwargs: object) -> Engine:
    url = url or get_settings().database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
