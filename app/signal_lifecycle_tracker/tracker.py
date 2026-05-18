"""
signal_lifecycle_tracker/tracker.py

Analyzes the full lifecycle of a signal from activation to close.

Produces per-signal:
  - continuation_half_life    — how long did quality persist?
  - participation_decay_rate  — how fast did participation deteriorate?
  - survivability_score       — composite 0-100 score from lifecycle evidence
  - state_path                — full continuation state sequence
  - decay_point               — where quality first dropped

Produces per-week:
  - aggregate analytics across all signals
  - false_recovery_frequency
  - regime_transition_behavior
  - participation_persistence_summary
"""

from datetime import datetime
from typing import Optional
from collections import Counter
from sqlmodel import Session, select

from app.database.models import Signal, LifecycleEvent
from app.continuation_state_logger.classifier import survivability_score


# Score thresholds
HIGH_QUALITY_THRESHOLD = 3   # healthy / recovering / weakening
LOW_QUALITY_THRESHOLD  = 2   # unstable_transition / false_recovery / decaying


# ─────────────────────────────────────────────────────────────
# Per-signal lifecycle summary
# ─────────────────────────────────────────────────────────────

def compute_lifecycle_summary(signal_id: int, session: Session) -> dict:
    """
    Full lifecycle summary for one signal.
    Used by weekly exporter and analytics endpoint.
    """
    signal = session.get(Signal, signal_id)
    if not signal:
        return {"error": f"Signal {signal_id} not found"}

    events = session.exec(
        select(LifecycleEvent)
        .where(LifecycleEvent.signal_id == signal_id)
        .order_by(LifecycleEvent.event_ts)
    ).all()

    if not events:
        return {"signal_id": signal_id, "error": "No lifecycle events recorded"}

    state_path = [e.continuation_state for e in events]
    scores     = [survivability_score(s) for s in state_path]

    decay_index = _find_decay_index(scores)
    decay_state = state_path[decay_index] if decay_index is not None else None
    decay_ts    = events[decay_index].event_ts if decay_index is not None else None

    half_life         = _classify_half_life(events, decay_index)
    decay_rate        = _compute_participation_decay_rate(events, scores)
    signal_surv_score = _compute_survivability_score(
        state_path, scores, half_life, decay_rate, signal.result
    )

    participation_events = [
        e for e in events if e.participation_quality == "persistent"
    ]
    participation_ratio = len(participation_events) / len(events)

    trap_events = [
        e for e in events
        if e.volatility_event in ("liquidity_sweep", "liquidation", "exhaustion")
    ]

    false_recovery_present = "false_recovery" in state_path
    trapped_present        = "trapped" in state_path
    recovered_from_false   = (
        false_recovery_present
        and "recovering" in state_path
        and state_path.index("recovering") > state_path.index("false_recovery")
    )

    return {
        "signal_id":                    signal_id,
        "direction":                    signal.direction,
        "week":                         signal.week,
        "session":                      signal.session,
        "result":                       signal.result,
        "net_pnl_usd":                  signal.net_pnl_usd,
        "rr_achieved":                  signal.rr_achieved,
        "regime":                       signal.regime,
        "oi_state":                     signal.oi_state,
        # Lifecycle intelligence
        "state_path":                   state_path,
        "initial_state":                state_path[0],
        "final_state":                  signal.final_continuation_state or state_path[-1],
        "decay_point":                  decay_state,
        "decay_ts":                     decay_ts.isoformat() if decay_ts else None,
        "continuation_half_life":       half_life,
        "participation_decay_rate":     decay_rate,
        "survivability_score":          signal_surv_score,
        "participation_persistence_ratio": round(participation_ratio, 2),
        "false_recovery_present":       false_recovery_present,
        "recovered_from_false_recovery": recovered_from_false,
        "trapped_present":              trapped_present,
        "trap_events_count":            len(trap_events),
        "total_lifecycle_events":       len(events),
    }


# ─────────────────────────────────────────────────────────────
# Per-week aggregate analytics
# ─────────────────────────────────────────────────────────────

def get_week_lifecycle_summaries(week: str, session: Session) -> list[dict]:
    signals = session.exec(select(Signal).where(Signal.week == week)).all()
    return [compute_lifecycle_summary(s.id, session) for s in signals]


