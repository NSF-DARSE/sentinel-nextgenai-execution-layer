from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel
from uuid import UUID


class DocumentCreate(BaseModel):
    filename: str
    content_type: str = "application/pdf"


class DocumentCreateResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str


class JobStatusResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

class DocumentUploadResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str
    s3_key: str


class ReviewQueueItem(BaseModel):
    job_id: UUID
    document_id: UUID
    filename: str
    confidence_score: float | None = None
    authentic: bool | None = None
    auth_confidence: float | None = None
    entity_count: int | None = None
    pii_types_found: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None


class ReviewDecision(BaseModel):
    decision: str  # "approved" or "rejected"
    notes: str | None = None


class ReviewResponse(BaseModel):
    job_id: UUID
    status: str
    review_status: str