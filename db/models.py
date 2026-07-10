from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"

    number: Mapped[str] = mapped_column(String(20), primary_key=True)
    short_description: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(String(100))
    stage: Mapped[Optional[str]] = mapped_column(String(100))
    current_stage: Mapped[Optional[str]] = mapped_column(String(200))
    # Stored as strings — ServiceNow date formats vary, parse later if needed
    opened_at: Mapped[Optional[str]] = mapped_column(String(50))
    updated_at: Mapped[Optional[str]] = mapped_column(String(50))
    requested_by: Mapped[Optional[str]] = mapped_column(String(200))
    requested_for: Mapped[Optional[str]] = mapped_column(String(200))
    groups: Mapped[Optional[str]] = mapped_column(Text)
    group_name: Mapped[Optional[str]] = mapped_column(Text)
    application: Mapped[Optional[str]] = mapped_column(Text)
    expected_delivery: Mapped[Optional[str]] = mapped_column(String(100))
    category: Mapped[Optional[str]] = mapped_column(String(200))
    request_number: Mapped[Optional[str]] = mapped_column(String(20))
    detail_url: Mapped[Optional[str]] = mapped_column(Text)
    raw_fields: Mapped[Optional[dict]] = mapped_column(JSONB)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
