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
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.database.models import (
    EngineeringObservation,
    EngineeringReview,
    EngineeringEvidence,
)

router = APIRouter(prefix="/engineering", tags=["engineering"])


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
