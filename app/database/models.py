"""
database/models.py

Core SQLModel table definitions for btc-brain-ops.

Tables:
  Signal         — one row per signal, includes PnL tracking
  LifecycleEvent — append-only state transition log
  WeeklyExport   — cached weekly markdown + obsidian exports
"""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Signal(SQLModel, table=True):
    """
    One row per signal ingested from the production bot.
    Core fields filled at ingestion; outcome + PnL filled at close.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # --- Identity ---
    week: str                           # "W20"
    direction: str                      # "LONG" | "SHORT"
    entry_price: float
    tp_price: float
    sl_price: float
    session: str                        # "Asia" | "London" | "NY" | "Late"
    signal_ts: datetime

    # --- Market context at signal time ---
    regime: Optional[str] = None        # "TREND" | "RANGE" | "HIGH_VOL" | "LOW_VOL"
    oi_state: Optional[str] = None      # "expanding" | "neutral" | "contracting"
    rsi_at_entry: Optional[float] = None
    atr_at_entry: Optional[float] = None
    trend_4h: Optional[str] = None      # "BULLISH" | "BEARISH" | "NEUTRAL"

    # --- Outcome ---
    result: Optional[str] = None        # "WIN" | "LOSS" | "OPEN"
    exit_price: Optional[float] = None
    exit_ts: Optional[datetime] = None

    # --- PnL tracking ---
    # Fill these when closing a trade for real performance measurement
    position_size_usd: Optional[float] = None   # USD value of position
    capital_pct: Optional[float] = None         # % of total capital used (e.g. 1.0 = 1%)
    fee_usd: Optional[float] = None             # total fee paid (entry + exit)
    gross_pnl_usd: Optional[float] = None       # PnL before fee
    net_pnl_usd: Optional[float] = None         # PnL after fee  ← the real number
    pnl_pct: Optional[float] = None             # % return on position (gross)
    net_pnl_pct: Optional[float] = None         # % return on position (net, after fee)
    rr_achieved: Optional[float] = None         # actual R:R achieved (e.g. 0.8, 1.0, 2.0)

    # --- Continuation intelligence ---
    final_continuation_state: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


class LifecycleEvent(SQLModel, table=True):
    """
    Append-only event log — never update or delete, only append.
    Every state transition or observation produces one row.
    """
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


class WeeklyExport(SQLModel, table=True):
    """Cached weekly exports. One row per week, overwritten on regenerate."""
    id: Optional[int] = Field(default=None, primary_key=True)
    week: str = Field(unique=True)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    markdown_content: str
    obsidian_content: Optional[str] = None
