"""Add metadata columns to jobs table

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # From authenticate step
    op.add_column("jobs", sa.Column("document_type",   sa.String(64),  nullable=True))
    op.add_column("jobs", sa.Column("authentic",       sa.Boolean(),   nullable=True))
    op.add_column("jobs", sa.Column("auth_confidence", sa.Float(),     nullable=True))
    # From redact step
    op.add_column("jobs", sa.Column("entity_count",    sa.Integer(),   nullable=True))
    op.add_column("jobs", sa.Column("pii_types_found", sa.String(256), nullable=True))
    # From extract step
    op.add_column("jobs", sa.Column("confidence_score",sa.Float(),     nullable=True))
    # After human review
    op.add_column("jobs", sa.Column("review_status",   sa.String(32),  nullable=True))

    # Add NEEDS_REVIEW to the job_status enum
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'NEEDS_REVIEW'")


def downgrade() -> None:
    op.drop_column("jobs", "review_status")
    op.drop_column("jobs", "confidence_score")
    op.drop_column("jobs", "pii_types_found")
    op.drop_column("jobs", "entity_count")
    op.drop_column("jobs", "auth_confidence")
    op.drop_column("jobs", "authentic")
    op.drop_column("jobs", "document_type")
