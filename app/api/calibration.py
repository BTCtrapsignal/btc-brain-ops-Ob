"""
api/calibration.py

Missed opportunity intelligence and filter calibration endpoints.

PURPOSE:
  Measure filter suppression cost.
  Understand whether current filters protect alpha OR suppress profitable setups.
  DATA → EVIDENCE → CALIBRATION (not frustration → assumption)

DOCTRINE:
  Do NOT loosen filters blindly.
  Do NOT remove participation confirmation.
  Collect evidence FIRST. Calibrate SECOND.

ENDPOINTS:
  POST /calibration/missed           — log a blocked setup
  POST /calibration/missed/{id}/outcome — fill in what happened after
  GET  /calibration/missed/          — list missed opportunities
  GET  /calibration/stats/week/{week} — suppression statistics for a week
  GET  /calibration/filters/analysis  — which filters are blocking most / costing most
"""

import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select, func

from app.database import get_session
from app.database.models import MissedOpportunity
from app.api.events import log_event

router = APIRouter(prefix="/calibration", tags=["calibration"])


# ─────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────

class MissedOpportunityRequest(BaseModel):
    """Log a blocked setup at the time it is rejected."""
    week: str
    session: str
    direction: str
    price_at_rejection: float

    # Market context
    regime: Optional[str] = None
    oi_state: Optional[str] = None
    oi_value: Optional[float] = None
    atr_value: Optional[float] = None
    rsi_value: Optional[float] = None
    trend_4h: Optional[str] = None
    setup_type: Optional[str] = None

    # What blocked it
    blocked_by: Optional[str] = None        # e.g. "oi_expansion,atr_threshold"
    rejection_reason: Optional[str] = None
    near_valid: Optional[bool] = None

    # Threshold proximity
    oi_gap_pct: Optional[float] = None
    atr_gap_pct: Optional[float] = None
    rsi_gap: Optional[float] = None

    # Would-have-been trade levels
    tp_equivalent: Optional[float] = None
    sl_equivalent: Optional[float] = None

    notes: Optional[str] = None


class OutcomeRequest(BaseModel):
    """
    Fill in what actually happened after the setup was rejected.
    Called when price data is available (30m, 2h, 4h after rejection).
    """
    price_30m_later: Optional[float] = None
    price_2h_later: Optional[float] = None
    price_4h_later: Optional[float] = None

    # Manual assessments (operator fills during weekly review)
    would_have_worked: Optional[bool] = None
    move_direction_correct: Optional[bool] = None
    suppression_justified: Optional[bool] = None
    suppression_type: Optional[str] = None  # protected_loss|suppressed_win|neutral|unknown
    tp_hit: Optional[bool] = None
    sl_hit: Optional[bool] = None
    notes: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/missed", status_code=201)
def log_missed_opportunity(
    payload: MissedOpportunityRequest,
    session: Session = Depends(get_session),
):
    """
    Log a setup that was evaluated and rejected by filters.
    Called by the production bot (fire-and-forget) when a setup is blocked.
    """
    mo = MissedOpportunity(**payload.model_dump())
    session.add(mo)
    session.commit()
    session.refresh(mo)

    log_event(
        session,
        event_type="SETUP_REJECTED",
        source=payload.setup_type or "unknown",
        week=payload.week,
        direction=payload.direction,
        reason=payload.blocked_by,
        event_metadata=f"near_valid={payload.near_valid} "
                       f"oi_gap={payload.oi_gap_pct} atr_gap={payload.atr_gap_pct}",
    )

    return {
        "id": mo.id,
        "message": "Missed opportunity logged. Fill outcome via POST /calibration/missed/{id}/outcome",
    }


@router.post("/missed/{mo_id}/outcome")
def fill_outcome(
    mo_id: int,
    payload: OutcomeRequest,
    session: Session = Depends(get_session),
):
    """
    Fill in what the market did after the setup was rejected.
    Computes pct moves automatically from price data.
    """
    mo = session.get(MissedOpportunity, mo_id)
    if not mo:
        raise HTTPException(status_code=404, detail="Missed opportunity not found")

    # Fill price data
    if payload.price_30m_later is not None:
        mo.price_30m_later = payload.price_30m_later
        mo.move_30m_pct = _pct_move(mo.price_at_rejection, payload.price_30m_later, mo.direction)

    if payload.price_2h_later is not None:
        mo.price_2h_later = payload.price_2h_later
        mo.move_2h_pct = _pct_move(mo.price_at_rejection, payload.price_2h_later, mo.direction)

    if payload.price_4h_later is not None:
        mo.price_4h_later = payload.price_4h_later
        mo.move_4h_pct = _pct_move(mo.price_at_rejection, payload.price_4h_later, mo.direction)

    # Fill assessments
    for field in ["would_have_worked", "move_direction_correct",
                  "suppression_justified", "suppression_type",
                  "tp_hit", "sl_hit", "notes"]:
        val = getattr(payload, field)
        if val is not None:
            setattr(mo, field, val)

    # Auto-classify suppression_type if not provided
    if mo.suppression_type is None and mo.move_2h_pct is not None:
        mo.suppression_type = _auto_classify(mo)

    session.add(mo)
    session.commit()

    return {
        "id": mo.id,
        "suppression_type": mo.suppression_type,
        "move_2h_pct": mo.move_2h_pct,
        "would_have_worked": mo.would_have_worked,
    }


