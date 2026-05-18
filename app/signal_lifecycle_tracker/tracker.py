"""
signal_lifecycle_tracker/tracker.py

Analyzes the full lifecycle of a signal from activation to close.

Produces:
  - continuation half-life (how long did quality persist after entry?)
  - state path (healthy → weakening → false_recovery → exhausted)
  - participation persistence duration
  - decay point (when did continuation quality first fall below threshold?)
  - survivability summary

CONTINUATION HALF-LIFE:
  long        — quality persisted through most of the trade
  moderate    — quality held for ~half the trade duration
  short       — quality decayed within a few candles of entry
  immediate   — decay began at or before entry
"""

from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select

from app.database.models import Signal, LifecycleEvent
from app.continuation_state_logger.classifier import survivability_score


# Half-life thresholds (score out of 5)
HIGH_QUALITY_THRESHOLD = 3   # healthy / recovering / weakening
LOW_QUALITY_THRESHOLD = 2    # unstable_transition, false_recovery, decaying


def compute_lifecycle_summary(signal_id: int, session: Session) -> dict:
    """
    Build a complete lifecycle summary for a signal.
    Returns a dict suitable for the weekly intelligence exporter.
    """
    signal = session.get(Signal, signal_id)
    if not signal:
        return {"error": f"Signal {signal_id} not found"}

    events = session.exec(
        select(LifecycleEvent)
        .where(LifecycleEvent.signal_id == signal_id)
        .order_by(LifecycleEvent.event_ts)
    ).all()

    if not events:
        return {"signal_id": signal_id, "error": "No lifecycle events recorded"}

    state_path = [e.continuation_state for e in events]
    scores = [survivability_score(s) for s in state_path]

    # Decay point: first event where score drops below HIGH_QUALITY_THRESHOLD
    decay_index = _find_decay_index(scores)
    decay_state = state_path[decay_index] if decay_index is not None else None
    decay_ts = events[decay_index].event_ts if decay_index is not None else None

    # Half-life classification
    half_life = _classify_half_life(
        events=events,
        decay_index=decay_index,
        signal=signal,
    )

    # Participation persistence: fraction of events with persistent/recovering participation
    participation_events = [
        e for e in events
        if e.participation_quality in ("persistent",)
    ]
    participation_ratio = len(participation_events) / len(events) if events else 0.0

    # Volatility trap events
    trap_events = [
        e for e in events
        if e.volatility_event in ("liquidity_sweep", "liquidation", "exhaustion")
    ]

    return {
        "signal_id": signal_id,
        "direction": signal.direction,
        "week": signal.week,
        "session": signal.session,
        "result": signal.result,
        "pnl_pct": signal.pnl_pct,
        "state_path": state_path,
        "initial_state": state_path[0] if state_path else None,
        "final_state": signal.final_continuation_state or state_path[-1],
        "decay_point": decay_state,
        "decay_ts": decay_ts.isoformat() if decay_ts else None,
        "continuation_half_life": half_life,
        "participation_persistence_ratio": round(participation_ratio, 2),
        "trap_events_count": len(trap_events),
        "total_lifecycle_events": len(events),
    }


def _find_decay_index(scores: list[int]) -> Optional[int]:
    """Return index of first score below HIGH_QUALITY_THRESHOLD, or None."""
    for i, score in enumerate(scores):
        if score < HIGH_QUALITY_THRESHOLD:
            return i
    return None


def _classify_half_life(
    events: list[LifecycleEvent],
    decay_index: Optional[int],
    signal: Signal,
) -> str:
    """
    Classify continuation half-life based on when decay began relative to trade duration.

    If no decay occurred → long
    If decay at first event → immediate
    """
    if not events:
        return "unknown"

    if decay_index is None:
        return "long"

    if decay_index == 0:
        return "immediate"

    # Use fraction of events before decay as proxy for time fraction
    fraction = decay_index / len(events)

    if fraction >= 0.6:
        return "long"
    if fraction >= 0.3:
        return "moderate"
    if fraction > 0:
        return "short"
    return "immediate"


def get_week_lifecycle_summaries(week: str, session: Session) -> list[dict]:
    """Return lifecycle summaries for all signals in a given week."""
    signals = session.exec(
        select(Signal).where(Signal.week == week)
    ).all()

    return [
        compute_lifecycle_summary(signal_id=s.id, session=session)
        for s in signals
    ]
