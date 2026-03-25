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