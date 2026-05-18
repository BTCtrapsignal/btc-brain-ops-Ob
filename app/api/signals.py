"""
api/signals.py

Signal ingestion, lifecycle tracking, and PnL recording.

POST /signals/ingest              — new signal from production bot
POST /signals/{id}/close          — record outcome + PnL
POST /signals/{id}/lifecycle      — manual state observation
POST /signals/{id}/auto-lifecycle — observation-driven state transition
GET  /signals/                    — list (filter by week/direction/result)
GET  /signals/{id}                — signal + full lifecycle
GET  /signals/stats/week/{week}       — PnL summary for a week
GET  /signals/analytics/week/{week}  — full lifecycle intelligence (for ChatGPT Saturday)
GET  /signals/{id}/summary           — lifecycle summary for one signal
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select, func

from app.database import Signal, LifecycleEvent, get_session
from app.signal_lifecycle_tracker.tracker import (
    compute_lifecycle_summary,
    compute_week_analytics,
)
from app.continuation_state_logger.classifier import (
    classify_initial_hypothesis,
    classify_transition_full,
)

router = APIRouter(prefix="/signals", tags=["signals"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignalIngestRequest(BaseModel):
    week: str
    direction: str
    entry_price: float
    tp_price: float
    sl_price: float
    session: str
    signal_ts: Optional[datetime] = None
    regime: Optional[str] = None
    oi_state: Optional[str] = None
    rsi_at_entry: Optional[float] = None
    atr_at_entry: Optional[float] = None
    trend_4h: Optional[str] = None


class SignalCloseRequest(BaseModel):
    result: str                         # "WIN" | "LOSS"
    exit_price: float
    exit_ts: Optional[datetime] = None

    # PnL — fill as many as you have; net_pnl_usd is the most important
    position_size_usd: Optional[float] = None
    capital_pct: Optional[float] = None
    fee_usd: Optional[float] = None
    gross_pnl_usd: Optional[float] = None
    net_pnl_usd: Optional[float] = None     # PnL after fee — real number
    pnl_pct: Optional[float] = None
    net_pnl_pct: Optional[float] = None
    rr_achieved: Optional[float] = None


class LifecycleEventRequest(BaseModel):
    continuation_state: str
    participation_quality: Optional[str] = None
    volatility_event: Optional[str] = None
    oi_expanding: Optional[bool] = None
    volume_persisting: Optional[bool] = None
    follow_through: Optional[bool] = None
    note: Optional[str] = None


class AutoLifecycleRequest(BaseModel):
    oi_expanding: Optional[bool] = None
    volume_persisting: Optional[bool] = None
    follow_through: Optional[bool] = None
    volatility_event: Optional[str] = None
    participation_quality: Optional[str] = None
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ingest", status_code=201)
def ingest_signal(payload: SignalIngestRequest, session: Session = Depends(get_session)):
    """Receive new signal. Classifies opening hypothesis automatically."""
    signal = Signal(
        week=payload.week,
        direction=payload.direction,
        entry_price=payload.entry_price,
        tp_price=payload.tp_price,
        sl_price=payload.sl_price,
        session=payload.session,
        signal_ts=payload.signal_ts or datetime.utcnow(),
        regime=payload.regime,
        oi_state=payload.oi_state,
        rsi_at_entry=payload.rsi_at_entry,
        atr_at_entry=payload.atr_at_entry,
        trend_4h=payload.trend_4h,
        result="OPEN",
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)

    hyp = classify_initial_hypothesis(
        direction=signal.direction,
        regime=signal.regime,
        oi_state=signal.oi_state,
        trend_4h=signal.trend_4h,
    )

    note = (
        f"Opening hypothesis. "
        f"Confidence: {hyp.confidence:.0%}. "
        f"{hyp.reasoning}"
        + (f" Memory anchor: {hyp.memory_anchor}." if hyp.memory_anchor else "")
    )

    event = LifecycleEvent(
        signal_id=signal.id,
        continuation_state=hyp.state,
        participation_quality=_oi_to_participation(signal.oi_state),
        note=note,
    )
    session.add(event)
    session.commit()

    return {
        "signal_id": signal.id,
        "opening_hypothesis": hyp.as_dict(),
    }


@router.post("/{signal_id}/close")
def close_signal(
    signal_id: int,
    payload: SignalCloseRequest,
    session: Session = Depends(get_session),
):
    """
    Record trade outcome and PnL.
    net_pnl_usd is the most important field — fill it whenever possible.
    """
    signal = session.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.result = payload.result
    signal.exit_price = payload.exit_price
    signal.exit_ts = payload.exit_ts or datetime.utcnow()

    # PnL fields — store whatever is provided
    signal.position_size_usd = payload.position_size_usd
    signal.capital_pct = payload.capital_pct
    signal.fee_usd = payload.fee_usd
    signal.gross_pnl_usd = payload.gross_pnl_usd
    signal.net_pnl_usd = payload.net_pnl_usd
    signal.pnl_pct = payload.pnl_pct
    signal.net_pnl_pct = payload.net_pnl_pct
    signal.rr_achieved = payload.rr_achieved

    # Auto-compute net_pnl_usd if not provided but components are
    if signal.net_pnl_usd is None and signal.gross_pnl_usd is not None and signal.fee_usd is not None:
        signal.net_pnl_usd = signal.gross_pnl_usd - signal.fee_usd

    # Final continuation state from last lifecycle event
    last_event = session.exec(
        select(LifecycleEvent)
        .where(LifecycleEvent.signal_id == signal_id)
        .order_by(LifecycleEvent.event_ts.desc())
    ).first()
    if last_event:
        signal.final_continuation_state = last_event.continuation_state

    session.add(signal)
    session.commit()

    return {
        "signal_id": signal_id,
        "result": payload.result,
        "net_pnl_usd": signal.net_pnl_usd,
        "final_continuation_state": signal.final_continuation_state,
    }


@router.post("/{signal_id}/lifecycle")
def add_lifecycle_event(
    signal_id: int,
    payload: LifecycleEventRequest,
    session: Session = Depends(get_session),
):
    """Manual lifecycle observation — you specify the state directly."""
    if not session.get(Signal, signal_id):
        raise HTTPException(status_code=404, detail="Signal not found")

    event = LifecycleEvent(
        signal_id=signal_id,
        continuation_state=payload.continuation_state,
        participation_quality=payload.participation_quality,
        volatility_event=payload.volatility_event,
        oi_expanding=payload.oi_expanding,
        volume_persisting=payload.volume_persisting,
        follow_through=payload.follow_through,
        note=payload.note,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    return {"event_id": event.id, "continuation_state": event.continuation_state}


@router.post("/{signal_id}/auto-lifecycle")
def auto_lifecycle_event(
    signal_id: int,
    payload: AutoLifecycleRequest,
    session: Session = Depends(get_session),
):
    """
    Observation-driven lifecycle update.
    Supply what you observed — classifier computes the next state.
    Missing fields = not observed (preserves uncertainty, does not degrade).
    """
    if not session.get(Signal, signal_id):
        raise HTTPException(status_code=404, detail="Signal not found")

    last_event = session.exec(
        select(LifecycleEvent)
        .where(LifecycleEvent.signal_id == signal_id)
        .order_by(LifecycleEvent.event_ts.desc())
    ).first()
    current_state = last_event.continuation_state if last_event else "unstable_transition"

    transition = classify_transition_full(
        current_state=current_state,
        oi_expanding=payload.oi_expanding,
        volume_persisting=payload.volume_persisting,
        follow_through=payload.follow_through,
        volatility_event=payload.volatility_event,
    )

    note = (
        f"Auto: {current_state} → {transition.next_state}. "
        f"Weight: {transition.observation_weight}. "
        f"Delta: {transition.confidence_delta:+.2f}. "
        f"{transition.reasoning}"
        + (f" | {payload.note}" if payload.note else "")
    )

    event = LifecycleEvent(
        signal_id=signal_id,
        continuation_state=transition.next_state,
        participation_quality=payload.participation_quality,
        volatility_event=payload.volatility_event,
        oi_expanding=payload.oi_expanding,
        volume_persisting=payload.volume_persisting,
        follow_through=payload.follow_through,
        note=note,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    return {
        "event_id": event.id,
        "previous_state": current_state,
        "transition": transition.as_dict(),
    }


@router.get("/stats/week/{week}")
def week_stats(week: str, session: Session = Depends(get_session)):
    """
    PnL summary for a given week.
    The most important endpoint for understanding if the system actually works.
    """
    signals = session.exec(
        select(Signal).where(Signal.week == week)
    ).all()

    if not signals:
        raise HTTPException(status_code=404, detail=f"No signals found for {week}")

    closed = [s for s in signals if s.result in ("WIN", "LOSS")]
    wins = [s for s in closed if s.result == "WIN"]
    losses = [s for s in closed if s.result == "LOSS"]
    open_trades = [s for s in signals if s.result == "OPEN"]

    # PnL aggregates — only from signals that have net_pnl_usd
    pnl_signals = [s for s in closed if s.net_pnl_usd is not None]
    total_net_pnl = sum(s.net_pnl_usd for s in pnl_signals)
    total_fees = sum(s.fee_usd for s in closed if s.fee_usd is not None)
    win_pnl = sum(s.net_pnl_usd for s in wins if s.net_pnl_usd is not None)
    loss_pnl = sum(s.net_pnl_usd for s in losses if s.net_pnl_usd is not None)

    # Expectancy = (WR × avg_win) + ((1-WR) × avg_loss)
    wr = len(wins) / len(closed) if closed else 0
    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = loss_pnl / len(losses) if losses else 0
    expectancy = (wr * avg_win) + ((1 - wr) * avg_loss) if closed else 0

    # By direction
    longs = [s for s in closed if s.direction == "LONG"]
    shorts = [s for s in closed if s.direction == "SHORT"]
    long_wins = [s for s in longs if s.result == "WIN"]
    short_wins = [s for s in shorts if s.result == "WIN"]

    return {
        "week": week,
        "signals": {
            "total": len(signals),
            "closed": len(closed),
            "open": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(wr * 100, 1),
        },
        "pnl": {
            "total_net_pnl_usd": round(total_net_pnl, 2),
            "win_pnl_usd": round(win_pnl, 2),
            "loss_pnl_usd": round(loss_pnl, 2),
            "total_fees_usd": round(total_fees, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "expectancy_per_trade_usd": round(expectancy, 2),
            "pnl_tracked": f"{len(pnl_signals)}/{len(closed)} trades",
        },
        "by_direction": {
            "LONG": {
                "total": len(longs),
                "wins": len(long_wins),
                "win_rate_pct": round(len(long_wins) / len(longs) * 100, 1) if longs else 0,
                "net_pnl_usd": round(sum(s.net_pnl_usd for s in longs if s.net_pnl_usd), 2),
            },
            "SHORT": {
                "total": len(shorts),
                "wins": len(short_wins),
                "win_rate_pct": round(len(short_wins) / len(shorts) * 100, 1) if shorts else 0,
                "net_pnl_usd": round(sum(s.net_pnl_usd for s in shorts if s.net_pnl_usd), 2),
            },
        },
    }



@router.get("/analytics/week/{week}")
def week_analytics(week: str, session: Session = Depends(get_session)):
    """
    Full lifecycle intelligence summary for a week.
    This is the primary endpoint for Saturday ChatGPT analysis.

    Returns continuation state distribution, participation behavior,
    survivability scores, false recovery frequency, half-life distribution,
    decay rate patterns, and W19 doctrine confirmation check.
    """
    result = compute_week_analytics(week=week, session=session)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{signal_id}/summary")
def signal_summary(signal_id: int, session: Session = Depends(get_session)):
    """
    Lifecycle intelligence summary for a single signal.
    Includes: state path, half-life, decay rate, survivability score.
    """
    result = compute_lifecycle_summary(signal_id=signal_id, session=session)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/")
def list_signals(
    week: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = None,
    session: Session = Depends(get_session),
):
    query = select(Signal)
    if week:
        query = query.where(Signal.week == week)
    if direction:
        query = query.where(Signal.direction == direction)
    if result:
        query = query.where(Signal.result == result)
    return session.exec(query.order_by(Signal.signal_ts.desc())).all()


@router.get("/{signal_id}")
def get_signal(signal_id: int, session: Session = Depends(get_session)):
    signal = session.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    events = session.exec(
        select(LifecycleEvent)
        .where(LifecycleEvent.signal_id == signal_id)
        .order_by(LifecycleEvent.event_ts)
    ).all()
    return {"signal": signal, "lifecycle": events}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _oi_to_participation(oi_state: Optional[str]) -> str:
    return {"expanding": "persistent", "neutral": "absent", "contracting": "exhausted"}.get(
        oi_state or "", "absent"
    )
