"""
api/engineering.py — REQ-W27-002

CRUD endpoints for the Engineering Database.
Brain Ops is the authoritative store for EO, ER, and Evidence records.

Endpoints:
  POST   /engineering/eo              — create Engineering Observation
  GET    /engineering/eo/             — list EOs (with filters)
  GET    /engineering/eo/{eo_id}      — retrieve EO
  PATCH  /engineering/eo/{eo_id}      — update EO (status, evidence, notes)

  POST   /engineering/er              — create Engineering Review
  GET    /engineering/er/             — list ERs (with filters)
  GET    /engineering/er/{er_id}      — retrieve ER
  PATCH  /engineering/er/{er_id}      — update ER

  POST   /engineering/evidence        — record evidence increment
  GET    /engineering/evidence/{er_id} — list evidence for an ER or EO

Governance:
  Engineering Constitution Article 6:  Review IDs are immutable.
  Engineering Constitution Article 9:  Promotion belongs to Engineering Review (ChatGPT).
  Engineering Constitution Article 15: Engineering Reviews indexed by immutable IDs.

  These endpoints provide storage and retrieval only.
  Promotion decisions, confidence assignments, and lifecycle transitions
  are performed by Engineering Review (ChatGPT) — never automatically.
"""

from datetime import datetime
from typing import Optional
import logging
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.database.models import (
    EngineeringObservation,
    EngineeringReview,
    EngineeringEvidence,
    MirroredObservation,
)
from app.services import evidence_lifecycle  # REQ-B2-001 / EA-009

router = APIRouter(prefix="/engineering", tags=["engineering"])

# EC-W28-005 (Brain Ops Logging Baseline, approved, normative):
# module-level logger only. No basicConfig, handlers, formatters, or
# custom logging classes in this router module — application-level
# configuration (if any) belongs at the Brain Ops entry point.
logger = logging.getLogger(__name__)

# EC-W28-007 (Request Validation Failure Logging, approved, normative):
# handles FastAPI/Pydantic-level request validation failures (e.g.
# missing required fields, wrong types) that occur BEFORE the mirror
# endpoint function body executes — these are not caught by the
# schema_version check inside mirror_observation(), since Pydantic
# rejects the request before that code ever runs.
#
# This handler is registered on the FastAPI `app` instance in
# app/main.py (FastAPI does not support router-scoped exception
# handlers — RequestValidationError handlers must be registered at
# the app level). It is defined here, alongside the endpoint it
# concerns, and imported into main.py for registration.
#
# Scope: only requests to POST /engineering/mirror are logged here.
# EC-W28-007 refers to "the mirror endpoint" in the context of
# observation ingestion (source_system/observation_id/schema_version
# fields) — this scoping was chosen to match that context precisely,
# rather than also capturing query-parameter validation errors on the
# GET /engineering/mirror/... retrieval endpoints, which are a
# different concern not described by EC-W28-007's required context
# fields. Flagging this scoping choice for confirmation.
async def mirror_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    if request.url.path == "/engineering/mirror" and request.method == "POST":
        # Best-effort extraction from the raw request body captured by
        # FastAPI in exc.body. exc.body may be a dict (parsed JSON), a
        # non-dict type, or None (e.g. malformed JSON) — use "unavailable"
        # per EC-W28-007 when a field cannot be safely obtained.
        raw_body = exc.body if isinstance(exc.body, dict) else {}
        source_system = raw_body.get("source_system", "unavailable")
        observation_id = raw_body.get("observation_id", "unavailable")
        schema_version = raw_body.get("schema_version", "unavailable")

        # Concise failure_reason built from Pydantic error locations and
        # messages only — never the field values themselves, to avoid
        # logging runtime_context/summary/payload content per
        # EC-W28-005's Logging Restrictions.
        failure_reason = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()
        )

        logger.warning(
            "engineering_mirror.validation_failed "
            "source_system=%s observation_id=%s schema_version=%s "
            "failure_reason=%s",
            source_system,
            observation_id,
            schema_version,
            failure_reason,
        )

    # Preserve FastAPI's normal 422 response semantics exactly, by
    # delegating to FastAPI's own default handler rather than
    # constructing a response manually.
    return await request_validation_exception_handler(request, exc)