@router.get("/missed/")
def list_missed(
    week: Optional[str] = None,
    suppression_type: Optional[str] = None,
    near_valid: Optional[bool] = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    """List missed opportunities with optional filters."""
    query = select(MissedOpportunity).order_by(MissedOpportunity.observed_ts.desc())
    if week:
        query = query.where(MissedOpportunity.week == week)
    if suppression_type:
        query = query.where(MissedOpportunity.suppression_type == suppression_type)
    if near_valid is not None:
        query = query.where(MissedOpportunity.near_valid == near_valid)
    return session.exec(query.limit(limit)).all()


@router.get("/stats/week/{week}")
def week_suppression_stats(week: str, session: Session = Depends(get_session)):
    """
    Suppression cost statistics for a given week.
    Core output for weekly calibration review.
    """
    missed = session.exec(
        select(MissedOpportunity).where(MissedOpportunity.week == week)
    ).all()

    if not missed:
        return {"week": week, "message": "No missed opportunities logged this week."}

    total = len(missed)
    with_outcome = [m for m in missed if m.suppression_type is not None]
    protected_losses = [m for m in with_outcome if m.suppression_type == "protected_loss"]
    suppressed_wins  = [m for m in with_outcome if m.suppression_type == "suppressed_win"]
    neutral          = [m for m in with_outcome if m.suppression_type == "neutral"]
    near_valid_count = sum(1 for m in missed if m.near_valid)

    # Which filters are blocking most
    filter_counts: dict = {}
    for m in missed:
        if m.blocked_by:
            for f in m.blocked_by.split(","):
                f = f.strip()
                if f:
                    filter_counts[f] = filter_counts.get(f, 0) + 1

    # Average move after rejection (directional)
    moves_2h = [m.move_2h_pct for m in missed if m.move_2h_pct is not None]
    avg_move_2h = round(sum(moves_2h) / len(moves_2h), 2) if moves_2h else None

    # Suppression cost ratio
    cost_ratio = None
    if with_outcome:
        cost_ratio = round(len(suppressed_wins) / len(with_outcome) * 100, 1)

    return {
        "week": week,
        "total_missed": total,
        "with_outcome_assessed": len(with_outcome),
        "near_valid_count": near_valid_count,
        "suppression_breakdown": {
            "protected_loss":   len(protected_losses),
            "suppressed_win":   len(suppressed_wins),
            "neutral":          len(neutral),
            "unclassified":     total - len(with_outcome),
        },
        "suppression_cost_pct": cost_ratio,  # % of assessed that were suppressed_wins
        "avg_directional_move_2h": avg_move_2h,
        "most_blocking_filters": dict(
            sorted(filter_counts.items(), key=lambda x: -x[1])
        ),
        "calibration_signal": _calibration_signal(
            len(protected_losses), len(suppressed_wins), len(with_outcome)
        ),
    }


@router.get("/filters/analysis")
def filter_analysis(
    min_weeks: int = 2,
    session: Session = Depends(get_session),
):
    """
    Cross-week filter suppression analysis.
    Which filters are blocking most setups?
    Which have highest suppressed_win rate?
    Requires data from multiple weeks for meaningful signal.

    Use this to identify SPECIFIC filters for threshold recalibration.
    Do NOT use this to remove filters wholesale.
    """
    all_missed = session.exec(select(MissedOpportunity)).all()

    if len(all_missed) < 5:
        return {
            "message": (
                f"Insufficient data. Have {len(all_missed)} records. "
                "Need ≥5 for basic analysis, ≥20 for calibration decisions."
            ),
            "records": len(all_missed),
            "minimum_for_calibration": 20,
        }

    # Per-filter statistics
    filter_stats: dict = {}
    for m in all_missed:
        if not m.blocked_by:
            continue
        for f in m.blocked_by.split(","):
            f = f.strip()
            if not f:
                continue
            if f not in filter_stats:
                filter_stats[f] = {
                    "blocked_count": 0,
                    "suppressed_wins": 0,
                    "protected_losses": 0,
                    "neutral": 0,
                    "unclassified": 0,
                    "near_valid_count": 0,
                    "avg_move_2h": [],
                }
            fs = filter_stats[f]
            fs["blocked_count"] += 1
            if m.near_valid:
                fs["near_valid_count"] += 1
            if m.suppression_type == "suppressed_win":
                fs["suppressed_wins"] += 1
            elif m.suppression_type == "protected_loss":
                fs["protected_losses"] += 1
            elif m.suppression_type == "neutral":
                fs["neutral"] += 1
            else:
                fs["unclassified"] += 1
            if m.move_2h_pct is not None:
                fs["avg_move_2h"].append(m.move_2h_pct)

    # Compute derived stats per filter
    result = {}
    for fname, fs in filter_stats.items():
        assessed = fs["suppressed_wins"] + fs["protected_losses"] + fs["neutral"]
        cost_pct = round(fs["suppressed_wins"] / assessed * 100, 1) if assessed > 0 else None
        avg_2h   = round(sum(fs["avg_move_2h"]) / len(fs["avg_move_2h"]), 2) if fs["avg_move_2h"] else None
        result[fname] = {
            "blocked_count":      fs["blocked_count"],
            "near_valid_count":   fs["near_valid_count"],
            "suppressed_wins":    fs["suppressed_wins"],
            "protected_losses":   fs["protected_losses"],
            "neutral":            fs["neutral"],
            "suppression_cost_pct": cost_pct,
            "avg_directional_move_2h": avg_2h,
            "calibration_verdict": _filter_verdict(
                fs["suppressed_wins"], fs["protected_losses"],
                assessed, avg_2h
            ),
        }

    # Sort by suppression cost
    result = dict(
        sorted(result.items(),
               key=lambda x: x[1]["suppression_cost_pct"] or 0,
               reverse=True)
    )

    return {
        "total_records_analyzed": len(all_missed),
        "filters_analyzed": len(result),
        "calibration_ready": len(all_missed) >= 20,
        "minimum_for_calibration": 20,
        "filter_analysis": result,
        "doctrine": (
            "Calibrate ONLY filters with: suppression_cost_pct > 60% "
            "AND ≥10 assessed records AND avg_move_2h > 1.5%. "
            "Do not remove filters wholesale."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _pct_move(price_at: float, price_after: float, direction: str) -> float:
    """
    Compute directional % move.
    Positive = moved in correct direction, negative = moved against.
    """
    if price_at == 0:
        return 0.0
    raw_pct = (price_after - price_at) / price_at * 100
    if direction.upper() == "SHORT":
        raw_pct = -raw_pct
    return round(raw_pct, 3)


def _auto_classify(mo: MissedOpportunity) -> str:
    """
    Auto-classify suppression_type from available outcome data.
    Conservative: defaults to 'neutral' when uncertain.
    """
    move = mo.move_2h_pct

    if move is None:
        return "unknown"

    # If SL would have been hit: protected_loss
    if mo.sl_hit:
        return "protected_loss"

    # If TP would have been hit: suppressed_win
    if mo.tp_hit:
        return "suppressed_win"

    # From price movement alone (rough classification)
    if move > 1.5:
        return "suppressed_win"
    elif move < -1.0:
        return "protected_loss"
    else:
        return "neutral"


def _calibration_signal(
    protected: int, suppressed: int, total_assessed: int
) -> str:
    """
    High-level calibration signal for weekly review.
    Requires meaningful sample before making any recommendation.
    """
    if total_assessed < 5:
        return f"INSUFFICIENT_DATA — need ≥5 assessed outcomes (have {total_assessed})"

    if total_assessed < 10:
        return "EARLY_DATA — continue collecting before drawing conclusions"

    if suppressed == 0 and protected > 0:
        return "FILTERS_WORKING — all assessed rejections prevented losses"

    cost_pct = suppressed / total_assessed * 100

    if cost_pct > 70:
        return (
            f"REVIEW_WARRANTED — {cost_pct:.0f}% of assessed are suppressed_wins. "
            "Identify specific filters responsible before adjusting."
        )
    elif cost_pct > 40:
        return (
            f"MONITOR_CLOSELY — {cost_pct:.0f}% suppression cost. "
            "Continue collecting data. Review specific filters at W22+."
        )
    else:
        return (
            f"FILTERS_CALIBRATED — {cost_pct:.0f}% suppression cost is within acceptable range. "
            "No calibration needed yet."
        )


def _filter_verdict(
    suppressed: int, protected: int, assessed: int, avg_move: Optional[float]
) -> str:
    """Per-filter calibration verdict."""
    if assessed < 3:
        return "INSUFFICIENT_DATA"

    cost_pct = suppressed / assessed * 100 if assessed > 0 else 0

    if cost_pct > 60 and assessed >= 10 and avg_move and avg_move > 1.5:
        return "CALIBRATE — high suppression cost with strong directional moves"
    elif cost_pct > 40 and assessed >= 5:
        return "MONITOR — moderate suppression cost, collect more data"
    elif cost_pct < 20:
        return "WORKING — low suppression cost, filter is protecting well"
    else:
        return "UNCLEAR — mixed evidence, continue collecting"
