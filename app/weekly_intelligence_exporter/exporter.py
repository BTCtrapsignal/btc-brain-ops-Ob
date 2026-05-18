"""
weekly_intelligence_exporter/exporter.py

Generates the weekly intelligence markdown export from the database.

Output structure:
  # Weekly Intelligence Report — W[N]

  ## Signal Summary
  ## Continuation State Analysis
  ## Participation Intelligence
  ## Volatility Events
  ## Continuation Half-Life Analysis
  ## State Path Narratives
  ## Recurring Patterns
  ## Adaptive Intelligence Notes
"""

from collections import Counter
from sqlmodel import Session, select

from app.database.models import Signal, LifecycleEvent
from app.signal_lifecycle_tracker.tracker import get_week_lifecycle_summaries
from app.continuation_state_logger.classifier import state_description, survivability_score


def generate_weekly_markdown(week: str, session: Session) -> str:
    signals = session.exec(select(Signal).where(Signal.week == week)).all()

    if not signals:
        return f"# Weekly Intelligence Report — {week}\n\n_No signals recorded for this week._\n"

    summaries = get_week_lifecycle_summaries(week=week, session=session)

    lines = []
    lines.append(f"# Weekly Intelligence Report — {week}")
    lines.append("")
    lines.append("> Structure gives directional permission.")
    lines.append("> Participation gives continuation permission.")
    lines.append("> Persistence gives survivability proof.")
    lines.append("")

    # ------------------------------------------------------------------
    # Signal Summary
    # ------------------------------------------------------------------
    lines.append("## Signal Summary")
    lines.append("")

    total = len(signals)
    wins = sum(1 for s in signals if s.result == "WIN")
    losses = sum(1 for s in signals if s.result == "LOSS")
    open_trades = sum(1 for s in signals if s.result == "OPEN")
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

    longs = [s for s in signals if s.direction == "LONG"]
    shorts = [s for s in signals if s.direction == "SHORT"]
    long_wins = sum(1 for s in longs if s.result == "WIN")
    short_wins = sum(1 for s in shorts if s.result == "WIN")
    long_wr = (long_wins / len(longs) * 100) if longs else 0.0
    short_wr = (short_wins / len(shorts) * 100) if shorts else 0.0

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total signals | {total} |")
    lines.append(f"| Wins | {wins} |")
    lines.append(f"| Losses | {losses} |")
    lines.append(f"| Open | {open_trades} |")
    lines.append(f"| Win rate | {wr:.1f}% |")
    lines.append(f"| LONG signals | {len(longs)} (WR {long_wr:.0f}%) |")
    lines.append(f"| SHORT signals | {len(shorts)} (WR {short_wr:.0f}%) |")
    lines.append("")

    # ------------------------------------------------------------------
    # Continuation State Analysis
    # ------------------------------------------------------------------
    lines.append("## Continuation State Analysis")
    lines.append("")

    initial_states = [s.get("initial_state") for s in summaries if s.get("initial_state")]
    final_states = [s.get("final_state") for s in summaries if s.get("final_state")]

    if initial_states:
        state_counts = Counter(initial_states)
        lines.append("### Initial States at Signal Activation")
        for state, count in state_counts.most_common():
            score = survivability_score(state)
            lines.append(f"- **{state}** × {count} — survivability {score}/5 — {state_description(state)}")
        lines.append("")

    if final_states:
        final_counts = Counter(final_states)
        lines.append("### Final States at Close")
        for state, count in final_counts.most_common():
            lines.append(f"- **{state}** × {count}")
        lines.append("")

    # ------------------------------------------------------------------
    # Participation Intelligence
    # ------------------------------------------------------------------
    lines.append("## Participation Intelligence")
    lines.append("")

    all_events = []
    for sig in signals:
        evts = session.exec(
            select(LifecycleEvent).where(LifecycleEvent.signal_id == sig.id)
        ).all()
        all_events.extend(evts)

    if all_events:
        pq_counts = Counter(
            e.participation_quality for e in all_events if e.participation_quality
        )
        if pq_counts:
            lines.append("### Participation Quality Distribution (all lifecycle events)")
            for pq, count in pq_counts.most_common():
                lines.append(f"- **{pq}**: {count} observations")
            lines.append("")

        oi_expanding = sum(1 for e in all_events if e.oi_expanding is True)
        oi_flat = sum(1 for e in all_events if e.oi_expanding is False)
        vol_persisting = sum(1 for e in all_events if e.volume_persisting is True)
        follow_through = sum(1 for e in all_events if e.follow_through is True)

        lines.append("### Participation Persistence Signals")
        lines.append(f"- OI expanding: {oi_expanding} events")
        lines.append(f"- OI flat/contracting: {oi_flat} events")
        lines.append(f"- Volume persisting: {vol_persisting} events")
        lines.append(f"- Follow-through confirmed: {follow_through} events")
        lines.append("")

    # ------------------------------------------------------------------
    # Volatility Events
    # ------------------------------------------------------------------
    lines.append("## Volatility Events")
    lines.append("")

    vol_events = [e for e in all_events if e.volatility_event]
    if vol_events:
        ve_counts = Counter(e.volatility_event for e in vol_events)
        for ve, count in ve_counts.most_common():
            lines.append(f"- **{ve}**: {count} events")
    else:
        lines.append("_No volatility events recorded._")
    lines.append("")

    # ------------------------------------------------------------------
    # Continuation Half-Life Analysis
    # ------------------------------------------------------------------
    lines.append("## Continuation Half-Life Analysis")
    lines.append("")

    half_lives = [s.get("continuation_half_life") for s in summaries if s.get("continuation_half_life")]
    if half_lives:
        hl_counts = Counter(half_lives)
        for hl, count in hl_counts.most_common():
            lines.append(f"- **{hl}**: {count} signals")
        lines.append("")
        immediate_count = hl_counts.get("immediate", 0) + hl_counts.get("short", 0)
        if immediate_count > 0:
            lines.append(f"> ⚠️ {immediate_count} signal(s) showed immediate or short continuation half-life — participation failure at or near entry.")
        lines.append("")

    # ------------------------------------------------------------------
    # State Path Narratives
    # ------------------------------------------------------------------
    lines.append("## State Path Narratives")
    lines.append("")

    for summary in summaries:
        if summary.get("error"):
            continue
        path = summary.get("state_path", [])
        if not path:
            continue
        direction = summary.get("direction", "?")
        result = summary.get("result", "?")
        half_life = summary.get("continuation_half_life", "?")
        sid = summary.get("signal_id")

        path_str = " → ".join(path)
        lines.append(f"**Signal #{sid}** ({direction} / {result} / half-life: {half_life})")
        lines.append(f"```")
        lines.append(path_str)
        lines.append(f"```")
        if summary.get("decay_point"):
            lines.append(f"Decay point: **{summary['decay_point']}**")
        lines.append("")

    # ------------------------------------------------------------------
    # Recurring Patterns
    # ------------------------------------------------------------------
    lines.append("## Recurring Patterns This Week")
    lines.append("")

    false_recovery_count = sum(
        1 for s in summaries if "false_recovery" in s.get("state_path", [])
    )
    trapped_count = sum(
        1 for s in summaries if "trapped" in s.get("state_path", [])
    )
    immediate_decay_count = sum(
        1 for s in summaries if s.get("continuation_half_life") == "immediate"
    )

    if false_recovery_count:
        lines.append(f"- **False recovery continuation**: {false_recovery_count} signal(s)")
    if trapped_count:
        lines.append(f"- **Trapped continuation**: {trapped_count} signal(s)")
    if immediate_decay_count:
        lines.append(f"- **Immediate decay**: {immediate_decay_count} signal(s)")

    if not any([false_recovery_count, trapped_count, immediate_decay_count]):
        lines.append("_No dominant failure patterns this week._")
    lines.append("")

    # ------------------------------------------------------------------
    # Adaptive Intelligence Notes
    # ------------------------------------------------------------------
    lines.append("## Adaptive Intelligence Notes")
    lines.append("")
    lines.append("_Fill this section during Saturday ChatGPT review._")
    lines.append("")
    lines.append("- [ ] Did W{week} confirm, reject, or mutate W19 doctrine?".replace("{week}", week))
    lines.append("- [ ] Did participation recover or remain absent?")
    lines.append("- [ ] Did OI expand persistently or spike and fade?")
    lines.append("- [ ] Did volatility expansion become accepted or revert?")
    lines.append("- [ ] What filter evolution does this week suggest?")
    lines.append("")

    lines.append("---")
    lines.append(f"_Generated by btc-brain-ops weekly intelligence exporter — {week}_")

    return "\n".join(lines)
