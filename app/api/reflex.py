"""
api/reflex.py

READ-ONLY endpoints for Reflex Engine external analysis layer.

ARCHITECTURE RULE:
  Reflex Engine may ONLY read from these endpoints.
  Reflex CANNOT write, modify, or execute anything.
  All endpoints are GET only — no POST, no PUT, no DELETE.
  If Reflex Engine crashes, btc-brain-ops continues normally.

Endpoints:
  GET /reflex/signals/latest          — most recent N signals
  GET /reflex/signals/week/{week}     — all signals for a week
  GET /reflex/market/structure        — current market structure summary
  GET /reflex/lifecycle/recent        — recent lifecycle state transitions
  GET /reflex/events/recent           — recent system events
  GET /reflex/intelligence/week/{week} — full week analytics for Reflex
  GET /reflex/status                  — system health + schema version
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import Signal, LifecycleEvent, EventLog, get_session
from app.signal_lifecycle_tracker.tracker import compute_week_analytics

router = APIRouter(prefix="/reflex", tags=["reflex-readonly"])

# Schema version — bump when fields change so Reflex knows to adapt
SCHEMA_VERSION = "1.0.0"


@router.get("/status")
def reflex_status():
    """
    System status for Reflex Engine.
    Reports schema version, system identity, and access rules.
    """
    return {
        "system": "btc-brain-ops",
        "schema_version": SCHEMA_VERSION,
        "access": "READ ONLY",
        "rules": [
            "Reflex may not write, modify, or execute.",
            "Reflex may not override OPS decisions.",
            "Reflex may not block or suppress signals.",
            "Main system operates independently of Reflex.",
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/signals/latest")
def latest_signals(
    limit: int = 10,
    direction: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """
    Most recent signals. Reflex uses this for real-time context.
    Returns universal schema fields including quality metadata.
    """
    query = select(Signal).order_by(Signal.signal_ts.desc())
    if direction:
        query = query.where(Signal.direction == direction)
    signals = session.exec(query.limit(min(limit, 50))).all()
    return {
        "count": len(signals),
        "schema_version": SCHEMA_VERSION,
        "signals": [_signal_summary(s) for s in signals],
    }


@router.get("/signals/week/{week}")
def signals_by_week(week: str, session: Session = Depends(get_session)):
    """All signals for a specific week with full quality metadata."""
    signals = session.exec(
        select(Signal).where(Signal.week == week).order_by(Signal.signal_ts)
    ).all()
    if not signals:
        raise HTTPException(status_code=404, detail=f"No signals for {week}")
    return {
        "week": week,
        "count": len(signals),
        "schema_version": SCHEMA_VERSION,
        "signals": [_signal_summary(s) for s in signals],
    }


@router.get("/market/structure")
def market_structure(session: Session = Depends(get_session)):
    """
    Current market structure summary derived from recent signals.
    Reflex uses this for regime and participation context.
    """
    recent = session.exec(
        select(Signal)
        .where(Signal.result == "OPEN")
        .order_by(Signal.signal_ts.desc())
        .limit(5)
    ).all()

    last_10 = session.exec(
        select(Signal).order_by(Signal.signal_ts.desc()).limit(10)
    ).all()

    closed = [s for s in last_10 if s.result in ("WIN", "LOSS")]
    wins = [s for s in closed if s.result == "WIN"]

    # Derive dominant regime from recent signals
    regimes = [s.regime for s in last_10 if s.regime]
    dominant_regime = max(set(regimes), key=regimes.count) if regimes else "UNKNOWN"

    # OI state tendency
    oi_states = [s.oi_state for s in last_10 if s.oi_state]
    dominant_oi = max(set(oi_states), key=oi_states.count) if oi_states else "unknown"

    # Liquidity risk tendency
    liq_risks = [s.liquidity_risk for s in last_10 if s.liquidity_risk]
    dominant_liq = max(set(liq_risks), key=liq_risks.count) if liq_risks else "unknown"

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "open_trades": len(recent),
        "recent_win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "dominant_regime": dominant_regime,
        "dominant_oi_state": dominant_oi,
        "dominant_liquidity_risk": dominant_liq,
        "confidence_factors": _aggregate_confidence_reasons(last_10),
        "last_signal_ts": last_10[0].signal_ts.isoformat() if last_10 else None,
    }


@router.get("/lifecycle/recent")
def recent_lifecycle(
    limit: int = 20,
    session: Session = Depends(get_session),
):
    """
    Recent lifecycle state transitions.
    Reflex uses this for continuation behavior analysis.
    """
    events = session.exec(
        select(LifecycleEvent)
        .order_by(LifecycleEvent.event_ts.desc())
        .limit(min(limit, 100))
    ).all()

    return {
        "count": len(events),
        "events": [
            {
                "signal_id": e.signal_id,
                "event_ts": e.event_ts.isoformat(),
                "continuation_state": e.continuation_state,
                "participation_quality": e.participation_quality,
                "volatility_event": e.volatility_event,
                "oi_expanding": e.oi_expanding,
                "follow_through": e.follow_through,
            }
            for e in events
        ],
    }


@router.get("/events/recent")
def recent_events(
    event_type: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    """Recent system events. Reflex uses this for failure/recovery patterns."""
    query = select(EventLog).order_by(EventLog.event_ts.desc())
    if event_type:
        query = query.where(EventLog.event_type == event_type)
    events = session.exec(query.limit(min(limit, 200))).all()
    return {
        "count": len(events),
        "events": [
            {
                "id": e.id,
                "event_ts": e.event_ts.isoformat(),
                "event_type": e.event_type,
                "source": e.source,
                "signal_id": e.signal_id,
                "direction": e.direction,
                "result": e.result,
                "state_from": e.state_from,
                "state_to": e.state_to,
                "reason": e.reason,
            }
            for e in events
        ],
    }


@router.get("/intelligence/week/{week}")
def week_intelligence(week: str, session: Session = Depends(get_session)):
    """
    Full week analytics for Reflex Engine analysis.
    This is the primary data feed for Reflex adaptive intelligence.
    Identical to /signals/analytics/week/{week} but namespaced for Reflex.
    """
    result = compute_week_analytics(week=week, session=session)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    result["schema_version"] = SCHEMA_VERSION
    result["reflex_note"] = (
        "READ ONLY. Reflex may analyze but not modify. "
        "Main system operates independently."
    )
    return result


# ── Internal helpers ─────────────────────────────────────────

def _signal_summary(s: Signal) -> dict:
    """Compact universal schema representation of a signal."""
    return {
        "id": s.id,
        "week": s.week,
        "direction": s.direction,
        "source": s.source,
        "session": s.session,
        "signal_ts": s.signal_ts.isoformat(),
        "entry_price": s.entry_price,
        "tp_price": s.tp_price,
        "sl_price": s.sl_price,
        # Market context
        "regime": s.regime,
        "oi_state": s.oi_state,
        "trend_4h": s.trend_4h,
        "rsi_at_entry": s.rsi_at_entry,
        # Quality metadata
        "setup_type": s.setup_type,
        "market_regime": s.market_regime,
        "liquidity_risk": s.liquidity_risk,
        "confidence_reason": s.confidence_reason,
        "breakout_quality": s.breakout_quality,
        "volatility_state": s.volatility_state,
        "risk_score": s.risk_score,
        # Outcome
        "result": s.result,
        "net_pnl_usd": s.net_pnl_usd,
        "rr_achieved": s.rr_achieved,
        "final_continuation_state": s.final_continuation_state,
    }


def _aggregate_confidence_reasons(signals: list) -> dict:
    """Count confidence factors across recent signals."""
    counts: dict = {}
    for s in signals:
        if s.confidence_reason:
            for factor in s.confidence_reason.split("+"):
                factor = factor.strip()
                if factor:
                    counts[factor] = counts.get(factor, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))
