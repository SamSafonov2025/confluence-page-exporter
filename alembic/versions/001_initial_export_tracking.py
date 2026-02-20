"""Initial: export tracking tables

Revision ID: 001
Revises:
Create Date: 2026-02-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'exported_page_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('page_id', sa.String(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('page_title', sa.String()),
        sa.Column('export_format', sa.String()),
        sa.Column('exported_at', sa.DateTime()),
        sa.UniqueConstraint('page_id', 'version_number', 'export_format',
                            name='uq_page_version_format'),
    )
    op.create_table(
        'exported_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('page_id', sa.String(), nullable=False),
        sa.Column('attachment_id', sa.String(), nullable=False),
        sa.Column('attachment_title', sa.String()),
        sa.Column('attachment_version', sa.Integer()),
        sa.Column('exported_at', sa.DateTime()),
        sa.UniqueConstraint('page_id', 'attachment_id',
                            name='uq_page_attachment'),
    )


def downgrade() -> None:
    op.drop_table('exported_attachments')
    op.drop_table('exported_page_versions')
