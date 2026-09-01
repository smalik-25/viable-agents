"""The SQLAlchemy declarative base. Lives outside ``kernel/`` on purpose.

Keeping SQLAlchemy out of the strict-checked kernel package removes the main mypy
friction source and keeps the kernel importing nothing heavy. The naming
convention is load-bearing, not cosmetic: without it Alembic autogenerate cannot
detect anonymously named constraints and emits phantom drop/create churn on every
revision, which erodes trust in the migrations exactly when later phases add tables.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, MetaData, Numeric, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this as a class attribute, not mutable state
        dict[str, Any]: JSONB,
        # NUMERIC(14,8): cost columns need eight fractional digits because a
        # cache-read token at 0.1x can cost fractions of a microcent.
        Decimal: Numeric(14, 8),
        uuid.UUID: Uuid(as_uuid=True),
        dt.datetime: DateTime(timezone=True),
    }
