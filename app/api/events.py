"""
api/events.py

Structured event logging endpoint.
Events are written here by btc-brain-ops internally,
and read by Reflex Engine via /reflex/* endpoints.

POST /events/log        — log a system event
GET  /events/           — list recent events (with filters)
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import EventLog, get_session

router = APIRouter(prefix="/events", tags=["events"])


class EventLogRequest(BaseModel):
    event_type: str
    source: Optional[str] = None
    signal_id: Optional[int] = None
    week: Optional[str] = None
    direction: Optional[str] = None
    result: Optional[str] = None
    state_from: Optional[str] = None
    state_to: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[str] = None


def log_event(
    session: Session,
    event_type: str,
    source: str = None,
    signal_id: int = None,
    week: str = None,
    direction: str = None,
    result: str = None,
    state_from: str = None,
    state_to: str = None,
    reason: str = None,
    metadata: str = None,
) -> EventLog:
    """
    Internal helper — call this from other API handlers to log events.
    Lightweight: one DB insert, no blocking.
    """
    ev = EventLog(
        event_type=event_type,
        source=source,
        signal_id=signal_id,
        week=week,
        direction=direction,
        result=result,
        state_from=state_from,
        state_to=state_to,
        reason=reason,
        metadata=metadata,
    )
    session.add(ev)
    session.commit()
    return ev


@router.post("/log", status_code=201, tags=["events"])
def log_event_endpoint(
    payload: EventLogRequest,
    session: Session = Depends(get_session),
):
    """Log a system event. Used internally by btc-brain-ops components."""
    ev = log_event(session=session, **payload.model_dump())
    return {"event_id": ev.id, "event_type": ev.event_type}


@router.get("/", tags=["events"])
def list_events(
    event_type: Optional[str] = None,
    source: Optional[str] = None,
    week: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """List recent events with optional filters."""
    query = select(EventLog).order_by(EventLog.event_ts.desc())
    if event_type:
        query = query.where(EventLog.event_type == event_type)
    if source:
        query = query.where(EventLog.source == source)
    if week:
        query = query.where(EventLog.week == week)
    return session.exec(query.limit(limit)).all()