# REQ-W28-001 / EC-W28-002 (Supported Schema Version, approved, normative):
# the mirror endpoint accepts only this exact schema_version value.
# Future schema versions require a new Engineering Decision.
SUPPORTED_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────

class EOCreateRequest(BaseModel):
    eo_id: str
    title: str
    layer: Optional[str] = None
    sprint_opened: Optional[str] = None
    status: Optional[str] = "open"
    confidence: Optional[str] = None
    linked_er_id: Optional[str] = None
    linked_signal_id: Optional[int] = None
    hypothesis: Optional[str] = None
    trigger_condition: Optional[str] = None
    exit_criteria: Optional[str] = None
    monitoring_window_opened: Optional[datetime] = None
    monitoring_window_expires: Optional[datetime] = None
    notes: Optional[str] = None


class EOUpdateRequest(BaseModel):
    status: Optional[str] = None
    evidence_count: Optional[int] = None
    confidence: Optional[str] = None
    linked_er_id: Optional[str] = None
    hypothesis: Optional[str] = None
    trigger_condition: Optional[str] = None
    exit_criteria: Optional[str] = None
    monitoring_window_expires: Optional[datetime] = None
    notes: Optional[str] = None


class ERCreateRequest(BaseModel):
    er_id: str
    title: str
    review_type: Optional[str] = None
    layer: Optional[str] = None
    cross_system: Optional[bool] = None
    layer_primary: Optional[str] = None
    layer_secondary: Optional[str] = None
    sprint_opened: Optional[str] = None
    status: Optional[str] = "draft"
    confidence: Optional[str] = None
    evidence_threshold: Optional[str] = None
    hypothesis: Optional[str] = None
    verified_finding: Optional[str] = None
    owner: Optional[str] = None
    deployment_id: Optional[str] = None
    linked_req_id: Optional[str] = None
    linked_eo_id: Optional[str] = None
    promotion_eligibility: Optional[bool] = None


class ERUpdateRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    confidence: Optional[str] = None
    evidence_count: Optional[int] = None
    negative_evidence_count: Optional[int] = None
    evidence_threshold: Optional[str] = None
    verified_finding: Optional[str] = None
    last_observation: Optional[datetime] = None
    sprint_closed: Optional[str] = None
    promotion_eligibility: Optional[bool] = None
    retirement_reason: Optional[str] = None
    deployment_id: Optional[str] = None
    linked_req_id: Optional[str] = None
    linked_eo_id: Optional[str] = None


class EvidenceCreateRequest(BaseModel):
    er_id: Optional[str] = None
    eo_id: Optional[str] = None
    evidence_type: Optional[str] = None        # "positive"|"negative"|"baseline"|"transition"
    source_system: Optional[str] = None
    deployment_id: Optional[str] = None
    observation_window: Optional[str] = None
    runtime_log_ref: Optional[str] = None
    description: Optional[str] = None


class MirrorObservationRequest(BaseModel):
    """
    Request model for POST /engineering/mirror (API-W28-001 Section 2).

    Fields match ADR-W28-001 Section 4 Observation Payload exactly —
    no fields added, renamed, or omitted.

    All fields are required (no Optional / default values). This
    reflects API-W28-001 Section 3: "Each request contains one
    completed engineering observation. Partial observations are
    prohibited." ADR-W28-001 Section 4 lists these as the "minimum
    fields" every mirrored observation SHALL contain.

    Note: the MirroredObservation storage model (Task 1) declares
    runtime_context as an Optional/nullable column for storage-layer
    flexibility. That nullability is a database-column concern, not a
    relaxation of this request contract — a request missing
    runtime_context is rejected here regardless of what the storage
    column permits.

    Field types (confidence: float, runtime_context: dict,
    observation_id: str) follow ED-W28-001 Decisions 3-5.
    """
    source_system: str
    observation_id: str
    observed_at: datetime
    market: str
    timeframe: str
    structural_state: str
    behavioural_state: str
    confidence: float
    summary: str
    runtime_context: dict
    schema_version: str


