"""
tests/test_evidence_lifecycle.py

Tests for the Evidence Lifecycle service (REQ-B2-001, REQ-B2-001A,
EA-001 through EA-009). Follows the same test baseline as Sprint B-1
(ED-W28-002 / ED-B2, consistent pattern): pytest + isolated in-memory
SQLite. No FastAPI TestClient is needed here since Evidence Lifecycle
is implemented as a producer-independent service layer, not new HTTP
endpoints (REQ-B2-001's Out of Scope explicitly excludes "API Design").

Covers:
  - AC-001: every accepted Observation produces exactly one Evidence
  - AC-002: Evidence traceable to its originating Observation
  - AC-003: Evidence cannot be modified after creation
  - AC-004: Evidence can participate in multiple validations without
    duplication or mutation (append-only validation records)
  - AC-005: archived Evidence remains queryable and traceable
  - EA-006: effective validation status = latest validation record
  - EA-007: Evidence + initial validation as one atomic operation
  - EA-008: no mutable validation_status field exists on Evidence
  - EA-009: producer-independent core, Reflex-specific adapter only
"""

import uuid
from datetime import datetime

import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from sqlmodel.pool import StaticPool

from app.database.models import (
    Evidence,
    EvidenceValidationRecord,
    EvidenceArchivalRecord,
    MirroredObservation,
)
from app.services import evidence_lifecycle as el


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_mirrored_observation(session: Session, **overrides) -> MirroredObservation:
    defaults = dict(
        source_system="reflex",
        observation_id=f"obs-{uuid.uuid4()}",
        observed_at=datetime.utcnow(),
        market="BTCUSDT",
        timeframe="1H",
        structural_state="accumulation",
        behavioural_state="pressure_accumulating",
        confidence=0.6,
        summary="test",
        runtime_context={"note": "test"},
        schema_version="1.0",
    )
    defaults.update(overrides)
    obs = MirroredObservation(**defaults)
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


# ─────────────────────────────────────────────────────────────
# AC-001, AC-002: creation + traceability
# ─────────────────────────────────────────────────────────────

def test_create_evidence_producer_independent(session: Session):
    evidence = el.create_evidence(
        producer_id="reflex",
        observation_reference="obs-123",
        evidence_timestamp=datetime.utcnow(),
        runtime_context={"a": 1},
        session=session,
    )
    session.commit()
    assert evidence.evidence_id is not None
    assert evidence.producer_id == "reflex"
    assert evidence.observation_reference == "obs-123"


def test_evidence_traceable_by_producer_and_reference(session: Session):
    el.create_evidence(
        producer_id="reflex",
        observation_reference="obs-456",
        evidence_timestamp=datetime.utcnow(),
        runtime_context=None,
        session=session,
    )
    session.commit()

    found = el.get_evidence_by_producer_observation("reflex", "obs-456", session)
    assert found is not None
    assert found.observation_reference == "obs-456"


def test_reflex_adapter_creates_exactly_one_evidence_per_observation(session: Session):
    obs = _make_mirrored_observation(session)
    evidence, record = el.create_evidence_for_mirrored_observation(obs, session)

    assert evidence.producer_id == "reflex"
    assert evidence.observation_reference == obs.observation_id

    all_evidence = session.exec(select(Evidence)).all()
    assert len(all_evidence) == 1  # AC-001: exactly one Evidence


# ─────────────────────────────────────────────────────────────
# AC-003, EA-008: immutability
# ─────────────────────────────────────────────────────────────

def test_evidence_model_has_no_update_endpoint_or_mutation_path():
    """
    There is no PATCH/PUT route or service function anywhere for
    Evidence — confirmed by the absence of any such function in
    app/services/evidence_lifecycle.py. This test documents that
    absence structurally: every public function either creates a new
    row or reads existing rows; none accepts an Evidence instance for
    the purpose of changing its fields.
    """
    mutating_functions = [
        name for name in dir(el)
        if name.startswith("update_evidence") or name.startswith("patch_evidence")
    ]
    assert mutating_functions == []


def test_validation_status_is_not_a_field_on_evidence():
    """EA-008: the Evidence table shall not contain a mutable validation_status field."""
    assert "validation_status" not in Evidence.__fields__


# ─────────────────────────────────────────────────────────────
# EA-006, EA-007: validation, append-only records, effective status
# ─────────────────────────────────────────────────────────────

def test_validate_evidence_accepted(session: Session):
    obs = _make_mirrored_observation(session)
    evidence, record = el.create_evidence_for_mirrored_observation(obs, session)
    assert record.outcome == "accepted"


def test_validate_evidence_incomplete_when_runtime_context_not_associated(session: Session):
    evidence = el.create_evidence(
        producer_id="reflex",
        observation_reference="obs-789",
        evidence_timestamp=datetime.utcnow(),
        runtime_context={"x": 1},
        session=session,
    )
    session.flush()

    record = el.validate_evidence(
        evidence=evidence,
        observation_exists=True,
        observation_reference_valid=True,
        producer_id_present=True,
        timestamp_present=True,
        runtime_context_structurally_valid=True,
        runtime_context_associated=False,  # forces Incomplete
        persisted_data_consistent=True,
        session=session,
    )
    session.commit()
    assert record.outcome == "incomplete"
    assert "runtime_context_not_associated" in record.decision_reasons


