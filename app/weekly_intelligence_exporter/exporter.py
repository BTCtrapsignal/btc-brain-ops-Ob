"""
weekly_intelligence_exporter/exporter.py

Generates the weekly intelligence markdown export.

W22 UPDATE: Zero-Signal Mode
  Exports meaningful intelligence even when signals = 0.
  When no signals exist, report is built from:
    - MissedOpportunity records (suppression data)
    - EventLog SETUP_REJECTED events (filter breakdown)
    - Calibration summary

W22 UPDATE: Reliability Metadata
  WeeklyExport now stores counts at generation time so ChatGPT
  can validate report completeness during Saturday review.
"""

from collections import Counter
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select

from app.database.models import Signal, LifecycleEvent, MissedOpportunity, EventLog, WeeklyExport, EngineeringObservation, EngineeringReview, EngineeringEvidence
from app.signal_lifecycle_tracker.tracker import get_week_lifecycle_summaries
from app.continuation_state_logger.classifier import state_description, survivability_score


# ─────────────────────────────────────────────────────────────
# REQ-W27-002 version metadata
# Kept local to this exporter so weekly metadata generation does
# not depend on importing the engineering exporter module.
# ─────────────────────────────────────────────────────────────
PACKAGE_VERSION_PREFIX = "W27"
SCHEMA_VERSION = "engineering-export-v1"
_BRAIN_OPS_VERSION = "1.2.0"
COMPATIBLE_RUNTIME = "Signal Bot v7.9+"


# ─────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────

def generate_weekly_markdown(week: str, session: Session) -> str:
    """
    Generate full weekly intelligence markdown.
    Works even when signals = 0 (W22 zero-signal mode).
    """
    signals  = session.exec(select(Signal).where(Signal.week == week)).all()
    missed   = session.exec(select(MissedOpportunity).where(MissedOpportunity.week == week)).all()
    events   = session.exec(select(EventLog).where(EventLog.week == week)).all()

    has_signals = len(signals) > 0
    has_missed  = len(missed)  > 0
    has_events  = len(events)  > 0

    # If truly empty — still generate a minimal report, not a blank
    if not has_signals and not has_missed and not has_events:
        return _empty_report(week)

    lines = []
    lines += _header(week)

    if has_signals:
        lines += _signal_summary(signals)
        lines += _continuation_state_section(signals, session)
        lines += _participation_section(signals, session)
        lines += _volatility_section(signals, session)
        lines += _half_life_section(signals, session)
        lines += _state_path_narratives(signals, session)
        lines += _recurring_patterns(signals, session)
    else:
        lines += _zero_signal_banner(week, missed, events)

    lines += _suppression_breakdown(missed, events, week)
    lines += _missed_opportunity_section(missed)
    lines += _adaptive_notes(week, has_signals)
    lines += _report_footer(week, len(signals), len(missed), len(events))

    return "\n".join(lines)


def get_generation_metadata(week: str, session: Session) -> dict:
    """
    Return metadata about the generated export for storage in WeeklyExport.

    Existing keys (unchanged — W22):
      signal_count_at_generation
      missed_count_at_generation
      event_count_at_generation
      is_complete

    REQ-W27-002 additions:
      package_version             — e.g. "W27.0"
      schema_version              — e.g. "engineering-export-v1"
      generator_version           — Brain Ops version
      compatible_runtime          — minimum Signal Bot version
      eo_count_at_generation      — EOs in database at generation time
      er_count_at_generation      — ERs in database at generation time
      evidence_count_at_generation — evidence records at generation time
    """
    # ── Existing counts (unchanged) ───────────────────────────
    signal_count = len(session.exec(select(Signal).where(Signal.week == week)).all())
    missed_count = len(session.exec(select(MissedOpportunity).where(MissedOpportunity.week == week)).all())
    event_count  = len(session.exec(select(EventLog).where(EventLog.week == week)).all())
    is_complete  = signal_count > 0

    # ── REQ-W27-002: Engineering counts ──────────────────────
    eo_count       = len(session.exec(select(EngineeringObservation)).all())
    er_count       = len(session.exec(select(EngineeringReview)).all())
    evidence_count = len(session.exec(select(EngineeringEvidence)).all())

    return {
        # Existing keys — preserved exactly
        "signal_count_at_generation": signal_count,
        "missed_count_at_generation":  missed_count,
        "event_count_at_generation":   event_count,
        "is_complete":                 is_complete,
        # REQ-W27-002: versioning
        "package_version":             f"{PACKAGE_VERSION_PREFIX}.0",
        "schema_version":              SCHEMA_VERSION,
        "generator_version":           f"Brain Ops {_BRAIN_OPS_VERSION}",
        "compatible_runtime":          COMPATIBLE_RUNTIME,
        # REQ-W27-002: engineering counts
        "eo_count_at_generation":      eo_count,
        "er_count_at_generation":      er_count,
        "evidence_count_at_generation": evidence_count,
    }

