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



class MissedOpportunity(SQLModel, table=True):
    """
    Records setups that were evaluated but blocked by filters.

    Purpose: measure filter suppression cost — understand whether
    current filters are protecting alpha OR suppressing profitable setups.

    This is NOT a trade record. It is an observation of what the market
    did AFTER a setup was rejected, compared to what would have happened.

    Doctrine: DATA → EVIDENCE → CALIBRATION (not frustration → assumption)
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Identity ─────────────────────────────────────────────
    week: str
    session: str
    observed_ts: datetime = Field(default_factory=datetime.utcnow)
    direction: str                          # "LONG" | "SHORT" — direction of rejected setup

    # ── Market context at rejection time ─────────────────────
    price_at_rejection: float
    regime: Optional[str] = None
    oi_state: Optional[str] = None
    oi_value: Optional[float] = None        # actual OI % at time (for threshold analysis)
    atr_value: Optional[float] = None       # actual ATR at time (for threshold analysis)
    rsi_value: Optional[float] = None
    trend_4h: Optional[str] = None
    setup_type: Optional[str] = None        # "smc_sweep" | "auto_pro" | "compression_breakout"

    # ── Rejection reasons (which filters blocked this) ────────
    # Comma-separated list of blocking filter names
    blocked_by: Optional[str] = None        # e.g. "oi_expansion,atr_threshold"
    rejection_reason: Optional[str] = None  # human-readable reason
    near_valid: Optional[bool] = None       # True if just barely below threshold

    # ── Threshold proximity (how close was it?) ───────────────
    # Positive = was X% below threshold (missed by X%)
    oi_gap_pct: Optional[float] = None      # how far OI was from min threshold
    atr_gap_pct: Optional[float] = None     # how far ATR was from min threshold
    rsi_gap: Optional[float] = None         # how far RSI was from threshold

    # ── Post-rejection outcome (filled later by operator/bot) ─
    price_30m_later: Optional[float] = None
    price_2h_later: Optional[float] = None
    price_4h_later: Optional[float] = None
    move_30m_pct: Optional[float] = None    # % move 30min after rejection
    move_2h_pct: Optional[float] = None     # % move 2h after rejection
    move_4h_pct: Optional[float] = None     # % move 4h after rejection

    # ── Outcome assessment ────────────────────────────────────
    # Filled after post-rejection outcome is known
    would_have_worked: Optional[bool] = None    # did price move in rejected direction?
    move_direction_correct: Optional[bool] = None  # was direction correct at 2h?
    suppression_justified: Optional[bool] = None   # did rejection prevent a loss?

    # tp_equivalent and sl_equivalent for would-have-been trade
    tp_equivalent: Optional[float] = None
    sl_equivalent: Optional[float] = None
    tp_hit: Optional[bool] = None           # would TP have been hit?
    sl_hit: Optional[bool] = None           # would SL have been hit?

    # ── Intelligence classification ───────────────────────────
    # Filled during weekly review
    suppression_type: Optional[str] = None
    # "protected_loss"      — rejection prevented a losing trade (filter worked)
    # "suppressed_win"      — rejection blocked a winning trade (filter cost)
    # "neutral"             — rejection didn't matter (price went sideways)
    # "unknown"             — insufficient data to classify

    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class WeeklyExport(SQLModel, table=True):
    """Cached weekly exports. One row per week, with reliability metadata."""
    id: Optional[int] = Field(default=None, primary_key=True)
    week: str = Field(unique=True)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    markdown_content: str
    obsidian_content: Optional[str] = None
    # W22: Reliability metadata — lets ChatGPT validate report completeness
    signal_count_at_generation: Optional[int] = None
    missed_count_at_generation:  Optional[int] = None
    event_count_at_generation:   Optional[int] = None
    is_complete: Optional[bool]  = None
