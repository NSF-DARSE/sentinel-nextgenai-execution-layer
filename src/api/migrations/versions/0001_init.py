from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # IMPORTANT: create_type=False prevents SQLAlchemy from auto-emitting CREATE TYPE
    job_status = postgresql.ENUM(
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "NEEDS_REVIEW",
        name="job_status",
        create_type=False,
    )
    # Create enum only if missing
    job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
            sa.Column("status", job_status, nullable=False, server_default=sa.text("'QUEUED'")),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_document_id", table_name="jobs")
    op.drop_table("jobs")

    job_status = postgresql.ENUM(
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "NEEDS_REVIEW",
        name="job_status",
        create_type=False,
    )
    job_status.drop(op.get_bind(), checkfirst=True)

    op.drop_table("documents")