# ─────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────

def _header(week: str) -> list:
    return [
        f"# Weekly Intelligence Report — {week}",
        "",
        "> Structure gives directional permission.",
        "> Participation gives continuation permission.",
        "> Persistence gives survivability proof.",
        "",
    ]


def _zero_signal_banner(week: str, missed: list, events: list) -> list:
    """
    Shown when signals = 0. Explains what happened instead of showing blank.
    """
    rejected = [e for e in events if e.event_type == "SETUP_REJECTED"]
    lines = [
        "## Signal Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Signals executed | **0** |",
        f"| Setups detected | {len(missed)} |",
        f"| Setups rejected (EventLog) | {len(rejected)} |",
        f"| W22 Phase 1 status | Suppression gates calibrated, evidence collecting |",
        "",
        "> **Zero-Signal Week** — Suppression dominated execution.",
        "> This is expected during W22 Phase 1 gate calibration.",
        "> Intelligence below reflects what was detected and why it was blocked.",
        "",
    ]
    return lines


def _signal_summary(signals: list) -> list:
    total      = len(signals)
    wins       = sum(1 for s in signals if s.result == "WIN")
    losses     = sum(1 for s in signals if s.result == "LOSS")
    open_t     = sum(1 for s in signals if s.result == "OPEN")
    wr         = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    longs      = [s for s in signals if s.direction == "LONG"]
    shorts     = [s for s in signals if s.direction == "SHORT"]
    long_wins  = sum(1 for s in longs  if s.result == "WIN")
    short_wins = sum(1 for s in shorts if s.result == "WIN")
    long_wr    = (long_wins  / len(longs)  * 100) if longs  else 0.0
    short_wr   = (short_wins / len(shorts) * 100) if shorts else 0.0

    return [
        "## Signal Summary", "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total signals | {total} |",
        f"| Wins | {wins} |",
        f"| Losses | {losses} |",
        f"| Open | {open_t} |",
        f"| Win rate | {wr:.1f}% |",
        f"| LONG signals | {len(longs)} (WR {long_wr:.0f}%) |",
        f"| SHORT signals | {len(shorts)} (WR {short_wr:.0f}%) |",
        "",
    ]


