from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    jobs: Mapped[list["Job"]] = relationship(back_populates="document")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), index=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_type=False),
        default=JobStatus.QUEUED,
    )
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Populated by the authenticate step
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authentic: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    auth_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Populated by the redact step
    entity_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pii_types_found: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Populated by the extract step
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Set after human review (NEEDS_REVIEW jobs)
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    document: Mapped["Document"] = relationship(back_populates="jobs")