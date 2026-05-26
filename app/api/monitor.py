"""
api/monitor.py — btc-brain-ops System Monitor

Infers health of ALL 3 services from Brain Ops database activity.
No need to ping Layer A or C directly — their footprints are in the DB.

Endpoints:
  GET /monitor/status   — full system health: Layer A activity + Layer B self + Layer C reads
  GET /monitor/layer-a  — Layer A (Signal Bot) activity detail
  GET /monitor/layer-c  — Layer C (Reflex Engine) read activity

Logic:
  Layer A health  → last signal ingested (signal_ts) + last SIGNAL_CREATED event
  Layer B health  → self (this service) uptime + DB connectivity
  Layer C health  → last /reflex/* endpoint call via EventLog (if logged)
                    or inferred from last LifecycleEvent created by reflex reads
"""

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func

from app.database import Signal, LifecycleEvent, EventLog, get_session

router = APIRouter(prefix="/monitor", tags=["monitor"])

# ── Thresholds ──────────────────────────────────────────────────────────────
# ปรับตามจริง — ถ้า Layer A ส่ง signal มาช่วงตลาดเปิดปกติ
LAYER_A_WARN_MINUTES  = 60    # เกิน 60 นาทีไม่มี signal → warn
LAYER_A_DEAD_HOURS    = 6     # เกิน 6 ชั่วโมง → likely down
LAYER_C_WARN_MINUTES  = 120   # Reflex อ่านข้อมูลทุก cycle (~15-30 นาที)
# ────────────────────────────────────────────────────────────────────────────

_start_time = datetime.utcnow()


@router.get("/status")
def monitor_status(session: Session = Depends(get_session)):
    """
    Full system health snapshot.
    Dashboard calls this single endpoint to show all 3 layers.
    """
    now = datetime.utcnow()

    # ── Layer B: self ─────────────────────────────────────────────────────
    uptime_seconds = (now - _start_time).total_seconds()
    total_signals  = session.exec(select(func.count(Signal.id))).one()
    total_events   = session.exec(select(func.count(EventLog.id))).one()

    layer_b = {
        "status": "online",
        "uptime_seconds": int(uptime_seconds),
        "uptime_human": _fmt_duration(uptime_seconds),
        "db_connected": True,
        "total_signals_stored": total_signals,
        "total_events_logged": total_events,
        "checked_at": now.isoformat(),
    }

    # ── Layer A: inferred from last signal ingested ───────────────────────
    last_signal = session.exec(
        select(Signal).order_by(Signal.signal_ts.desc())
    ).first()

    last_signal_event = session.exec(
        select(EventLog)
        .where(EventLog.event_type == "SIGNAL_CREATED")
        .order_by(EventLog.event_ts.desc())
    ).first()

    open_trades = session.exec(
        select(func.count(Signal.id)).where(Signal.result == "OPEN")
    ).one()

    if last_signal:
        a_age_minutes = (now - last_signal.signal_ts).total_seconds() / 60
        if a_age_minutes < LAYER_A_WARN_MINUTES:
            a_status = "active"
        elif a_age_minutes < LAYER_A_DEAD_HOURS * 60:
            a_status = "idle"   # อาจเป็นช่วง low activity ปกติ
        else:
            a_status = "likely_down"
        a_last_seen = last_signal.signal_ts.isoformat()
        a_age_human = _fmt_duration(a_age_minutes * 60)
    else:
        a_status    = "no_data"
        a_last_seen = None
        a_age_human = None
        a_age_minutes = None

    layer_a = {
        "status": a_status,
        "inference": "derived from last signal ingested into Brain Ops",
        "last_signal_ts": a_last_seen,
        "last_signal_age": a_age_human,
        "last_signal_source": last_signal.source if last_signal else None,
        "open_trades_in_db": open_trades,
        "last_signal_created_event": last_signal_event.event_ts.isoformat() if last_signal_event else None,
        "private_address": "btc-alert-bot.railway.internal",
        "public_url": None,
    }

    # ── Layer C: inferred from reflex-tagged events or lifecycle reads ────
    # Reflex Engine reads /reflex/* endpoints — ถ้า Brain Ops log reflex activity
    # ใช้ EventLog source field ถ้า Signal Bot ส่ง source="reflex"
    # หรือดูจาก LifecycleEvent timestamps ที่ Reflex อาจ trigger ผ่าน
    last_reflex_event = session.exec(
        select(EventLog)
        .where(EventLog.source == "reflex")
        .order_by(EventLog.event_ts.desc())
    ).first()

    # Fallback: ดูจาก lifecycle events ที่เกิดขึ้นล่าสุด (Reflex อาจ trigger)
    last_lifecycle = session.exec(
        select(LifecycleEvent).order_by(LifecycleEvent.event_ts.desc())
    ).first()

    if last_reflex_event:
        c_age_minutes = (now - last_reflex_event.event_ts).total_seconds() / 60
        c_status = "active" if c_age_minutes < LAYER_C_WARN_MINUTES else "idle"
        c_last_seen = last_reflex_event.event_ts.isoformat()
        c_note = "confirmed via reflex-tagged EventLog"
    else:
        # ยังไม่มี reflex event — แสดงสถานะ "unconfirmed"
        c_status    = "unconfirmed"
        c_last_seen = None
        c_note = (
            "ยังไม่มี EventLog ที่ source='reflex' — "
            "Reflex อ่าน /reflex/* endpoints แบบ read-only จึงไม่ทิ้ง event ไว้ใน DB "
            "จนกว่าจะ deploy /brain-state และ Reflex เริ่มส่ง data กลับ"
        )
        c_age_minutes = None

    layer_c = {
        "status": c_status,
        "inference": c_note,
        "last_activity_ts": c_last_seen,
        "last_activity_age": _fmt_duration(c_age_minutes * 60) if c_age_minutes else None,
        "last_lifecycle_event_ts": last_lifecycle.event_ts.isoformat() if last_lifecycle else None,
        "private_address": "btc-reflex-engine.railway.internal",
        "public_url": None,
        "brain_state_endpoint": "MISSING — deploy pending W21",
    }

    # ── Overall system health ─────────────────────────────────────────────
    all_ok = (
        layer_b["status"] == "online"
        and layer_a["status"] in ("active",)
        and layer_c["status"] in ("active", "unconfirmed")
    )
    has_warning = (
        layer_a["status"] in ("idle",)
        or layer_c["status"] == "idle"
    )
    overall = "healthy" if all_ok else ("warning" if has_warning else "degraded")

    return {
        "overall": overall,
        "checked_at": now.isoformat(),
        "layer_a": layer_a,
        "layer_b": layer_b,
        "layer_c": layer_c,
        "notes": [
            "Layer A & C are worker processes with no public URL",
            "Layer A health is inferred from signal ingestion activity",
            "Layer C health is inferred from reflex-tagged events (limited until /brain-state deployed)",
        ],
    }


