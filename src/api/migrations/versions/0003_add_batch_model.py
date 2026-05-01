"""add_batch_model

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-30 23:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create batches table
    op.create_table(
        'batches',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. Add batch_id to documents
    op.add_column('documents', sa.Column('batch_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_documents_batch_id'), 'documents', ['batch_id'], unique=False)
    op.create_foreign_key(None, 'documents', 'batches', ['batch_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(None, 'documents', type_='foreignkey')
    op.drop_index(op.f('ix_documents_batch_id'), table_name='documents')
    op.drop_column('documents', 'batch_id')
    op.drop_table('batches')
