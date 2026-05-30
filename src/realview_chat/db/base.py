from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from realview_chat.db.config import get_database_url


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_engine(get_database_url(), echo=False, future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)