# ─────────────────────────────────────────────────────────────
# Engineering Observation endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/eo", status_code=201)
def create_eo(payload: EOCreateRequest, session: Session = Depends(get_session)):
    """Create a new Engineering Observation. eo_id must be unique and immutable."""
    existing = session.exec(
        select(EngineeringObservation).where(EngineeringObservation.eo_id == payload.eo_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"EO {payload.eo_id} already exists. IDs are immutable (Article 6).",
        )
    eo = EngineeringObservation(**payload.model_dump())
    session.add(eo)
    session.commit()
    session.refresh(eo)
    return {"eo_id": eo.eo_id, "status": eo.status, "id": eo.id}


@router.get("/eo/")
def list_eos(
    status: Optional[str] = None,
    layer: Optional[str] = None,
    sprint_opened: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """List Engineering Observations with optional filters."""
    query = select(EngineeringObservation).order_by(EngineeringObservation.created_at.desc())
    if status:
        query = query.where(EngineeringObservation.status == status)
    if layer:
        query = query.where(EngineeringObservation.layer == layer)
    if sprint_opened:
        query = query.where(EngineeringObservation.sprint_opened == sprint_opened)
    return session.exec(query.limit(limit)).all()


@router.get("/eo/{eo_id}")
def get_eo(eo_id: str, session: Session = Depends(get_session)):
    """Retrieve a single Engineering Observation by immutable eo_id."""
    eo = session.exec(
        select(EngineeringObservation).where(EngineeringObservation.eo_id == eo_id)
    ).first()
    if not eo:
        raise HTTPException(status_code=404, detail=f"EO {eo_id} not found.")
    return eo


@router.patch("/eo/{eo_id}")
def update_eo(eo_id: str, payload: EOUpdateRequest, session: Session = Depends(get_session)):
    """
    Update mutable fields on an Engineering Observation.
    eo_id is immutable and cannot be changed (Article 6).
    """
    eo = session.exec(
        select(EngineeringObservation).where(EngineeringObservation.eo_id == eo_id)
    ).first()
    if not eo:
        raise HTTPException(status_code=404, detail=f"EO {eo_id} not found.")
    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(eo, field, value)
    eo.updated_at = datetime.utcnow()
    session.add(eo)
    session.commit()
    session.refresh(eo)
    return {"eo_id": eo.eo_id, "status": eo.status, "evidence_count": eo.evidence_count}


# ─────────────────────────────────────────────────────────────
# Engineering Review endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/er", status_code=201)
def create_er(payload: ERCreateRequest, session: Session = Depends(get_session)):
    """Create a new Engineering Review. er_id must be unique and immutable."""
    existing = session.exec(
        select(EngineeringReview).where(EngineeringReview.er_id == payload.er_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"ER {payload.er_id} already exists. IDs are immutable (Article 6).",
        )
    er = EngineeringReview(**payload.model_dump())
    session.add(er)
    session.commit()
    session.refresh(er)
    return {"er_id": er.er_id, "status": er.status, "id": er.id}


@router.get("/er/")
def list_ers(
    status: Optional[str] = None,
    layer: Optional[str] = None,
    sprint_opened: Optional[str] = None,
    confidence: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """List Engineering Reviews with optional filters."""
    query = select(EngineeringReview).order_by(EngineeringReview.created_at.desc())
    if status:
        query = query.where(EngineeringReview.status == status)
    if layer:
        query = query.where(EngineeringReview.layer == layer)
    if sprint_opened:
        query = query.where(EngineeringReview.sprint_opened == sprint_opened)
    if confidence:
        query = query.where(EngineeringReview.confidence == confidence)
    return session.exec(query.limit(limit)).all()


@router.get("/er/{er_id}")
def get_er(er_id: str, session: Session = Depends(get_session)):
    """Retrieve a single Engineering Review by immutable er_id."""
    er = session.exec(
        select(EngineeringReview).where(EngineeringReview.er_id == er_id)
    ).first()
    if not er:
        raise HTTPException(status_code=404, detail=f"ER {er_id} not found.")
    return er


@router.patch("/er/{er_id}")
def update_er(er_id: str, payload: ERUpdateRequest, session: Session = Depends(get_session)):
    """
    Update mutable fields on an Engineering Review.
    er_id is immutable and cannot be changed (Article 6).
    Promotion decisions belong to Engineering Review (ChatGPT) — Article 9.
    """
    er = session.exec(
        select(EngineeringReview).where(EngineeringReview.er_id == er_id)
    ).first()
    if not er:
        raise HTTPException(status_code=404, detail=f"ER {er_id} not found.")
    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(er, field, value)
    er.updated_at = datetime.utcnow()
    session.add(er)
    session.commit()
    session.refresh(er)
    return {
        "er_id": er.er_id,
        "status": er.status,
        "confidence": er.confidence,
        "evidence_count": er.evidence_count,
    }


# ─────────────────────────────────────────────────────────────
# Engineering Evidence endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/evidence", status_code=201)
def record_evidence(payload: EvidenceCreateRequest, session: Session = Depends(get_session)):
    """
    Record an evidence increment against an ER or EO.
    At least one of er_id or eo_id must be provided.
    Positive and negative evidence are equally valid (Article 5).
    """
    if not payload.er_id and not payload.eo_id:
        raise HTTPException(
            status_code=422,
            detail="At least one of er_id or eo_id must be provided.",
        )
    ev = EngineeringEvidence(**payload.model_dump())
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return {
        "id": ev.id,
        "er_id": ev.er_id,
        "eo_id": ev.eo_id,
        "evidence_type": ev.evidence_type,
        "recorded_at": ev.recorded_at,
    }


@router.get("/evidence/{ref_id}")
def list_evidence(
    ref_id: str,
    ref_type: str = "er",      # "er" | "eo"
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """
    List all evidence records for an ER or EO.
    ref_type="er" (default) queries by er_id.
    ref_type="eo" queries by eo_id.
    """
    if ref_type == "eo":
        query = select(EngineeringEvidence).where(
            EngineeringEvidence.eo_id == ref_id
        ).order_by(EngineeringEvidence.recorded_at.desc())
    else:
        query = select(EngineeringEvidence).where(
            EngineeringEvidence.er_id == ref_id
        ).order_by(EngineeringEvidence.recorded_at.desc())
    return session.exec(query.limit(limit)).all()


# ─────────────────────────────────────────────────────────────
# Mirrored Observation endpoint (REQ-W28-001 / ADR-W28-001 / API-W28-001)
#
# Sprint B-1 status — implements:
#   Task 2  — request model + payload validation (MirrorObservationRequest)
#   Task 3  — schema_version verification (EC-W28-002)
#   Task 4  — idempotency lookup (source_system + observation_id)
#   Task 5  — duplicate-request behaviour: return existing, 200 OK
#             (EC-W28-003)
#   Task 6  — new-record persistence
#   Task 8  — success response (repository acknowledgement, 201 Created
#             for new records; existing-record return is 200 per Task 5)
#   Task 9/10 — structured logging via Python's standard logging module
#             (EC-W28-005), module-level logger declared above.
#
# Explicitly NOT implemented in this task (deferred to later WBS tasks):
#   - retrieval endpoint(s) (Task 11 — see separate GET route below)
#   - tests (Brain Ops-side tests to follow)
#   - Reflex-side integration (blocked — no Reflex source evidence)
# ─────────────────────────────────────────────────────────────

@router.post("/mirror", status_code=201)
def mirror_observation(
    payload: MirrorObservationRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Receive one completed Reflex engineering observation for repository
    persistence (API-W28-001 Section 2: POST /engineering/mirror).

    Implements Tasks 2, 3, 4, 5, 6, 8, 9/10 per the approved WBS and
    EC-W28-002 (schema version), EC-W28-003 (duplicate behaviour), and
    EC-W28-005 (logging baseline).
    """

    # ── Task 3: schema_version verification (EC-W28-002) ───────
    if payload.schema_version != SUPPORTED_SCHEMA_VERSION:
        # engineering_mirror.validation_failed — WARNING
        # EC-W28-005 Logging Restrictions: do not log full payload,
        # runtime_context content, or complete summaries.
        logger.warning(
            "engineering_mirror.validation_failed "
            "source_system=%s observation_id=%s schema_version=%s "
            "failure_reason=%s",
            payload.source_system,
            payload.observation_id,
            payload.schema_version,
            "unsupported_schema_version",
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported schema_version '{payload.schema_version}'. "
                f"Only '{SUPPORTED_SCHEMA_VERSION}' is currently supported "
                f"(EC-W28-002)."
            ),
        )

    # engineering_mirror.request_received — INFO
    logger.info(
        "engineering_mirror.request_received "
        "source_system=%s observation_id=%s schema_version=%s",
        payload.source_system,
        payload.observation_id,
        payload.schema_version,
    )

    # ── Task 4: idempotency lookup (source_system + observation_id) ──
    # ADR-W28-001 Section 4 Idempotency Rule / API-W28-001 Section 10:
    # this pair uniquely identifies one engineering observation.
    existing = session.exec(
        select(MirroredObservation).where(
            MirroredObservation.source_system == payload.source_system,
            MirroredObservation.observation_id == payload.observation_id,
        )
    ).first()

    # ── Task 5: duplicate branch (EC-W28-003) ───────────────────
    # Duplicate requests are NOT errors. Return the existing
    # observation. No additional record is created.
    if existing:
        # engineering_mirror.duplicate_returned — INFO
        logger.info(
            "engineering_mirror.duplicate_returned "
            "source_system=%s observation_id=%s stored_record_id=%s",
            payload.source_system,
            payload.observation_id,
            existing.id,
        )
        # EC-W28-003 specifies HTTP 200 for the duplicate branch, while
        # the route decorator's status_code=201 governs the default
        # (new-record) case. FastAPI's documented mechanism for a
        # per-branch override is to accept a `response: Response`
        # parameter and set response.status_code directly — no other
        # approved document specifies an alternative mechanism, and
        # this is the minimal standard FastAPI pattern for the case.
        response.status_code = 200
        return existing

    # ── Task 6: new-record persistence ──────────────────────────
    try:
        record = MirroredObservation(
            source_system=payload.source_system,
            observation_id=payload.observation_id,
            observed_at=payload.observed_at,
            market=payload.market,
            timeframe=payload.timeframe,
            structural_state=payload.structural_state,
            behavioural_state=payload.behavioural_state,
            confidence=payload.confidence,
            summary=payload.summary,
            runtime_context=payload.runtime_context,
            schema_version=payload.schema_version,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
    except Exception as exc:
        session.rollback()
        # engineering_mirror.persistence_failed — ERROR, with traceback
        # EC-W28-005: "Logging failures must not cause observation
        # persistence to fail" — this is the inverse case (persistence
        # failure being logged), traceback preserved via exc_info.
        logger.error(
            "engineering_mirror.persistence_failed "
            "source_system=%s observation_id=%s exception_type=%s",
            payload.source_system,
            payload.observation_id,
            type(exc).__name__,
            exc_info=True,
        )
        # ADR-W28-001 Section 5, FM-5 (Repository Failure): errors must
        # be observable and never silently discard evidence. Re-raising
        # as a 500 surfaces the failure to the caller (Reflex), which
        # per FM-5 must treat delivery as unsuccessful and is not
        # interrupted by this failure (Reflex-side isolation is a
        # Reflex-side concern, out of scope here).
        raise HTTPException(
            status_code=500,
            detail="Repository failure during observation persistence.",
        )

    # engineering_mirror.persisted — INFO
    logger.info(
        "engineering_mirror.persisted "
        "source_system=%s observation_id=%s stored_record_id=%s",
        payload.source_system,
        payload.observation_id,
        record.id,
    )

    # REQ-B2-001 / EA-007 / EA-009: Evidence Lifecycle.
    # Additive to Sprint B-1's established behavior — invoked only on
    # this new-record path (never on the duplicate-return path above,
    # since a duplicate is not a new accepted Observation per REQ-B2-001
    # AC-001). Failures here are logged (inside the adapter) but do NOT
    # alter this endpoint's Sprint B-1 response contract: no Sprint B-2
    # document authorizes changing Sprint B-1's established status
    # codes or response shape, and the Sprint B-2 handoff explicitly
    # states its objective is NOT to modify Sprint B-1. This
    # interpretation is noted in the accompanying Implementation Report
    # for Engineering Authority's awareness.
    try:
        evidence_lifecycle.create_evidence_for_mirrored_observation(record, session)
    except evidence_lifecycle.EvidenceCreationError:
        # Already logged inside the adapter
        # (evidence_lifecycle.creation_or_validation_failed).
        # Intentionally not re-raised — see note above.
        pass

    # ── Task 8: success response ─────────────────────────────────
    # API-W28-001 Section 4: 201 Created (set via route decorator
    # status_code=201, applies to this — the default — branch).
    # Response body is repository acknowledgement only; the original
    # observation payload is not modified (returned as persisted).
    return record


# ─────────────────────────────────────────────────────────────
# Task 11: Retrieval endpoint(s) for mirrored observations
# REQ-W28-001 FR-10: retrieval available for Engineering Review,
# Evidence, Weekly Engineering Packages, and future Engineering
# Intelligence. Retrieval does not authorize modification.
# Follows the existing list/detail pattern used by /eo/, /eo/{eo_id},
# /er/, /er/{er_id} above, and the filter/limit pattern from
# app/api/reflex.py (conditional `where` reassignment, capped limit).
# ─────────────────────────────────────────────────────────────

@router.get("/mirror/")
def list_mirrored_observations(
    source_system: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """List mirrored observations with optional filters (FR-10)."""
    query = select(MirroredObservation).order_by(
        MirroredObservation.mirrored_at.desc()
    )
    if source_system:
        query = query.where(MirroredObservation.source_system == source_system)
    if market:
        query = query.where(MirroredObservation.market == market)
    return session.exec(query.limit(min(limit, 200))).all()


@router.get("/mirror/{source_system}/{observation_id}")
def get_mirrored_observation(
    source_system: str,
    observation_id: str,
    session: Session = Depends(get_session),
):
    """
    Retrieve a single mirrored observation by its composite identity
    (source_system, observation_id) — EC-W28-006 (Mirror Retrieval
    Identity, approved, normative).

    Both path values are mandatory. No default or inferred
    source_system is applied (EC-W28-006 explicitly disallows this;
    a previously implemented default of "reflex" was rejected and has
    been removed).
    """
    record = session.exec(
        select(MirroredObservation).where(
            MirroredObservation.source_system == source_system,
            MirroredObservation.observation_id == observation_id,
        )
    ).first()
    if not record:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No mirrored observation found for "
                f"source_system={source_system}, observation_id={observation_id}."
            ),
        )
    return record


# ─────────────────────────────────────────────────────────────
# Health summary
# ─────────────────────────────────────────────────────────────

@router.get("/status")
def engineering_status(session: Session = Depends(get_session)):
    """
    Engineering Database health summary.
    Returns counts of EOs, ERs, and Evidence records by status.
    """
    all_eos = session.exec(select(EngineeringObservation)).all()
    all_ers = session.exec(select(EngineeringReview)).all()
    all_ev  = session.exec(select(EngineeringEvidence)).all()

    eo_by_status: dict = {}
    for eo in all_eos:
        eo_by_status[eo.status] = eo_by_status.get(eo.status, 0) + 1

    er_by_status: dict = {}
    for er in all_ers:
        er_by_status[er.status] = er_by_status.get(er.status, 0) + 1

    return {
        "eo_total":      len(all_eos),
        "eo_by_status":  eo_by_status,
        "er_total":      len(all_ers),
        "er_by_status":  er_by_status,
        "evidence_total": len(all_ev),
    }