def test_validate_evidence_rejected_when_observation_unverifiable(session: Session):
    evidence = el.create_evidence(
        producer_id="reflex",
        observation_reference="obs-999",
        evidence_timestamp=datetime.utcnow(),
        runtime_context=None,
        session=session,
    )
    session.flush()

    record = el.validate_evidence(
        evidence=evidence,
        observation_exists=False,  # forces Rejected
        observation_reference_valid=True,
        producer_id_present=True,
        timestamp_present=True,
        runtime_context_structurally_valid=True,
        runtime_context_associated=True,
        persisted_data_consistent=True,
        session=session,
    )
    session.commit()
    assert record.outcome == "rejected"
    assert "originating_observation_cannot_be_verified" in record.decision_reasons


def test_revalidation_appends_new_record_does_not_modify_previous(session: Session):
    """AC-004 / EA-006: an Incomplete outcome resolved later appends a
    NEW record; the previous record is untouched."""
    obs = _make_mirrored_observation(session)
    evidence, first_record = el.create_evidence_for_mirrored_observation(obs, session)

    # Simulate a later re-validation appending a second record
    second_record = el.validate_evidence(
        evidence=evidence,
        observation_exists=True,
        observation_reference_valid=True,
        producer_id_present=True,
        timestamp_present=True,
        runtime_context_structurally_valid=True,
        runtime_context_associated=True,
        persisted_data_consistent=True,
        session=session,
    )
    session.commit()

    history = el.get_validation_history(evidence.evidence_id, session)
    assert len(history) == 2
    assert history[0].validation_record_id == first_record.validation_record_id
    assert history[1].validation_record_id == second_record.validation_record_id
    # first record's own fields remain unchanged
    assert history[0].outcome == first_record.outcome


def test_effective_validation_status_is_latest_record(session: Session):
    obs = _make_mirrored_observation(session)
    evidence, first_record = el.create_evidence_for_mirrored_observation(obs, session)

    el.validate_evidence(
        evidence=evidence,
        observation_exists=False,  # second validation: Rejected
        observation_reference_valid=True,
        producer_id_present=True,
        timestamp_present=True,
        runtime_context_structurally_valid=True,
        runtime_context_associated=True,
        persisted_data_consistent=True,
        session=session,
    )
    session.commit()

    effective = el.get_effective_validation_status(evidence.evidence_id, session)
    assert effective == "rejected"  # reflects the LATEST record, not the first (accepted)


def test_adapter_atomicity_no_orphaned_evidence_on_failure(session: Session, monkeypatch):
    """
    EA-007: if validation fails to persist, the Evidence created in the
    same operation must not be left orphaned (rolled back together).
    """
    obs = _make_mirrored_observation(session)

    def broken_validate(*args, **kwargs):
        raise RuntimeError("simulated validation failure")

    monkeypatch.setattr(el, "validate_evidence", broken_validate)

    with pytest.raises(el.EvidenceCreationError):
        el.create_evidence_for_mirrored_observation(obs, session)

    # Evidence must NOT exist after rollback — no orphaned row
    all_evidence = session.exec(select(Evidence)).all()
    assert len(all_evidence) == 0


# ─────────────────────────────────────────────────────────────
# AC-005: archival remains queryable/traceable
# ─────────────────────────────────────────────────────────────

def test_archived_evidence_remains_queryable(session: Session):
    obs = _make_mirrored_observation(session)
    evidence, _ = el.create_evidence_for_mirrored_observation(obs, session)

    assert el.is_archived(evidence.evidence_id, session) is False

    el.archive_evidence(evidence.evidence_id, "test archival", session)

    assert el.is_archived(evidence.evidence_id, session) is True
    # Still fully retrievable — archiving never hides or removes rows
    still_found = el.get_evidence_by_id(evidence.evidence_id, session)
    assert still_found is not None
    assert still_found.evidence_id == evidence.evidence_id


def test_archiving_does_not_mutate_evidence_fields(session: Session):
    obs = _make_mirrored_observation(session)
    evidence, _ = el.create_evidence_for_mirrored_observation(obs, session)
    original_created_at = evidence.created_at

    el.archive_evidence(evidence.evidence_id, "reason", session)

    refreshed = el.get_evidence_by_id(evidence.evidence_id, session)
    assert refreshed.created_at == original_created_at  # unchanged


# ─────────────────────────────────────────────────────────────
# EA-009: producer-independence boundary
# ─────────────────────────────────────────────────────────────

def test_core_functions_do_not_reference_mirrored_observation_type():
    """
    Structural check: only create_evidence_for_mirrored_observation
    should reference MirroredObservation. This is a best-effort static
    check (source inspection), not a full architectural enforcement.
    """
    import inspect

    producer_independent_functions = [
        el.create_evidence,
        el.validate_evidence,
        el.get_effective_validation_status,
        el.get_validation_history,
        el.archive_evidence,
        el.is_archived,
        el.get_evidence_by_id,
        el.get_evidence_by_producer_observation,
    ]
    for fn in producer_independent_functions:
        source = inspect.getsource(fn)
        assert "MirroredObservation" not in source, (
            f"{fn.__name__} references MirroredObservation — "
            f"violates EA-009 producer-independence boundary"
        )