def _suppression_breakdown(missed: list, events: list, week: str) -> list:
    """
    W22: Full suppression breakdown — the primary section when signals = 0.
    Shows which filters blocked what, and suppression cost where assessable.
    """
    lines = ["## Suppression Breakdown", ""]

    if not missed and not events:
        lines += ["_No suppression data recorded this week._",
                  "_Ensure Signal Bot is sending to POST /calibration/missed._", ""]
        return lines

    # Filter frequency from MissedOpportunity
    filter_counts: dict = {}
    near_valid_by_filter: dict = {}
    direction_counts: dict = {"LONG": 0, "SHORT": 0}
    suppression_types: dict = {}

    for m in missed:
        # Direction tracking
        d = m.direction or "UNKNOWN"
        direction_counts[d] = direction_counts.get(d, 0) + 1

        # Suppression type
        if m.suppression_type:
            suppression_types[m.suppression_type] = suppression_types.get(m.suppression_type, 0) + 1

        # Filter breakdown
        if m.blocked_by:
            for f in m.blocked_by.split(","):
                f = f.strip()
                if not f:
                    continue
                filter_counts[f] = filter_counts.get(f, 0) + 1
                if m.near_valid:
                    near_valid_by_filter[f] = near_valid_by_filter.get(f, 0) + 1

    # Summary table
    assessed = [m for m in missed if m.suppression_type]
    suppressed_wins   = sum(1 for m in assessed if m.suppression_type == "suppressed_win")
    protected_losses  = sum(1 for m in assessed if m.suppression_type == "protected_loss")
    neutral_count     = sum(1 for m in assessed if m.suppression_type == "neutral")
    cost_pct = round(suppressed_wins / len(assessed) * 100, 1) if assessed else None

    lines += [
        "### Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total blocked setups | {len(missed)} |",
        f"| Near-valid (almost passed) | {sum(1 for m in missed if m.near_valid)} |",
        f"| Assessed outcomes | {len(assessed)} |",
        f"| Suppressed wins | {suppressed_wins} |",
        f"| Protected losses | {protected_losses} |",
        f"| Neutral (no impact) | {neutral_count} |",
        f"| Suppression cost | {f'{cost_pct}%' if cost_pct is not None else 'N/A — collect more data'} |",
        "",
    ]

    # By direction
    if any(direction_counts.values()):
        lines += [
            "### Blocked by Direction",
            "",
            f"| Direction | Blocked |",
            f"|-----------|---------|",
        ]
        for d, cnt in direction_counts.items():
            if cnt > 0:
                lines.append(f"| {d} | {cnt} |")
        lines.append("")

    # By filter — the key diagnostic section
    if filter_counts:
        sorted_filters = sorted(filter_counts.items(), key=lambda x: -x[1])
        lines += [
            "### Filter Suppression Frequency",
            "",
            "_Which W22 Phase 1 gates blocked most setups:_",
            "",
            "| Filter | Blocked | Near-Valid |",
            "|--------|---------|------------|",
        ]
        for fname, cnt in sorted_filters:
            nv = near_valid_by_filter.get(fname, 0)
            lines.append(f"| `{fname}` | {cnt} | {nv} |")
        lines += [
            "",
            "> **Near-valid**: setup was very close to passing this filter.",
            "> High near-valid count = threshold recalibration candidate.",
            "",
        ]

    # Moves after rejection (if available)
    moves_2h = [m.move_2h_pct for m in missed if m.move_2h_pct is not None]
    if moves_2h:
        avg_move = round(sum(moves_2h) / len(moves_2h), 2)
        positive = sum(1 for mv in moves_2h if mv > 0.5)
        lines += [
            "### Post-Rejection Market Behavior",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Setups with 2h outcome | {len(moves_2h)} |",
            f"| Average directional move (2h) | {avg_move:+.2f}% |",
            f"| Moved in rejected direction | {positive}/{len(moves_2h)} |",
            "",
        ]

    return lines


