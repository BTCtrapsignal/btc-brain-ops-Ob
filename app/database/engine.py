"""
database/engine.py

SQLite engine + session factory.
Database file path is controlled by the DB_PATH env var (default: ./btc_brain_ops.db).
"""

import os
from sqlmodel import SQLModel, Session, create_engine

DB_PATH = os.getenv("DB_PATH", "btc_brain_ops.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# connect_args required for SQLite + FastAPI (multi-thread safety)
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency — yields a database session per request."""
    with Session(engine) as session:
        yield session
