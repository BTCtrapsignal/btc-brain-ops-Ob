"""
database/models.py

Tables:
  Signal       — one row per signal + universal schema + quality metadata
  LifecycleEvent — append-only state log
  EventLog     — structured audit log (signal creation, rejection, API events)
  WeeklyExport — cached weekly exports
"""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Signal(SQLModel, table=True):
    """
    Universal signal record.
    Core fields + quality metadata + PnL + continuation intelligence.
    All quality metadata fields are optional — populated when available.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Identity ─────────────────────────────────────────────
    week: str
    direction: str                      # "LONG" | "SHORT"
    entry_price: float
    tp_price: float
    sl_price: float
    session: str
    signal_ts: datetime
    source: Optional[str] = None        # "SMC" | "AUTO" | "opstest"

    # ── Market context ────────────────────────────────────────
    regime: Optional[str] = None        # "TREND" | "RANGE" | "HIGH_VOL" | "LOW_VOL"
    oi_state: Optional[str] = None      # "expanding" | "neutral" | "contracting"
    rsi_at_entry: Optional[float] = None
    atr_at_entry: Optional[float] = None
    trend_4h: Optional[str] = None      # "BULLISH" | "BEARISH" | "NEUTRAL"

    # ── Universal Signal Schema — quality metadata ────────────
    # These fields enable Reflex Engine read-only analysis.
    # All optional — populated by production bot when available.
    setup_type: Optional[str] = None        # "smc_sweep" | "auto_pro" | "manual"
    market_regime: Optional[str] = None     # same as regime, explicit schema field
    liquidity_risk: Optional[str] = None    # "low" | "medium" | "high"
    confidence_reason: Optional[str] = None # e.g. "oi_expanding+trend_aligned"
    breakout_quality: Optional[str] = None  # "clean" | "marginal" | "weak"
    volatility_state: Optional[str] = None  # "expanding" | "compressed" | "normal"
    risk_score: Optional[float] = None      # 0-100, higher = riskier

    # ── Outcome ───────────────────────────────────────────────
    result: Optional[str] = None        # "WIN" | "LOSS" | "OPEN"
    exit_price: Optional[float] = None
    exit_ts: Optional[datetime] = None

    # ── PnL ──────────────────────────────────────────────────
    position_size_usd: Optional[float] = None
    capital_pct: Optional[float] = None
    fee_usd: Optional[float] = None
    gross_pnl_usd: Optional[float] = None
    net_pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    net_pnl_pct: Optional[float] = None
    rr_achieved: Optional[float] = None

    # ── Continuation intelligence ─────────────────────────────
    final_continuation_state: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


class LifecycleEvent(SQLModel, table=True):
    """Append-only lifecycle state transition log."""
    id: Optional[int] = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="signal.id")
    event_ts: datetime = Field(default_factory=datetime.utcnow)

    continuation_state: str
    participation_quality: Optional[str] = None
    volatility_event: Optional[str] = None
    oi_expanding: Optional[bool] = None
    volume_persisting: Optional[bool] = None
    follow_through: Optional[bool] = None
    note: Optional[str] = None


class EventLog(SQLModel, table=True):
    """
    Structured audit log for all system events.

    event_type categories:
      SIGNAL_CREATED    — new signal ingested
      SIGNAL_REJECTED   — signal blocked by filter (with reason)
      LIFECYCLE_UPDATE  — continuation state transition
      TRADE_CLOSED      — WIN/LOSS recorded
      OPS_CONNECTED     — btc-brain-ops health check passed
      OPS_DISCONNECTED  — btc-brain-ops unreachable
      API_ERROR         — upstream API failure

    Reflex Engine reads this log for adaptive analysis.
    No writes from Reflex — read only.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    event_ts: datetime = Field(default_factory=datetime.utcnow)

    event_type: str                         # see categories above
    source: Optional[str] = None            # "SMC" | "AUTO" | "OPS" | "LIFECYCLE"
    signal_id: Optional[int] = None         # FK to Signal if applicable
    week: Optional[str] = None

    # Event payload — flexible text fields
    direction: Optional[str] = None
    result: Optional[str] = None
    state_from: Optional[str] = None        # for LIFECYCLE_UPDATE
    state_to: Optional[str] = None
    reason: Optional[str] = None            # rejection reason, error message
    event_metadata: Optional[str] = None    # JSON string for extra data


class WeeklyExport(SQLModel, table=True):
    """Cached weekly exports. One row per week."""
    id: Optional[int] = Field(default=None, primary_key=True)
    week: str = Field(unique=True)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    markdown_content: str
    obsidian_content: Optional[str] = None