def compute_week_analytics(week: str, session: Session) -> dict:
    """
    Aggregate intelligence across all signals in a week.
    This is the primary input for Saturday ChatGPT analysis.

    Returns a structured summary covering:
      - signal outcomes
      - continuation state distribution
      - participation behavior
      - survivability scores
      - false recovery frequency
      - regime transition patterns
      - half-life distribution
      - decay rate patterns
    """
    summaries = get_week_lifecycle_summaries(week, session)
    if not summaries:
        return {"week": week, "error": "No signals found"}

    valid = [s for s in summaries if "error" not in s]
    if not valid:
        return {"week": week, "error": "No lifecycle data yet"}

    closed  = [s for s in valid if s.get("result") in ("WIN", "LOSS")]
    wins    = [s for s in closed if s.get("result") == "WIN"]
    losses  = [s for s in closed if s.get("result") == "LOSS"]
    longs   = [s for s in valid if s.get("direction") == "LONG"]
    shorts  = [s for s in valid if s.get("direction") == "SHORT"]

    # ── Continuation state distribution ──────────────────────
    all_initial_states = [s["initial_state"] for s in valid if s.get("initial_state")]
    all_final_states   = [s["final_state"]   for s in valid if s.get("final_state")]
    all_state_paths    = []
    for s in valid:
        all_state_paths.extend(s.get("state_path", []))

    # ── Survivability scores ──────────────────────────────────
    surv_scores  = [s["survivability_score"] for s in valid if s.get("survivability_score") is not None]
    avg_surv     = round(sum(surv_scores) / len(surv_scores), 1) if surv_scores else 0
    win_surv     = [s["survivability_score"] for s in wins if s.get("survivability_score") is not None]
    loss_surv    = [s["survivability_score"] for s in losses if s.get("survivability_score") is not None]
    avg_win_surv  = round(sum(win_surv) / len(win_surv), 1) if win_surv else 0
    avg_loss_surv = round(sum(loss_surv) / len(loss_surv), 1) if loss_surv else 0

    # ── Participation ─────────────────────────────────────────
    persist_ratios = [s["participation_persistence_ratio"] for s in valid]
    avg_persist    = round(sum(persist_ratios) / len(persist_ratios), 2) if persist_ratios else 0

    # ── False recovery frequency ──────────────────────────────
    false_recovery_count   = sum(1 for s in valid if s.get("false_recovery_present"))
    recovered_count        = sum(1 for s in valid if s.get("recovered_from_false_recovery"))
    trapped_count          = sum(1 for s in valid if s.get("trapped_present"))
    false_recovery_pct     = round(false_recovery_count / len(valid) * 100, 1) if valid else 0

    # ── Half-life distribution ────────────────────────────────
    half_lives = [s["continuation_half_life"] for s in valid if s.get("continuation_half_life")]
    hl_counts  = dict(Counter(half_lives))

    # ── Decay rate distribution ───────────────────────────────
    decay_rates = [s["participation_decay_rate"] for s in valid if s.get("participation_decay_rate")]
    dr_counts   = dict(Counter(decay_rates))

    # ── W19 doctrine check ────────────────────────────────────
    # Does this week confirm W19? (false_recovery dominant in LONGs)
    long_false_recovery = sum(
        1 for s in longs if s.get("false_recovery_present")
    )
    w19_pattern_confirmed = (
        len(longs) > 0
        and long_false_recovery / len(longs) >= 0.5
    )

    # ── PnL intelligence ──────────────────────────────────────
    pnl_signals  = [s for s in closed if s.get("net_pnl_usd") is not None]
    total_net_pnl = round(sum(s["net_pnl_usd"] for s in pnl_signals), 2)

    return {
        "week": week,
        "generated_at": datetime.utcnow().isoformat(),

        "signals": {
            "total":    len(valid),
            "closed":   len(closed),
            "wins":     len(wins),
            "losses":   len(losses),
            "open":     len(valid) - len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "long":     len(longs),
            "short":    len(shorts),
            "long_wr":  round(sum(1 for s in longs if s.get("result") == "WIN") / len(longs) * 100, 1) if longs else 0,
            "short_wr": round(sum(1 for s in shorts if s.get("result") == "WIN") / len(shorts) * 100, 1) if shorts else 0,
        },

        "pnl": {
            "total_net_pnl_usd": total_net_pnl,
            "tracked_trades": len(pnl_signals),
        },

        "survivability": {
            "avg_score":           avg_surv,
            "avg_score_wins":      avg_win_surv,
            "avg_score_losses":    avg_loss_surv,
            "score_gap_win_loss":  round(avg_win_surv - avg_loss_surv, 1),
        },

        "continuation_states": {
            "initial_distribution": dict(Counter(all_initial_states)),
            "final_distribution":   dict(Counter(all_final_states)),
            "all_states_observed":  dict(Counter(all_state_paths)),
        },

        "participation": {
            "avg_persistence_ratio":    avg_persist,
            "false_recovery_count":     false_recovery_count,
            "false_recovery_pct":       false_recovery_pct,
            "recovered_from_false":     recovered_count,
            "trapped_count":            trapped_count,
            "w19_pattern_confirmed":    w19_pattern_confirmed,
        },

        "half_life_distribution":   hl_counts,
        "decay_rate_distribution":  dr_counts,

        "doctrine_check": {
            "w19_false_recovery_dominant": w19_pattern_confirmed,
            "long_false_recovery_count":   long_false_recovery,
            "long_total":                  len(longs),
            "note": (
                "W19 pattern confirmed: LONG signals dominated by false recovery"
                if w19_pattern_confirmed
                else "W19 pattern not dominant this week — check if regime improved"
            ),
        },

        "signal_details": valid,
    }


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _find_decay_index(scores: list[int]) -> Optional[int]:
    for i, score in enumerate(scores):
        if score < HIGH_QUALITY_THRESHOLD:
            return i
    return None


