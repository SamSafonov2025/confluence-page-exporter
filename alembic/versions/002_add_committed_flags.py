"""Add committed_to_git flags and committed_files table

Revision ID: 002
Revises: 001
Create Date: 2026-02-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Checkbox 2: committed to git
    op.add_column('exported_page_versions',
                  sa.Column('committed_to_git', sa.Boolean(),
                            server_default='false'))
    op.add_column('exported_page_versions',
                  sa.Column('committed_at', sa.DateTime()))

    op.add_column('exported_attachments',
                  sa.Column('committed_to_git', sa.Boolean(),
                            server_default='false'))
    op.add_column('exported_attachments',
                  sa.Column('committed_at', sa.DateTime()))

    # General file tracking for git_versioner
    op.create_table(
        'committed_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_path', sa.String(), nullable=False, unique=True),
        sa.Column('committed_at', sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table('committed_files')
    op.drop_column('exported_attachments', 'committed_at')
    op.drop_column('exported_attachments', 'committed_to_git')
    op.drop_column('exported_page_versions', 'committed_at')
    op.drop_column('exported_page_versions', 'committed_to_git')
