"""A user is unique per provider and external id; pull request number as integer, longer titles and descriptions

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("title", existing_type=sa.String(length=50), type_=sa.String(length=100), existing_nullable=False)
        batch_op.alter_column("description", existing_type=sa.String(length=500), type_=sa.String(length=1000), existing_nullable=True)
        batch_op.alter_column("pull_request_number", existing_type=sa.String(), type_=sa.Integer(), existing_nullable=True)

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ix_users_email")
        batch_op.drop_index("ix_users_external_id")
        batch_op.drop_index("ix_users_username")
        batch_op.create_unique_constraint("uq_users_identity_provider_external_id", ["identity_provider", "external_id"])


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_identity_provider_external_id", type_="unique")
        batch_op.create_index("ix_users_username", ["username"], unique=True)
        batch_op.create_index("ix_users_external_id", ["external_id"], unique=True)
        batch_op.create_index("ix_users_email", ["email"], unique=True)

    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("pull_request_number", existing_type=sa.Integer(), type_=sa.String(), existing_nullable=True)
        batch_op.alter_column("description", existing_type=sa.String(length=1000), type_=sa.String(length=500), existing_nullable=True)
        batch_op.alter_column("title", existing_type=sa.String(length=100), type_=sa.String(length=50), existing_nullable=False)
