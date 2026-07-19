"""Track staging object keys consumed by catalog imports.

Revision ID: 0004_track_promoted_staging_keys
Revises: 0003_add_payment_attempts
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_track_promoted_staging_keys"
down_revision: str | None = "0003_add_payment_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column(
            "promoted_staging_keys",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "promoted_staging_keys")
