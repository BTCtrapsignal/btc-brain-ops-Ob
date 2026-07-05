"""
database/models.py

Tables:
  Signal             — one row per signal + universal schema + quality metadata
  LifecycleEvent     — append-only state log
  EventLog           — structured audit log (signal creation, rejection, API events)
  WeeklyExport       — cached weekly exports
  MissedOpportunity  — filter suppression cost tracking

REQ-W27-002 additions:
  EngineeringObservation — EO register (Brain Ops as authoritative EO store)
  EngineeringReview      — ER register (Brain Ops as authoritative ER store)
  EngineeringEvidence    — evidence increment log per EO/ER
  WeeklyExport extended  — 11 new optional columns for engineering package
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
    setup_type: Optional[str] = None
    market_regime: Optional[str] = None
    liquidity_risk: Optional[str] = None
    confidence_reason: Optional[str] = None
    breakout_quality: Optional[str] = None
    volatility_state: Optional[str] = None
    risk_score: Optional[float] = None

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

    event_type: str
    source: Optional[str] = None
    signal_id: Optional[int] = None
    week: Optional[str] = None

    direction: Optional[str] = None
    result: Optional[str] = None
    state_from: Optional[str] = None
    state_to: Optional[str] = None
    reason: Optional[str] = None
    event_metadata: Optional[str] = None


class MissedOpportunity(SQLModel, table=True):
    """
    Records setups that were evaluated but blocked by filters.
    Measures filter suppression cost against post-rejection outcomes.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    week: str
    session: str
    observed_ts: datetime = Field(default_factory=datetime.utcnow)
    direction: str

    price_at_rejection: float
    regime: Optional[str] = None
    oi_state: Optional[str] = None
    oi_value: Optional[float] = None
    atr_value: Optional[float] = None
    rsi_value: Optional[float] = None
    trend_4h: Optional[str] = None
    setup_type: Optional[str] = None

    blocked_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    near_valid: Optional[bool] = None

    oi_gap_pct: Optional[float] = None
    atr_gap_pct: Optional[float] = None
    rsi_gap: Optional[float] = None

    price_30m_later: Optional[float] = None
    price_2h_later: Optional[float] = None
    price_4h_later: Optional[float] = None
    move_30m_pct: Optional[float] = None
    move_2h_pct: Optional[float] = None
    move_4h_pct: Optional[float] = None

    would_have_worked: Optional[bool] = None
    move_direction_correct: Optional[bool] = None
    suppression_justified: Optional[bool] = None

    tp_equivalent: Optional[float] = None
    sl_equivalent: Optional[float] = None
    tp_hit: Optional[bool] = None
    sl_hit: Optional[bool] = None

    suppression_type: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WeeklyExport(SQLModel, table=True):
    """
    Cached weekly exports. One row per week, with reliability metadata.

    REQ-W27-002: Extended with Engineering Package fields.
    New fields are all Optional — existing rows remain valid with NULL values.
    Requires additive ALTER TABLE migration before first deployment with these fields.
    See: migrations/add_engineering_package_columns.sql
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    week: str = Field(unique=True)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    markdown_content: str
    obsidian_content: Optional[str] = None

    # W22: Reliability metadata
    signal_count_at_generation: Optional[int] = None
    missed_count_at_generation:  Optional[int] = None
    event_count_at_generation:   Optional[int] = None
    is_complete: Optional[bool]  = None

    # REQ-W27-002: Engineering Package content fields
    engineering_index_content:    Optional[str] = None
    timeline_content:             Optional[str] = None
    event_bundle_json:            Optional[str] = None
    runtime_stats_json:           Optional[str] = None
    eo_register_content:          Optional[str] = None
    er_register_content:          Optional[str] = None
    engineering_summary_content:  Optional[str] = None

    # REQ-W27-002: Engineering Package versioning (Decision B/C)
    package_version:    Optional[str] = None   # e.g. "W27.0"
    schema_version:     Optional[str] = None   # e.g. "engineering-export-v1"
    generator_version:  Optional[str] = None   # Brain Ops version at generation time
    compatible_runtime: Optional[str] = None   # e.g. "Signal Bot v7.9+"


# ─────────────────────────────────────────────────────────────
# REQ-W27-002: Engineering Database Tables
# Brain Ops is the authoritative store for EO, ER, and Evidence.
# These tables are created automatically by create_all() on startup.
# No manual migration required for new tables.
# ─────────────────────────────────────────────────────────────

class EngineeringObservation(SQLModel, table=True):
    """
    Engineering Observation (EO) register.

    Records verified runtime observations that do not yet have sufficient
    evidence to open an Engineering Review. Acts as a holding area before
    evidence threshold is reached.

    Lifecycle: open → monitoring → closed / archived
    Confidence: Level A (multiple independent) / B (single production) / C (hypothesis)
    Exit criteria: A=expected behaviour confirmed / B=ER warranted / C=coverage increment / D=expired
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Identity ─────────────────────────────────────────────
    eo_id: str = Field(unique=True)             # e.g. "EO-W27-001" — immutable per Art.6
    title: str
    layer: Optional[str] = None                 # "A" | "B" | "C" | "D" | "Cross"
    sprint_opened: Optional[str] = None         # e.g. "W27"

    # ── Lifecycle ─────────────────────────────────────────────
    status: str = Field(default="open")
    # "open" | "monitoring" | "closed" | "archived"

    # ── Evidence ─────────────────────────────────────────────
    evidence_count: int = Field(default=0)
    confidence: Optional[str] = None            # "A" | "B" | "C"

    # ── Links ─────────────────────────────────────────────────
    linked_er_id: Optional[str] = None          # eo_id of related ER if promoted
    linked_signal_id: Optional[int] = None      # FK to Signal if observation is signal-specific

    # ── Engineering content ────────────────────────────────────
    hypothesis: Optional[str] = None            # single declarative statement
    trigger_condition: Optional[str] = None     # what increments evidence
    exit_criteria: Optional[str] = None         # which exit condition applies (A/B/C/D)

    # ── Monitoring window ──────────────────────────────────────
    monitoring_window_opened: Optional[datetime] = None
    monitoring_window_expires: Optional[datetime] = None   # 8-week expiry for exit D

    # ── Notes ─────────────────────────────────────────────────
    notes: Optional[str] = None

    # ── Audit ─────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EngineeringReview(SQLModel, table=True):
    """
    Engineering Review (ER) register.

    Records formal engineering reviews with immutable IDs, confidence levels,
    evidence counts, and full lifecycle tracking per the Engineering Constitution.

    Article 6: Review IDs are immutable.
    Article 15: Engineering Reviews are indexed by immutable Review IDs.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Identity ─────────────────────────────────────────────
    er_id: str = Field(unique=True)             # e.g. "ER-W26-015" — immutable per Art.6
    title: str
    review_type: Optional[str] = None
    # "Runtime Evidence" | "Implementation" | "Governance" | "Architecture"
    # "Operational" | "Retrospective"

    # ── Layer ─────────────────────────────────────────────────
    layer: Optional[str] = None                 # "A" | "B" | "C" | "Cross-System"
    cross_system: Optional[bool] = None
    layer_primary: Optional[str] = None
    layer_secondary: Optional[str] = None

    # ── Sprint ─────────────────────────────────────────────────
    sprint_opened: Optional[str] = None
    sprint_closed: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────
    status: str = Field(default="draft")
    # "draft" | "evidence_collecting" | "candidate" | "promoted"
    # "closed" | "deferred" | "archived"

    # ── Confidence and evidence ────────────────────────────────
    confidence: Optional[str] = None            # "A" | "B" | "C"
    evidence_count: int = Field(default=0)
    negative_evidence_count: int = Field(default=0)
    evidence_threshold: Optional[str] = None    # declared sufficient count/condition

    # ── Engineering content ────────────────────────────────────
    hypothesis: Optional[str] = None            # single declarative claim
    verified_finding: Optional[str] = None      # confirmed finding after review
    last_observation: Optional[datetime] = None

    # ── Deployment context ─────────────────────────────────────
    owner: Optional[str] = None                 # "Engineering Review (ChatGPT)"
    deployment_id: Optional[str] = None

    # ── Links ─────────────────────────────────────────────────
    linked_req_id: Optional[str] = None         # e.g. "REQ-W27-001"
    linked_eo_id: Optional[str] = None          # EO that was promoted to this ER

    # ── Promotion and retirement ───────────────────────────────
    promotion_eligibility: Optional[bool] = None
    retirement_reason: Optional[str] = None     # Art.14: traceability preserved

    # ── Audit ─────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EngineeringEvidence(SQLModel, table=True):
    """
    Evidence increment log per Engineering Review or Engineering Observation.

    Records one row per independent evidence observation. Enables Article 12
    continuous coverage measurement and prevents evidence count inflation
    (independent window vs repeated same-cycle observation).

    Article 5:  Negative evidence equally valid as positive evidence.
    Article 12: Runtime Coverage measured continuously throughout a sprint.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Links ─────────────────────────────────────────────────
    er_id: Optional[str] = None                 # string reference to EngineeringReview.er_id
    eo_id: Optional[str] = None                 # string reference to EngineeringObservation.eo_id

    # ── Evidence classification ────────────────────────────────
    evidence_type: Optional[str] = None
    # "positive" | "negative" | "baseline" | "transition"

    # ── Source ────────────────────────────────────────────────
    source_system: Optional[str] = None         # "A" | "B" | "C" | "Cross"
    deployment_id: Optional[str] = None
    observation_window: Optional[str] = None    # sprint or date range
    runtime_log_ref: Optional[str] = None       # specific log line or event reference

    # ── Description ───────────────────────────────────────────
    description: Optional[str] = None

    # ── Audit ─────────────────────────────────────────────────
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