def _missed_opportunity_section(missed: list) -> list:
    """W22: Detailed missed opportunity records."""
    lines = ["## Missed Opportunity Detail", ""]

    if not missed:
        lines += ["_No missed opportunities recorded._", ""]
        return lines

    near_valid  = [m for m in missed if m.near_valid]
    suppressed  = [m for m in missed if m.suppression_type == "suppressed_win"]

    lines += [
        f"**Total recorded:** {len(missed)} | "
        f"**Near-valid:** {len(near_valid)} | "
        f"**Confirmed suppressed wins:** {len(suppressed)}",
        "",
    ]

    # Near-valid setups deserve the most attention
    if near_valid:
        lines += ["### Near-Valid Setups (threshold recalibration candidates)", ""]
        for m in near_valid[:10]:  # cap at 10 for readability
            filters = m.blocked_by or "unknown"
            move    = f"{m.move_2h_pct:+.2f}%" if m.move_2h_pct is not None else "N/A"
            stype   = m.suppression_type or "unassessed"
            lines.append(
                f"- **{m.direction}** @ ${m.price_at_rejection:,.0f} | "
                f"Session: {m.session} | "
                f"Blocked: `{filters}` | "
                f"2h move: {move} | "
                f"Outcome: {stype}"
            )
        lines.append("")

    # Confirmed suppressed wins (most important for Phase 2 decision)
    if suppressed:
        lines += ["### Confirmed Suppressed Wins", ""]
        for m in suppressed[:10]:
            lines.append(
                f"- **{m.direction}** @ ${m.price_at_rejection:,.0f} | "
                f"Blocked: `{m.blocked_by}` | "
                f"2h: {m.move_2h_pct:+.2f}%"
            )
        lines.append("")

    return lines


def _continuation_state_section(signals, session) -> list:
    summaries = get_week_lifecycle_summaries(week=signals[0].week, session=session) if signals else []
    initial_states = [s.get("initial_state") for s in summaries if s.get("initial_state")]
    final_states   = [s.get("final_state")   for s in summaries if s.get("final_state")]
    lines = ["## Continuation State Analysis", ""]
    if initial_states:
        lines += ["### Initial States at Signal Activation", ""]
        for state, count in Counter(initial_states).most_common():
            lines.append(f"- **{state}** × {count} — survivability {survivability_score(state)}/5 — {state_description(state)}")
        lines.append("")
    if final_states:
        lines += ["### Final States at Close", ""]
        for state, count in Counter(final_states).most_common():
            lines.append(f"- **{state}** × {count}")
        lines.append("")
    return lines


def _participation_section(signals, session) -> list:
    all_events = []
    for sig in signals:
        all_events.extend(session.exec(
            select(LifecycleEvent).where(LifecycleEvent.signal_id == sig.id)
        ).all())
    lines = ["## Participation Intelligence", ""]
    if not all_events:
        lines += ["_No lifecycle events recorded._", ""]
        return lines
    pq_counts = Counter(e.participation_quality for e in all_events if e.participation_quality)
    if pq_counts:
        lines += ["### Participation Quality Distribution", ""]
        for pq, count in pq_counts.most_common():
            lines.append(f"- **{pq}**: {count} observations")
        lines.append("")
    oi_exp = sum(1 for e in all_events if e.oi_expanding is True)
    oi_flat = sum(1 for e in all_events if e.oi_expanding is False)
    lines += [
        "### Participation Persistence Signals",
        f"- OI expanding: {oi_exp} events",
        f"- OI flat/contracting: {oi_flat} events",
        f"- Volume persisting: {sum(1 for e in all_events if e.volume_persisting is True)} events",
        f"- Follow-through confirmed: {sum(1 for e in all_events if e.follow_through is True)} events",
        "",
    ]
    return lines


def _volatility_section(signals, session) -> list:
    all_events = []
    for sig in signals:
        all_events.extend(session.exec(
            select(LifecycleEvent).where(LifecycleEvent.signal_id == sig.id)
        ).all())
    lines = ["## Volatility Events", ""]
    vol_events = [e for e in all_events if e.volatility_event]
    if vol_events:
        for ve, count in Counter(e.volatility_event for e in vol_events).most_common():
            lines.append(f"- **{ve}**: {count} events")
    else:
        lines.append("_No volatility events recorded._")
    lines.append("")
    return lines


def _half_life_section(signals, session) -> list:
    summaries  = get_week_lifecycle_summaries(week=signals[0].week, session=session)
    half_lives = [s.get("continuation_half_life") for s in summaries if s.get("continuation_half_life")]
    lines = ["## Continuation Half-Life Analysis", ""]
    if half_lives:
        for hl, count in Counter(half_lives).most_common():
            lines.append(f"- **{hl}**: {count} signals")
        lines.append("")
        immediate = Counter(half_lives).get("immediate", 0) + Counter(half_lives).get("short", 0)
        if immediate:
            lines.append(f"> ⚠️ {immediate} signal(s) showed immediate or short half-life.")
        lines.append("")
    return lines


