from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from uuid import UUID


class DocumentCreate(BaseModel):
    filename: str
    content_type: str = "application/pdf"


class DocumentCreateResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str


class BatchCreateResponse(BaseModel):
    batch_id: UUID
    jobs: list[DocumentCreateResponse]


class JobStatusResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    filename: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BatchStatusResponse(BaseModel):
    batch_id: UUID
    status: str  # aggregate status: RUNNING, SUCCEEDED, etc.
    jobs: list[JobStatusResponse]
    created_at: datetime

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
    decision: Literal["approved", "rejected"]
    notes: str | None = Field(None, max_length=2048)


class ReviewResponse(BaseModel):
    job_id: UUID
    status: str
    review_status: str


class PerDocumentSummary(BaseModel):
    job_id: UUID
    filename: str
    status: str
    document_type: str | None = None
    score: float | None = None
    flags: list[str] = []


class BatchDecisionResponse(BaseModel):
    batch_id: UUID
    status: str
    application_profile: dict | None = None
    combined_score: dict | None = None
    combined_authenticity: dict | None = None
    per_document: list[PerDocumentSummary] = []