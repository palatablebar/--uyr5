"""profile batch activation

Revision ID: 20261111_0002
Revises: 20261109_0001
Create Date: 2026-11-11

Atomic profiling-batch activation:
  * datasets.active_profile_run_id — pointer to the effective profiling batch
  * rule_runs.profile_diff_json — column diff captured at activation time
  * column_profiles UNIQUE (run_id, column_name) — same-batch reruns stay
    idempotent
Existing rows are backfilled: every dataset adopts its latest profiled batch
as the effective one.
"""
from alembic import op

revision = "20261111_0002"
down_revision = "20261109_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE datasets ADD COLUMN active_profile_run_id INT NULL")
    op.execute("CREATE INDEX ix_datasets_active_profile_run_id ON datasets (active_profile_run_id)")
    op.execute("ALTER TABLE rule_runs ADD COLUMN profile_diff_json JSON NULL")
    op.execute(
        "ALTER TABLE column_profiles "
        "ADD CONSTRAINT uq_column_profiles_run_column UNIQUE (run_id, column_name)"
    )
    op.execute(
        "UPDATE datasets SET active_profile_run_id = ("
        "  SELECT MAX(cp.run_id) FROM column_profiles cp WHERE cp.dataset_id = datasets.id"
        ") WHERE active_profile_run_id IS NULL"
        "  AND EXISTS (SELECT 1 FROM column_profiles cp2 WHERE cp2.dataset_id = datasets.id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE column_profiles DROP CONSTRAINT uq_column_profiles_run_column")
    op.execute("ALTER TABLE rule_runs DROP COLUMN profile_diff_json")
    op.execute("DROP INDEX ix_datasets_active_profile_run_id ON datasets")
    op.execute("ALTER TABLE datasets DROP COLUMN active_profile_run_id")
