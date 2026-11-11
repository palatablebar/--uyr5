"""Engine / session factory shared by API and Celery worker."""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=8,
    max_overflow=4,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(max_attempts: int = 30, delay: float = 2.0) -> None:
    """Create all tables, waiting for MySQL to finish its first boot."""
    import time

    import app.models  # noqa: F401

    last_error = None
    for _ in range(max_attempts):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_schema_upgrades()
            return
        except Exception as exc:  # pragma: no cover - infra retry
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"database bootstrap failed: {last_error}")


def _ensure_schema_upgrades() -> None:
    """Idempotent in-place upgrades for databases created before a column or
    constraint existed (``create_all`` never alters existing tables). Mirrors
    alembic revision 20261111_0002; the FK on ``active_profile_run_id`` is
    intentionally model-only here so the ALTER stays cheap on busy tables.

    Every step commits in its own transaction: a failing step (e.g. the unique
    constraint meeting legacy duplicates, or a concurrent backend/worker boot
    racing the same ALTER) must not roll back the others; the strict steps
    propagate so ``init_db`` retries them.
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        dataset_columns = {column["name"] for column in inspector.get_columns("datasets")}
        run_columns = {column["name"] for column in inspector.get_columns("rule_runs")}
        profile_uniques = {u["name"] for u in inspector.get_unique_constraints("column_profiles")}
        profile_indexes = {i["name"] for i in inspector.get_indexes("column_profiles")}

    if "active_profile_run_id" not in dataset_columns:
        _run_upgrade_step(
            "datasets.active_profile_run_id",
            [
                "ALTER TABLE datasets ADD COLUMN active_profile_run_id INT NULL",
                "CREATE INDEX ix_datasets_active_profile_run_id ON datasets (active_profile_run_id)",
            ],
            tolerant=False,
        )
    if "profile_diff_json" not in run_columns:
        _run_upgrade_step(
            "rule_runs.profile_diff_json",
            ["ALTER TABLE rule_runs ADD COLUMN profile_diff_json JSON NULL"],
            tolerant=False,
        )
    if "uq_column_profiles_run_column" not in (profile_uniques | profile_indexes):
        # defense-in-depth only (the service layer already clears a batch's own
        # rows before rewriting it) — legacy duplicates must not block boot
        _run_upgrade_step(
            "column_profiles.uq_run_column",
            [
                "ALTER TABLE column_profiles "
                "ADD CONSTRAINT uq_column_profiles_run_column UNIQUE (run_id, column_name)"
            ],
            tolerant=True,
        )

    # Backfill: rows written before the pointer existed all belong to the
    # dataset's latest batch, so adopt it as the effective one.
    _run_upgrade_step(
        "datasets.active_profile_run_id.backfill",
        [
            "UPDATE datasets SET active_profile_run_id = ("
            "  SELECT MAX(cp.run_id) FROM column_profiles cp WHERE cp.dataset_id = datasets.id"
            ") WHERE active_profile_run_id IS NULL"
            "  AND EXISTS (SELECT 1 FROM column_profiles cp2 WHERE cp2.dataset_id = datasets.id)"
        ],
        tolerant=False,
    )


def _run_upgrade_step(step: str, statements: list[str], tolerant: bool) -> None:
    from app.utils.logger import log_event

    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    except Exception as exc:
        if not tolerant:
            raise
        log_event("SCHEMA_UPGRADE_STEP_FAILED", step=step, error=f"{type(exc).__name__}: {exc}")
