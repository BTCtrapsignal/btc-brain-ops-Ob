"""
app/main.py — btc-brain-ops v1.1.0

Phase 1: observe signals, track lifecycle, record PnL, export weekly intelligence.
Phase 1.1: universal signal schema, structured event logging, Reflex read-only API.

ARCHITECTURE:
  btc-signal-alert-system → ingest → btc-brain-ops → lifecycle → weekly export
  Reflex Engine           → READ ONLY via /reflex/* endpoints
  Reflex CANNOT write, modify, or execute anything.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.engine import create_db_and_tables
from app.api.signals import router as signals_router
from app.api.weekly import router as weekly_router
from app.api.events import router as events_router
from app.api.reflex import router as reflex_router
from app.api.monitor import router as monitor_router, VERSION
from app.api.calibration import router as calibration_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(
    title="btc-brain-ops",
    description=(
        "Parallel survivability intelligence system for BTC-Brain. "
        "Universal signal schema. Structured event logging. "
        "Read-only Reflex Engine interface. Monitor-compatible observability endpoints."
    ),
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(signals_router)
app.include_router(weekly_router)
app.include_router(events_router)
app.include_router(reflex_router)
app.include_router(monitor_router)
app.include_router(calibration_router)


@app.get("/", tags=["health"])
def root():
    return {
        "system": "btc-brain-ops",
        "version": "1.1.0",
        "phase": "1.1",
        "mode": "OBSERVE ONLY — no signal blocking",
        "doctrine": [
            "Structure gives directional permission.",
            "Participation gives continuation permission.",
            "Persistence gives survivability proof.",
        ],
        "endpoints": {
            "signals":    "/signals",
            "weekly":     "/weekly",
            "events":     "/events",
            "calibration": "/calibration",
            "reflex":     "/reflex  ← READ ONLY",
            "docs":       "/docs",
        },
        "architecture": {
            "main_system":    "deterministic, fast, reliable",
            "reflex_engine":  "adaptive, analytical, external only",
            "reflex_access":  "read-only via /reflex/*",
        },
    }


# /health and /monitor/status are registered by monitor_router
