import time
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants.column_type import ColumnType
from app.constants.rule_run_status import RuleRunStatus
from app.models import ColumnProfile, Dataset, RuleRun, User
from app.profiling import pattern, stats, type_infer
from app.profiling.loader import load_frame
from app.profiling.suggest import suggest_for_dataset
from app.schemas.dataset import serialize_profile_run_ref, serialize_profile_status_ref
from app.schemas.profile import serialize_column_profile
from app.utils.logger import log_event


def start_profile(db: Session, actor: User, dataset: Dataset) -> RuleRun:
    from app.middlewares.quota import consume_scan

    consume_scan(db, actor, dataset.id, "profile")
    run = RuleRun(dataset_id=dataset.id, kind="profile", triggered_by=actor.id, status=RuleRunStatus.QUEUED.value)
    db.add(run)
    dataset.status = "profiling"
    db.commit()
    db.refresh(run)
    log_event("PROFILE_ENQUEUED", dataset_id=dataset.id, run_id=run.id)
    return run


def latest_profile_run(db: Session, dataset_id: int) -> RuleRun | None:
    return (
        db.query(RuleRun)
        .filter(RuleRun.dataset_id == dataset_id, RuleRun.kind == "profile")
        .order_by(RuleRun.id.desc())
        .first()
    )


def active_profile_run(db: Session, dataset: Dataset) -> RuleRun | None:
    """The batch whose results are currently served; NULL until one succeeds."""
    if not dataset.active_profile_run_id:
        return None
    return db.get(RuleRun, dataset.active_profile_run_id)


def get_active_profiles(db: Session, dataset: Dataset) -> list[ColumnProfile]:
    """Column rows of the effective batch only — never a half-written new batch."""
    if not dataset.active_profile_run_id:
        return []
    return (
        db.query(ColumnProfile)
        .filter(ColumnProfile.run_id == dataset.active_profile_run_id)
        .order_by(ColumnProfile.ordinal.asc())
        .all()
    )


def _build_column_diff(
    previous: list[ColumnProfile], current: list[ColumnProfile], previous_run_id: int | None
) -> dict:
    """Diff the freshly computed batch against the previously effective one."""
    previous_by_name = {profile.column_name: profile for profile in previous}
    current_by_name = {profile.column_name: profile for profile in current}
    added = [profile.column_name for profile in current if profile.column_name not in previous_by_name]
    removed = [profile.column_name for profile in previous if profile.column_name not in current_by_name]
    type_changed = [
        {
            "column_name": profile.column_name,
            "from": previous_by_name[profile.column_name].inferred_type,
            "to": profile.inferred_type,
        }
        for profile in current
        if profile.column_name in previous_by_name
        and previous_by_name[profile.column_name].inferred_type != profile.inferred_type
    ]
    return {
        "previous_run_id": previous_run_id,
        "added": added,
        "removed": removed,
        "type_changed": type_changed,
    }


