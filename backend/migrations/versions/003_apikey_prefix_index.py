"""Index api_keys.key_prefix for O(1) key lookup (F6)

API-key validation looked up by hashing the presented key against every active
key (O(n) bcrypt). The fix queries by the key_prefix (first 10 chars) first, then
verifies only the handful that match — but key_prefix was never indexed (001 indexed
key_hash, not key_prefix). This adds that index.

Revision ID: 003_apikey_prefix_index
Revises: 002_messages_tenant_rls
Create Date: 2026-05-31
"""
from typing import Sequence, Union
from alembic import op

revision: str = "003_apikey_prefix_index"
down_revision: Union[str, None] = "002_messages_tenant_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