@router.get("/layer-a")
def layer_a_detail(
    hours: int = 24,
    session: Session = Depends(get_session),
):
    """Layer A activity detail — last N hours of signals and events."""
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)

    recent_signals = session.exec(
        select(Signal)
        .where(Signal.signal_ts >= since)
        .order_by(Signal.signal_ts.desc())
    ).all()

    recent_events = session.exec(
        select(EventLog)
        .where(EventLog.event_type == "SIGNAL_CREATED")
        .where(EventLog.event_ts >= since)
        .order_by(EventLog.event_ts.desc())
    ).all()

    return {
        "layer": "A",
        "service": "btc-signal-alert-system",
        "window_hours": hours,
        "signals_ingested": len(recent_signals),
        "signal_events": len(recent_events),
        "signals": [
            {
                "id": s.id,
                "signal_ts": s.signal_ts.isoformat(),
                "direction": s.direction,
                "source": s.source,
                "result": s.result,
                "week": s.week,
            }
            for s in recent_signals
        ],
    }


@router.get("/layer-c")
def layer_c_detail(session: Session = Depends(get_session)):
    """Layer C (Reflex Engine) inferred activity."""
    now = datetime.utcnow()

    reflex_events = session.exec(
        select(EventLog)
        .where(EventLog.source == "reflex")
        .order_by(EventLog.event_ts.desc())
        .limit(20)
    ).all()

    return {
        "layer": "C",
        "service": "btc-reflex-engine",
        "private_address": "btc-reflex-engine.railway.internal",
        "reflex_events_found": len(reflex_events),
        "note": (
            "Reflex reads /reflex/* endpoints (read-only). "
            "Events only appear here if Reflex writes back via a future /brain-state endpoint."
        ),
        "events": [
            {
                "event_ts": e.event_ts.isoformat(),
                "event_type": e.event_type,
                "source": e.source,
            }
            for e in reflex_events
        ],
        "checked_at": now.isoformat(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    """แปลง seconds เป็น human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    elif seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    else:
        d = int(seconds // 86400)
        h = int((seconds % 86400) // 3600)
        return f"{d}d {h}h"