def compute_profiles(db: Session, dataset: Dataset, run: RuleRun) -> int:
    """Compute one profiling batch and activate it atomically.

    * The previous effective batch stays untouched while the new one computes;
      the pointer ``dataset.active_profile_run_id`` flips only in the final
      commit together with the full row set.
    * Re-executing an already-succeeded batch is a no-op; re-executing a
      failed/partial batch first clears that batch's own rows, so the same
      batch never produces duplicates.
    """
    if run.status == RuleRunStatus.SUCCEEDED.value:
        existing = (
            db.query(func.count(ColumnProfile.id)).filter(ColumnProfile.run_id == run.id).scalar() or 0
        )
        log_event("PROFILE_BATCH_REUSED", dataset_id=dataset.id, run_id=run.id, columns=existing)
        return existing

    started = time.perf_counter()
    run.status = RuleRunStatus.RUNNING.value
    run.started_at = datetime.now()
    db.commit()

    loaded = load_frame(dataset.file_path, dataset.source_format)
    df = loaded.df
    total_rows = loaded.row_count
    log_event("PROFILE_STARTED", dataset_id=dataset.id, run_id=run.id, rows=total_rows)

    previous_run_id = dataset.active_profile_run_id
    previous_profiles = get_active_profiles(db, dataset)

    # Idempotency for retried batches: drop this batch's own leftovers only.
    db.query(ColumnProfile).filter(ColumnProfile.run_id == run.id).delete()
    db.flush()

    computed: list[ColumnProfile] = []
    for ordinal, column in enumerate(loaded.columns):
        series = df[column]
        inferred = type_infer.infer_type(series)
        column_stats = stats.compute_stats(series, inferred, total_rows)
        summary = None
        if inferred in (ColumnType.STRING.value, ColumnType.UNKNOWN.value, ColumnType.DATE.value, ColumnType.BOOLEAN.value):
            summary = pattern.pattern_summary(series)
        if summary is None and inferred in (ColumnType.INTEGER.value, ColumnType.DECIMAL.value):
            summary = f"数值列 · min={column_stats['min_value']} max={column_stats['max_value']}"
        profile = ColumnProfile(
            dataset_id=dataset.id,
            run_id=run.id,
            column_name=str(column),
            ordinal=ordinal,
            inferred_type=inferred,
            null_rate=round(column_stats["null_rate"], 4),
            distinct_count=column_stats["distinct_count"],
            min_value=column_stats["min_value"],
            max_value=column_stats["max_value"],
            p50=column_stats["p50"],
            p95=column_stats["p95"],
            top_values_json=column_stats["top_values"],
            pattern_summary=summary,
            outlier_count=column_stats["outlier_count"],
        )
        db.add(profile)
        computed.append(profile)

    diff = _build_column_diff(previous_profiles, computed, previous_run_id)

    # Atomic activation: pointer flip + run completion + diff + pruning of
    # superseded/failed batches commit together or not at all.
    dataset.row_count = total_rows
    dataset.column_count = len(loaded.columns)
    dataset.status = "profiled"
    dataset.active_profile_run_id = run.id
    run.rules_total = len(computed)
    run.rules_passed = len(computed)
    run.rows_scanned = total_rows
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    run.status = RuleRunStatus.SUCCEEDED.value
    run.finished_at = datetime.now()
    run.profile_diff_json = diff
    db.flush()
    db.query(ColumnProfile).filter(
        ColumnProfile.dataset_id == dataset.id,
        ColumnProfile.run_id != run.id,
    ).delete(synchronize_session=False)
    db.commit()
    log_event(
        "PROFILE_FINISHED",
        dataset_id=dataset.id,
        run_id=run.id,
        columns=len(computed),
        duration_ms=run.duration_ms,
    )
    log_event(
        "PROFILE_ACTIVATED",
        dataset_id=dataset.id,
        run_id=run.id,
        previous_run_id=previous_run_id if previous_run_id is not None else "-",
        added=len(diff["added"]),
        removed=len(diff["removed"]),
        type_changed=len(diff["type_changed"]),
    )
    return len(computed)


def mark_profile_failed(db: Session, dataset: Dataset, run: RuleRun, error_code: str) -> None:
    """Failure path: drop this batch's partial rows, keep serving the previous
    effective batch, and restore the dataset status the failure clobbered."""
    run.status = RuleRunStatus.FAILED.value
    run.error_code = error_code
    run.finished_at = datetime.now()
    db.query(ColumnProfile).filter(ColumnProfile.run_id == run.id).delete(synchronize_session=False)
    dataset.status = "profiled" if dataset.active_profile_run_id else "uploaded"
    db.commit()
    log_event(
        "PROFILE_FAILED_KEPT_ACTIVE",
        dataset_id=dataset.id,
        run_id=run.id,
        active_run_id=dataset.active_profile_run_id if dataset.active_profile_run_id is not None else "-",
        error_code=error_code,
    )


def profile_payload(db: Session, dataset: Dataset) -> dict:
    active_run = active_profile_run(db, dataset)
    latest_run = latest_profile_run(db, dataset.id)
    profiles = get_active_profiles(db, dataset)
    columns = [serialize_column_profile(profile) for profile in profiles]
    return {
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "source_format": dataset.source_format,
            "row_count": dataset.row_count,
            "column_count": dataset.column_count,
            "checksum": dataset.checksum,
            "status": dataset.status,
            "uploaded_at": dataset.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if dataset.uploaded_at else None,
        },
        # the effective batch (what the columns below belong to)
        "run": serialize_profile_run_ref(active_run) if active_run else None,
        # the most recent attempt, whatever its state — drives the status strip
        "latest_run": serialize_profile_status_ref(latest_run) if latest_run else None,
        # column diff of the effective batch vs its predecessor (None for legacy rows)
        "column_changes": (active_run.profile_diff_json if active_run else None),
        "columns": columns,
        "recommendations": suggest_for_dataset(columns, dataset.row_count) if columns else [],
    }
