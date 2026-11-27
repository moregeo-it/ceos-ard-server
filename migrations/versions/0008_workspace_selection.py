"""The PFS list becomes the selection of any workspace type, e.g. a document

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("pfs", new_column_name="selection", existing_type=sa.JSON(), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("selection", new_column_name="pfs", existing_type=sa.JSON(), existing_nullable=True)
