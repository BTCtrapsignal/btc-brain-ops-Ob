"""
app/services/evidence_lifecycle.py

Producer-independent Evidence Lifecycle service (REQ-B2-001,
REQ-B2-001A, EA-001 through EA-009; ADR-0001, ADR-0002).

This module implements:
  - Evidence creation (Stage 2)
  - Evidence validation (Stage 3, REQ-B2-001A decision rules)
  - Effective validation status projection (EA-006) — a read-only
    derivation, never stored on Evidence itself (EA-008)
  - Evidence archival (Stage 6) — an append-only record, by analogy to
    EA-006's validation-record pattern (see EvidenceArchivalRecord's
    docstring in app/database/models.py — this analogy is an
    implementation inference, flagged in the accompanying
    Implementation Report, not an explicit instruction)
  - A Reflex-specific adapter (EA-009) — the ONLY function in this
    module aware of MirroredObservation. Every other function here is
    producer-independent: it accepts only primitive values, never a
    producer-specific object.

Deferred / explicitly out of scope for this module (per EA-001,
EA-002, EA-009, and the Implementation Authorization):
  - No modification to EngineeringEvidence (EA-001).
  - No Evidence linkage to Question / Investigation / Finding
    (EA-002) — no link table, no linking functions exist here.
  - No Signal Bot adapter or producer-specific logic (EA-009,
    Implementation Authorization) — only Reflex is implemented.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.database.models import (
    Evidence,
    EvidenceValidationRecord,
    EvidenceArchivalRecord,
    MirroredObservation,
)

logger = logging.getLogger(__name__)

VALIDATOR_IDENTITY = "evidence_validation_service_v1"


class EvidenceCreationError(Exception):
    """Raised when Evidence + initial validation cannot be created as
    one consistent application operation (EA-007)."""


# ─────────────────────────────────────────────────────────────
# Producer-independent Evidence creation (Stage 2, FR-001/FR-002)
# ─────────────────────────────────────────────────────────────

def create_evidence(
    *,
    producer_id: str,
    observation_reference: str,
    evidence_timestamp: datetime,
    runtime_context: Optional[dict],
    session: Session,
) -> Evidence:
    """
    Build one immutable Evidence record (REQ-B2-001 Stage 2) and add it
    to the session. Does NOT commit — callers control transaction
    boundaries so that Evidence creation can be combined atomically
    with its initial validation record (EA-007). Uses session.flush()
    (not commit) to surface integrity errors early without ending the
    transaction.

    Producer-independent: accepts only primitive identifying values,
    never a producer-specific object. Producer-specific adapters are
    responsible for extracting these values from their own runtime
    objects (see create_evidence_for_mirrored_observation below).
    """
    evidence = Evidence(
        evidence_id=str(uuid.uuid4()),
        producer_id=producer_id,
        observation_reference=observation_reference,
        evidence_timestamp=evidence_timestamp,
        runtime_context=runtime_context,
    )
    session.add(evidence)
    session.flush()
    return evidence


# ─────────────────────────────────────────────────────────────
# Producer-independent Evidence validation (Stage 3, REQ-B2-001A)
# ─────────────────────────────────────────────────────────────

def validate_evidence(
    *,
    evidence: Evidence,
    observation_exists: bool,
    observation_reference_valid: bool,
    producer_id_present: bool,
    timestamp_present: bool,
    runtime_context_structurally_valid: bool,
    runtime_context_associated: bool,
    persisted_data_consistent: bool,
    session: Session,
) -> EvidenceValidationRecord:
    """
    Evaluate one Evidence record against REQ-B2-001A's decision rules
    and add exactly one new EvidenceValidationRecord (append-only,
    EA-006) to the session. Does NOT commit — see create_evidence's
    docstring on transaction boundaries.

    All boolean inputs represent checks the CALLER has already
    performed against the actual originating Observation / persisted
    Evidence. This function implements only the DECISION rules from
    REQ-B2-001A given those check results, and does not itself reach
    into any producer-specific store — keeping it producer-independent
    per EA-009.

    Rule mapping (REQ-B2-001A):
      Rejected  — any of: observation unverifiable, reference invalid,
                  persisted data inconsistent (EA-005: corruption is a
                  post-persistence integrity concept, independent of
                  transport-layer 422 validation).
      Accepted  — all of: observation exists, reference valid, producer
                  identity present, timestamp present, runtime_context
                  structurally valid AND associated to the originating
                  Observation (EA-004: structural presence + association
                  only — semantic field content is out of scope).
      Incomplete — none of the Rejected conditions apply, but not all
                  Accepted conditions are satisfied either.
    """
    checks_performed = {
        "observation_exists": observation_exists,
        "observation_reference_valid": observation_reference_valid,
        "producer_id_present": producer_id_present,
        "timestamp_present": timestamp_present,
        "runtime_context_structurally_valid": runtime_context_structurally_valid,
        "runtime_context_associated": runtime_context_associated,
        "persisted_data_consistent": persisted_data_consistent,
    }

    reasons = []

    if not observation_exists:
        reasons.append("originating_observation_cannot_be_verified")
    if not observation_reference_valid:
        reasons.append("observation_reference_invalid")
    if not persisted_data_consistent:
        reasons.append("persisted_data_inconsistent")  # EA-005 corruption concept

    if reasons:
        outcome = "rejected"
    else:
        accepted = (
            observation_exists
            and observation_reference_valid
            and producer_id_present
            and timestamp_present
            and runtime_context_structurally_valid
            and runtime_context_associated
        )
        if accepted:
            outcome = "accepted"
        else:
            outcome = "incomplete"
            if not producer_id_present:
                reasons.append("producer_id_missing")
            if not timestamp_present:
                reasons.append("timestamp_missing")
            if not runtime_context_structurally_valid:
                reasons.append("runtime_context_structurally_invalid")
            if not runtime_context_associated:
                reasons.append("runtime_context_not_associated")

    record = EvidenceValidationRecord(
        validation_record_id=str(uuid.uuid4()),
        evidence_id=evidence.evidence_id,
        outcome=outcome,
        checks_performed=checks_performed,
        decision_reasons="; ".join(reasons) if reasons else "all_checks_passed",
        validator_identity=VALIDATOR_IDENTITY,
    )
    session.add(record)
    session.flush()
    return record


def get_effective_validation_status(evidence_id: str, session: Session) -> Optional[str]:
    """
    EA-006: effective status is the outcome of the latest
    EvidenceValidationRecord for this evidence_id. Read projection
    only — never stored on Evidence itself (EA-008).
    """
    latest = session.exec(
        select(EvidenceValidationRecord)
        .where(EvidenceValidationRecord.evidence_id == evidence_id)
        .order_by(EvidenceValidationRecord.evaluated_at.desc())
    ).first()
    return latest.outcome if latest else None


def get_validation_history(evidence_id: str, session: Session) -> list[EvidenceValidationRecord]:
    """All validation records for an Evidence, oldest first — for audit/traceability (FR-003, AC-004)."""
    return session.exec(
        select(EvidenceValidationRecord)
        .where(EvidenceValidationRecord.evidence_id == evidence_id)
        .order_by(EvidenceValidationRecord.evaluated_at.asc())
    ).all()


# ─────────────────────────────────────────────────────────────
# Evidence archival (Stage 6) — see EvidenceArchivalRecord docstring
# in models.py regarding the append-only design inference.
# ─────────────────────────────────────────────────────────────

def archive_evidence(evidence_id: str, reason: Optional[str], session: Session) -> EvidenceArchivalRecord:
    record = EvidenceArchivalRecord(
        evidence_id=evidence_id,
        reason=reason,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    logger.info(
        "evidence_lifecycle.archived evidence_id=%s reason=%s",
        evidence_id,
        reason,
    )
    return record


def get_archival_record(evidence_id: str, session: Session) -> Optional[EvidenceArchivalRecord]:
    """
    REQ-B2-002: return the full EvidenceArchivalRecord when present, or
    None when no archival record exists. Does not modify archive
    behavior — read-only.
    """
    return session.exec(
        select(EvidenceArchivalRecord).where(EvidenceArchivalRecord.evidence_id == evidence_id)
    ).first()


def is_archived(evidence_id: str, session: Session) -> bool:
    # Reimplemented in terms of get_archival_record() per REQ-B2-002 —
    # observable contract (bool, True iff any archival record exists)
    # is unchanged.
    return get_archival_record(evidence_id, session) is not None


# ─────────────────────────────────────────────────────────────
# Traceability (FR-002, AC-002) — producer-independent queries
# ─────────────────────────────────────────────────────────────

def get_evidence_by_id(evidence_id: str, session: Session) -> Optional[Evidence]:
    return session.exec(select(Evidence).where(Evidence.evidence_id == evidence_id)).first()


def get_evidence_by_producer_observation(
    producer_id: str, observation_reference: str, session: Session
) -> Optional[Evidence]:
    return session.exec(
        select(Evidence).where(
            Evidence.producer_id == producer_id,
            Evidence.observation_reference == observation_reference,
        )
    ).first()


# ─────────────────────────────────────────────────────────────
# Reflex-specific adapter (EA-009) — the ONLY producer-aware code in
# this module. Invoked by app/api/engineering.py's mirror endpoint
# after a NEW (non-duplicate) MirroredObservation has been
# successfully persisted. Not invoked on the duplicate-return path,
# since a duplicate mirror request is not a new accepted Observation
# (REQ-B2-001 AC-001: "every accepted Observation produces exactly
# one Evidence" — a duplicate is not a new acceptance).
# ─────────────────────────────────────────────────────────────

def create_evidence_for_mirrored_observation(
    mirrored_observation: MirroredObservation,
    session: Session,
) -> tuple[Evidence, EvidenceValidationRecord]:
    """
    Reflex-specific adapter (EA-009). Extracts producer-independent
    values from a MirroredObservation and invokes the producer-
    independent creation + validation services as ONE atomic
    transaction (EA-007: "creation of Evidence and its initial
    validation record shall be handled as one consistent application
    operation" — implemented here via a single commit covering both
    inserts; a failure at any point rolls back both, so the system
    never silently leaves a newly created Evidence without a
    corresponding initial validation result).
    """
    try:
        evidence = create_evidence(
            producer_id=mirrored_observation.source_system,
            observation_reference=mirrored_observation.observation_id,
            evidence_timestamp=mirrored_observation.observed_at,
            runtime_context=mirrored_observation.runtime_context,
            session=session,
        )

        record = validate_evidence(
            evidence=evidence,
            observation_exists=True,  # mirrored_observation was just persisted successfully
            observation_reference_valid=bool(mirrored_observation.observation_id),
            producer_id_present=bool(mirrored_observation.source_system),
            timestamp_present=mirrored_observation.observed_at is not None,
            runtime_context_structurally_valid=isinstance(
                mirrored_observation.runtime_context, (dict, type(None))
            ),
            runtime_context_associated=(
                evidence.observation_reference == mirrored_observation.observation_id
            ),
            persisted_data_consistent=(
                evidence.producer_id == mirrored_observation.source_system
            ),
            session=session,
        )

        # Single commit covering both rows — this is the "one
        # consistent application operation" required by EA-007.
        session.commit()
        session.refresh(evidence)
        session.refresh(record)

        logger.info(
            "evidence_lifecycle.created_and_validated "
            "evidence_id=%s producer_id=%s observation_reference=%s outcome=%s",
            evidence.evidence_id,
            evidence.producer_id,
            evidence.observation_reference,
            record.outcome,
        )
        return evidence, record

    except Exception as exc:
        # Rolls back BOTH the Evidence and validation-record inserts,
        # since neither was committed until the single commit above —
        # satisfies EA-007's "never silently leave orphaned Evidence."
        session.rollback()
        logger.error(
            "evidence_lifecycle.creation_or_validation_failed "
            "producer_id=%s observation_reference=%s exception_type=%s",
            mirrored_observation.source_system,
            mirrored_observation.observation_id,
            type(exc).__name__,
            exc_info=True,
        )
        raise EvidenceCreationError(
            f"Evidence lifecycle failed for observation_reference="
            f"{mirrored_observation.observation_id}"
        ) from exc