def _classify_half_life(
    events: list,
    decay_index: Optional[int],
) -> str:
    if not events:
        return "unknown"
    if decay_index is None:
        return "long"
    if decay_index == 0:
        return "immediate"
    fraction = decay_index / len(events)
    if fraction >= 0.6:
        return "long"
    if fraction >= 0.3:
        return "moderate"
    return "short"


def _compute_participation_decay_rate(
    events: list,
    scores: list[int],
) -> str:
    """
    Classify how fast participation deteriorated across the lifecycle.

    none      — no decay observed
    gradual   — score declined steadily over multiple events
    rapid     — score dropped more than 2 points in a single step
    immediate — already below threshold at first event
    volatile  — score oscillated up and down
    """
    if not scores or len(scores) < 2:
        return "insufficient_data"

    if scores[0] < HIGH_QUALITY_THRESHOLD:
        return "immediate"

    deltas = [scores[i] - scores[i-1] for i in range(1, len(scores))]
    drops  = [d for d in deltas if d < 0]
    rises  = [d for d in deltas if d > 0]

    if not drops:
        return "none"

    # Oscillating: both drops and rises present
    if rises and drops:
        return "volatile"

    max_single_drop = min(deltas)  # most negative delta
    if max_single_drop <= -2:
        return "rapid"

    return "gradual"


def _compute_survivability_score(
    state_path: list[str],
    scores: list[int],
    half_life: str,
    decay_rate: str,
    result: Optional[str],
) -> int:
    """
    Composite survivability score 0-100 based on lifecycle evidence.

    Components:
      - avg survivability score of states observed (0-40)
      - continuation half-life quality              (0-25)
      - participation decay rate penalty            (0-20)
      - outcome confirmation                        (0-15)

    This score reflects how well the signal's continuation held up,
    independent of win/loss (a lucky WIN with immediate decay still
    scores low — it survived by price, not by participation quality).
    """
    if not scores:
        return 0

    # Component 1: avg state score (0-40)
    avg_state = sum(scores) / len(scores)
    state_component = round((avg_state / 5) * 40)

    # Component 2: half-life (0-25)
    hl_map = {"long": 25, "moderate": 15, "short": 6, "immediate": 0, "unknown": 10}
    hl_component = hl_map.get(half_life, 10)

    # Component 3: decay rate (0-20 — inverted penalty)
    dr_map = {
        "none": 20, "gradual": 14, "volatile": 8,
        "rapid": 3, "immediate": 0, "insufficient_data": 10,
    }
    dr_component = dr_map.get(decay_rate, 10)

    # Component 4: outcome (0-15)
    outcome_component = 0
    if result == "WIN":
        outcome_component = 15
    elif result == "LOSS":
        outcome_component = 0
    # OPEN = 0 (not yet scored)

    total = state_component + hl_component + dr_component + outcome_component
    return min(total, 100)
