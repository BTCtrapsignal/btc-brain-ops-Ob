"""
api/monitor.py

READ-ONLY observability endpoints for btc-system-monitor.

/health         — lightweight liveness check (fast, always responds)
/monitor/status — richer runtime state for monitor system

ARCHITECTURE RULES:
  - Monitor reads only. No writes, no execution influence.
  - If these endpoints fail, core system continues unaffected.
  - No secrets, no API keys, no execution controls exposed.
  - Fail gracefully: every field has a safe fallback value.
"""

import os
import time
import psutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func

from app.database import Signal, LifecycleEvent, EventLog, get_session
from app.database.engine import engine

router = APIRouter(tags=["monitor"])

# Track startup time for uptime calculation
_START_TS = time.time()
VERSION   = "1.2.0"


# ─────────────────────────────────────────────────────────────
# /health  — lightweight liveness (monitor polls this every ~30s)
# ─────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    """
    Lightweight liveness check.
    Always responds quickly. No DB query.
    Returns uptime and DB reachability.
    """
    uptime_sec  = int(time.time() - _START_TS)
    uptime_human = _fmt_uptime(uptime_sec)
    db_ok        = _check_db()

    return {
        "status":        "ok",
        "version":       VERSION,
        "uptime_human":  uptime_human,
        "uptime_seconds": uptime_sec,
        "db": {
            "status": "connected" if db_ok else "error",
        },
    }


# ─────────────────────────────────────────────────────────────
# /monitor/status  — richer observability for monitor system
# ─────────────────────────────────────────────────────────────

@router.get("/monitor/status")
def monitor_status(session: Session = Depends(get_session)):
    """
    Richer runtime state snapshot for btc-system-monitor.
    READ ONLY — monitor cannot write via this endpoint.
    All fields fail gracefully with safe defaults.
    """
    uptime_sec   = int(time.time() - _START_TS)
    uptime_human = _fmt_uptime(uptime_sec)
    db_ok        = _check_db()
    resources    = _get_resources()

    # Layer B stats (btc-brain-ops own data)
    layer_b = _get_layer_b_stats(session)

    # Layer A stats (derived from signal ingestion records)
    layer_a = _get_layer_a_stats(session)

    # Overall health
    overall = _compute_overall(db_ok, layer_b)

    return {
        "overall":   overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),

        "layer_a": layer_a,

        "layer_b": {
            "status":          "healthy" if db_ok else "degraded",
            "version":         VERSION,
            "uptime_human":    uptime_human,
            "uptime_seconds":  uptime_sec,
            "total_signals_stored": layer_b["total_signals"],
            "total_lifecycle_events": layer_b["total_lifecycle"],
            "total_event_logs": layer_b["total_events"],
            "open_signals":    layer_b["open_signals"],
        },

        "layer_c": {
            "status":  "observer_mode",
            "note":    "Reflex reads via /reflex/* — no writes",
            "version": "reflex-observer-1.0",
        },

        "db": {
            "status": "connected" if db_ok else "error",
            "path":   os.environ.get("DB_PATH", "btc_brain_ops.db"),
        },

        "resources": resources,
    }


# ─────────────────────────────────────────────────────────────
# Internal helpers — all fail gracefully
# ─────────────────────────────────────────────────────────────

def _check_db() -> bool:
    """Quick DB connectivity check. Returns False on any error."""
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False


def _get_layer_b_stats(session: Session) -> dict:
    """Aggregate counts from OPS database."""
    try:
        total_signals   = session.exec(select(func.count(Signal.id))).one()
        open_signals    = session.exec(
            select(func.count(Signal.id)).where(Signal.result == "OPEN")
        ).one()
        total_lifecycle = session.exec(select(func.count(LifecycleEvent.id))).one()
        total_events    = session.exec(select(func.count(EventLog.id))).one()
        return {
            "total_signals":   total_signals   or 0,
            "open_signals":    open_signals    or 0,
            "total_lifecycle": total_lifecycle or 0,
            "total_events":    total_events    or 0,
        }
    except Exception:
        return {
            "total_signals": 0, "open_signals": 0,
            "total_lifecycle": 0, "total_events": 0,
        }


def _get_layer_a_stats(session: Session) -> dict:
    """
    Layer A (btc-signal-alert-system) stats derived from OPS ingestion records.
    btc-brain-ops observes Layer A signals — these counts reflect what OPS received.
    """
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_ago    = datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc)

        # Signals ingested today
        signals_today = session.exec(
            select(func.count(Signal.id))
            .where(Signal.signal_ts >= today_start)
        ).one() or 0

        # Signals in last hour
        signals_last_hour = session.exec(
            select(func.count(Signal.id))
            .where(Signal.signal_ts >= hour_ago)
        ).one() or 0

        # Open trades
        open_trades = session.exec(
            select(func.count(Signal.id)).where(Signal.result == "OPEN")
        ).one() or 0

        # Last signal timestamp
        last_signal = session.exec(
            select(Signal.signal_ts).order_by(Signal.signal_ts.desc())
        ).first()
        last_signal_ts = last_signal.isoformat() if last_signal else None

        # Determine Layer A status
        if signals_last_hour > 0:
            status = "active"
        elif signals_today > 0:
            status = "signals_today_no_recent"
        else:
            status = "waiting_for_first_signal"

        return {
            "status":           status,
            "signals_today":    signals_today,
            "signals_last_hour": signals_last_hour,
            "open_trades_in_db": open_trades,
            "last_signal_ts":   last_signal_ts,
        }
    except Exception:
        return {
            "status":            "unknown",
            "signals_today":     0,
            "signals_last_hour": 0,
            "open_trades_in_db": 0,
            "last_signal_ts":    None,
        }


def _get_resources() -> dict:
    """System resource snapshot. Fails gracefully if psutil unavailable."""
    try:
        cpu  = psutil.cpu_percent(interval=0.1)
        ram  = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return {
            "cpu":  round(cpu,  1),
            "ram":  round(ram,  1),
            "disk": round(disk, 1),
        }
    except Exception:
        return {"cpu": None, "ram": None, "disk": None}


def _compute_overall(db_ok: bool, layer_b: dict) -> str:
    """Derive overall health string."""
    if not db_ok:
        return "degraded"
    if layer_b["open_signals"] > 10:
        return "busy"
    return "healthy"


def _fmt_uptime(seconds: int) -> str:
    """Format uptime as human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m"
