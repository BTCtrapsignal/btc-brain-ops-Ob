"""
app/main.py — btc-brain-ops
Phase 1: observe signals, track lifecycle, record PnL, export weekly intelligence.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.engine import create_db_and_tables
from app.api.signals import router as signals_router
from app.api.weekly import router as weekly_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(
    title="btc-brain-ops",
    description="Parallel survivability intelligence system for BTC-Brain.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(signals_router)
app.include_router(weekly_router)


@app.get("/", tags=["health"])
def root():
    return {
        "system": "btc-brain-ops",
        "version": "1.0.0",
        "phase": 1,
        "mode": "OBSERVE ONLY — no signal blocking",
        "doctrine": [
            "Structure gives directional permission.",
            "Participation gives continuation permission.",
            "Persistence gives survivability proof.",
        ],
        "key_endpoints": {
            "ingest_signal":   "POST /signals/ingest",
            "close_signal":    "POST /signals/{id}/close",
            "auto_lifecycle":  "POST /signals/{id}/auto-lifecycle",
            "week_pnl_stats":  "GET  /signals/stats/week/{week}",
            "generate_export": "POST /weekly/{week}/generate",
            "get_export":      "GET  /weekly/{week}",
            "docs":            "/docs",
        },
    }


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