def _state_path_narratives(signals, session) -> list:
    summaries = get_week_lifecycle_summaries(week=signals[0].week, session=session)
    lines = ["## State Path Narratives", ""]
    for s in summaries:
        if s.get("error") or not s.get("state_path"):
            continue
        path_str = " → ".join(s["state_path"])
        lines += [
            f"**Signal #{s['signal_id']}** ({s['direction']} / {s['result']} / half-life: {s.get('continuation_half_life','?')})",
            "```", path_str, "```",
        ]
        if s.get("decay_point"):
            lines.append(f"Decay point: **{s['decay_point']}**")
        lines.append("")
    return lines


def _recurring_patterns(signals, session) -> list:
    summaries = get_week_lifecycle_summaries(week=signals[0].week, session=session)
    lines = ["## Recurring Patterns This Week", ""]
    fr  = sum(1 for s in summaries if "false_recovery" in s.get("state_path", []))
    tr  = sum(1 for s in summaries if "trapped"        in s.get("state_path", []))
    imm = sum(1 for s in summaries if s.get("continuation_half_life") == "immediate")
    if fr:  lines.append(f"- **False recovery continuation**: {fr} signal(s)")
    if tr:  lines.append(f"- **Trapped continuation**: {tr} signal(s)")
    if imm: lines.append(f"- **Immediate decay**: {imm} signal(s)")
    if not any([fr, tr, imm]):
        lines.append("_No dominant failure patterns this week._")
    lines.append("")
    return lines


def _adaptive_notes(week: str, has_signals: bool) -> list:
    lines = ["## Adaptive Intelligence Notes — W22 Review Checklist", ""]
    if not has_signals:
        lines += [
            "_W22 zero-signal week — focus on suppression analysis:_",
            "",
            f"- [ ] Which filter blocked the most setups this week?",
            f"- [ ] Were any near-valid setups blocked by ATR threshold (C1)?",
            f"- [ ] Were any SHORT setups blocked by RSI gate (C2/C6)?",
            f"- [ ] Did emergency events occur? Did bearish continuation follow?",
            f"- [ ] What is the suppression cost % (suppressed wins / assessed)?",
            f"- [ ] Do suppression_cost_pct results justify Phase 2 threshold relaxation?",
            f"- [ ] W22 filter performance vs W21: better, worse, or same?",
            "",
        ]
    else:
        lines += [
            f"- [ ] Did {week} confirm, reject, or mutate W19 doctrine?",
            "- [ ] Did participation recover or remain absent?",
            "- [ ] Did OI expand persistently or spike and fade?",
            "- [ ] Did volatility expansion become accepted or revert?",
            "- [ ] What filter evolution does this week suggest?",
            "",
        ]
    return lines


def _report_footer(week: str, signal_count: int, missed_count: int, event_count: int) -> list:
    is_complete = signal_count > 0 or missed_count > 0 or event_count > 0
    return [
        "---",
        f"_Generated by btc-brain-ops — {week}_",
        f"_Report metadata: signals={signal_count} missed={missed_count} events={event_count} complete={is_complete}_",
    ]


def _empty_report(week: str) -> str:
    return "\n".join([
        f"# Weekly Intelligence Report — {week}",
        "",
        "## Status: No Data",
        "",
        "_No signals, missed opportunities, or events recorded for this week._",
        "",
        "**Possible causes:**",
        "- Signal Bot not yet sending to OPS (check BTC_BRAIN_OPS_URL env var)",
        "- Week identifier mismatch (check week format: W22-2026)",
        "- OPS deployment issue (check /health endpoint)",
        "",
        f"_Generated by btc-brain-ops — {week}_",
    ])
