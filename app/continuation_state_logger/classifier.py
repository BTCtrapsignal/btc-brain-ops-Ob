"""
continuation_state_logger/classifier.py

Continuation state classification engine for btc-brain-ops.

IMPORTANT ARCHITECTURE PRINCIPLE:
=================================
Initial classification is a HYPOTHESIS, not a truth-state.

  initial state hypothesis
  → lifecycle observation
  → continuation evolution
  → decay / persistence evidence
  → weekly intelligence update
  → adaptive memory refinement

The classifier produces:
  - an opening state estimate (probabilistic, based on available context)
  - a confidence score (how certain is this estimate?)
  - a reasoning string (what drove this classification?)

The lifecycle layer then UPDATES this estimate as real observations arrive.
Observations always outweigh the opening hypothesis.
The system should never "decide too early."

W19 DOCTRINE NOTE:
==================
W19 showed that LONG + neutral OI environments tend toward false recovery.
This is encoded as a PRIOR TENDENCY, not a permanent regime law.

W19 is a memory anchor that informs the opening hypothesis.
It is NOT a universal rule that applies to all future LONG signals.

The system must remain open to healthy LONG continuation
when lifecycle observations confirm persistent participation.

CONTINUATION STATES:
====================
  healthy            — participation expanding, volatility accepted, follow-through persisting
  weakening          — participation slowing, OI growth fading, follow-through thinning
  decaying           — price moving but commitment not compounding
  exhausted          — volatility spike without continuation
  trapped            — expansion punishes one-sided participation
  unstable_transition — regime shifting, structure and participation disagree
  recovering         — participation returning after weakness
  false_recovery     — structure improves but participation does not (yet)

CORE DOCTRINE:
==============
  Structure gives directional permission.
  Participation gives continuation permission.
  Persistence gives survivability proof.

  But survivability must be continuously observed after activation —
  not assumed immediately at entry.
"""

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Opening hypothesis result
# ---------------------------------------------------------------------------

@dataclass
class OpeningHypothesis:
    """
    The result of classify_initial_hypothesis().

    state                 — best-guess continuation state at signal activation
    confidence            — how confident is this estimate? (0.0 – 1.0)
                            low  = more uncertainty, lifecycle observations matter more
                            high = stronger prior, but still revisable by observations
    reasoning             — human-readable explanation of what drove this classification
    memory_anchor         — which week's doctrine influenced this (for traceability)
    requires_confirmation — always True in Phase 1: every state needs lifecycle evidence
    """
    state: str
    confidence: float
    reasoning: str
    memory_anchor: Optional[str] = None
    requires_confirmation: bool = True

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "memory_anchor": self.memory_anchor,
            "requires_confirmation": self.requires_confirmation,
        }


# ---------------------------------------------------------------------------
# Initial hypothesis classifier
# ---------------------------------------------------------------------------

def classify_initial_state(
    direction: str,
    regime: Optional[str],
    oi_state: Optional[str],
    trend_4h: Optional[str],
) -> str:
    """
    Convenience wrapper returning just the state string.
    Use classify_initial_hypothesis() when full context is needed.
    """
    return classify_initial_hypothesis(
        direction=direction,
        regime=regime,
        oi_state=oi_state,
        trend_4h=trend_4h,
    ).state


def classify_initial_hypothesis(
    direction: str,
    regime: Optional[str],
    oi_state: Optional[str],
    trend_4h: Optional[str],
) -> OpeningHypothesis:
    """
    Produce a probabilistic opening hypothesis for a signal's continuation state.

    This is an ESTIMATE based on limited information at signal time.
    All states returned here are provisional.
    The lifecycle layer must update them as observations arrive.

    Confidence ceiling is 0.65 — no single-point-in-time snapshot
    justifies high certainty before lifecycle evidence exists.

    W19 memory is encoded as a prior tendency (lower confidence, flags uncertainty)
    rather than a deterministic rule (which would prejudge the outcome).
    """
    direction = direction.upper()
    regime = (regime or "").upper()
    oi = (oi_state or "").lower()
    trend = (trend_4h or "").upper()

    # ------------------------------------------------------------------
    # LONG signal hypotheses
    # ------------------------------------------------------------------
    if direction == "LONG":

        # Best-supported case: all signals aligned
        if oi == "expanding" and regime == "TREND" and trend == "BULLISH":
            return OpeningHypothesis(
                state="healthy",
                confidence=0.60,
                reasoning=(
                    "OI expanding + TREND regime + bullish 4H alignment. "
                    "Favorable participation context at entry. "
                    "Confidence is moderate — healthy continuation must still be confirmed "
                    "by persistent follow-through across candles."
                ),
                requires_confirmation=True,
            )

        # Expanding OI in RANGE — potential breakout or sweep, uncertain
        if oi == "expanding" and regime == "RANGE":
            return OpeningHypothesis(
                state="unstable_transition",
                confidence=0.50,
                reasoning=(
                    "OI expanding suggests participation, but RANGE regime creates tension. "
                    "May be a genuine breakout attempt or a liquidity sweep. "
                    "Lifecycle observations needed to distinguish."
                ),
                requires_confirmation=True,
            )

        # Neutral OI + RANGE — W19 prior territory
        # Hypothesis: unstable_transition (not false_recovery directly)
        # because the opening observation cannot yet confirm participation failure
        if oi == "neutral" and regime == "RANGE":
            return OpeningHypothesis(
                state="unstable_transition",
                confidence=0.45,
                reasoning=(
                    "Neutral OI + RANGE regime. W18/W19 memory: this combination "
                    "showed a tendency toward continuation failure due to absent participation. "
                    "Opening hypothesis is unstable_transition — not false_recovery — "
                    "because the first candles may confirm or deny participation. "
                    "Watch OI and volume closely in early lifecycle."
                ),
                memory_anchor="W19",
                requires_confirmation=True,
            )

        # Neutral OI + TREND — structure helps but participation unclear
        if oi == "neutral" and regime == "TREND":
            return OpeningHypothesis(
                state="weakening",
                confidence=0.40,
                reasoning=(
                    "TREND regime provides structural support, but neutral OI suggests "
                    "participation is not expanding with price. "
                    "Hypothesis: weakening. Trend may carry price but "
                    "continuation persistence is uncertain. "
                    "Confirm with OI expansion in early lifecycle observations."
                ),
                memory_anchor="W19",
                requires_confirmation=True,
            )

        # Contracting OI — participation actively leaving
        if oi == "contracting":
            return OpeningHypothesis(
                state="decaying",
                confidence=0.50,
                reasoning=(
                    "Contracting OI at signal time: participation is leaving the market. "
                    "Continuation survivability is low at entry. "
                    "Strong OI reversal needed to support recovery."
                ),
                requires_confirmation=True,
            )

        # Insufficient context
        return OpeningHypothesis(
            state="unstable_transition",
            confidence=0.25,
            reasoning=(
                "Insufficient context for confident hypothesis. "
                f"regime={regime or 'unknown'}, oi={oi or 'unknown'}, "
                f"trend_4h={trend or 'unknown'}. "
                "Treating as unstable_transition until lifecycle evidence arrives."
            ),
            requires_confirmation=True,
        )

    # ------------------------------------------------------------------
    # SHORT signal hypotheses
    # ------------------------------------------------------------------
    if direction == "SHORT":

        # Clean SHORT: contracting OI + TREND + bearish structure
        if oi == "contracting" and regime == "TREND" and trend == "BEARISH":
            return OpeningHypothesis(
                state="healthy",
                confidence=0.60,
                reasoning=(
                    "OI contracting + TREND regime + bearish 4H alignment. "
                    "Participation context supports downside continuation. "
                    "Confirm with volume and follow-through persistence."
                ),
                requires_confirmation=True,
            )

        # SHORT into RANGE with contracting OI — limited continuation persistence
        if oi == "contracting" and regime == "RANGE":
            return OpeningHypothesis(
                state="weakening",
                confidence=0.40,
                reasoning=(
                    "OI contracting suggests some downside participation, "
                    "but RANGE regime limits continuation persistence. "
                    "Range boundary may act as support. Confirm follow-through."
                ),
                requires_confirmation=True,
            )

        # Expanding OI during SHORT — ambiguous, possible squeeze risk
        if oi == "expanding":
            return OpeningHypothesis(
                state="unstable_transition",
                confidence=0.40,
                reasoning=(
                    "Expanding OI during SHORT signal is ambiguous: "
                    "could indicate short-squeeze risk or genuine participation. "
                    "Unstable transition until lifecycle clarifies direction of OI flow."
                ),
                requires_confirmation=True,
            )

        # Neutral OI SHORT — W19 showed SHORT had cleaner continuation, but still uncertain
        if oi == "neutral":
            return OpeningHypothesis(
                state="unstable_transition",
                confidence=0.40,
                reasoning=(
                    "Neutral OI for SHORT signal. Participation not confirmed. "
                    "W19 memory: SHORT signals showed cleaner continuation than LONG "
                    "in similar environments, but neutral OI still weakens the hypothesis. "
                    "Watch for participation confirmation in first lifecycle events."
                ),
                memory_anchor="W19",
                requires_confirmation=True,
            )

        # Insufficient context
        return OpeningHypothesis(
            state="unstable_transition",
            confidence=0.25,
            reasoning=(
                "Insufficient context for SHORT hypothesis. "
                "Treating as unstable_transition until lifecycle evidence arrives."
            ),
            requires_confirmation=True,
        )

    # ------------------------------------------------------------------
    # Unknown direction
    # ------------------------------------------------------------------
    return OpeningHypothesis(
        state="unstable_transition",
        confidence=0.15,
        reasoning="Unknown direction. Cannot form meaningful opening hypothesis.",
        requires_confirmation=True,
    )


# ---------------------------------------------------------------------------
# Transition classifier (lifecycle updates)
# ---------------------------------------------------------------------------

@dataclass
class TransitionResult:
    """
    Result of classify_transition_full().

    next_state          — updated continuation state estimate
    confidence_delta    — how much this observation shifts overall confidence
                          positive = evidence supports current trajectory
                          negative = evidence contradicts or introduces uncertainty
    observation_weight  — how complete was this observation?
                          complete = all three signals (OI + volume + follow-through)
                          partial  = some signals
                          minimal  = very little data
    reasoning           — what drove this transition
    """
    next_state: str
    confidence_delta: float
    observation_weight: str
    reasoning: str

    def as_dict(self) -> dict:
        return {
            "next_state": self.next_state,
            "confidence_delta": self.confidence_delta,
            "observation_weight": self.observation_weight,
            "reasoning": self.reasoning,
        }


def classify_transition(
    current_state: str,
    oi_expanding: Optional[bool],
    volume_persisting: Optional[bool],
    follow_through: Optional[bool],
    volatility_event: Optional[str],
) -> str:
    """
    Convenience wrapper returning just the next state string.
    Use classify_transition_full() when full context is needed.
    """
    return classify_transition_full(
        current_state=current_state,
        oi_expanding=oi_expanding,
        volume_persisting=volume_persisting,
        follow_through=follow_through,
        volatility_event=volatility_event,
    ).next_state


def classify_transition_full(
    current_state: str,
    oi_expanding: Optional[bool],
    volume_persisting: Optional[bool],
    follow_through: Optional[bool],
    volatility_event: Optional[str],
) -> TransitionResult:
    """
    Given the current estimated state and new lifecycle observations,
    compute the updated state estimate.

    KEY PRINCIPLE:
    Transitions are driven by EVIDENCE, not assumptions.
    - Strong complete evidence moves state decisively
    - Partial evidence moves state conservatively
    - Missing evidence preserves uncertainty (does NOT auto-degrade)

    This prevents the system from being overly pessimistic
    when observations are simply incomplete rather than negative.

    A signal in false_recovery or unstable_transition CAN resolve positively
    if lifecycle observations confirm participation emergence.
    """
    # Measure observation completeness
    signals_present = sum([
        oi_expanding is not None,
        volume_persisting is not None,
        follow_through is not None,
    ])

    if signals_present == 3:
        obs_weight = "complete"
    elif signals_present >= 1:
        obs_weight = "partial"
    else:
        obs_weight = "minimal"

    # Classify participation signal from available evidence
    participation_confirmed = (
        oi_expanding is True
        and volume_persisting is True
        and follow_through is True
    )
    participation_denied = (
        (oi_expanding is False and volume_persisting is False)
        or (follow_through is False and oi_expanding is False)
    )
    # Uncertain = neither confirmed nor denied (includes partial observations)
    participation_uncertain = not participation_confirmed and not participation_denied

    is_trap_event = volatility_event in ("liquidity_sweep", "liquidation", "exhaustion")
    is_acceptance_event = volatility_event == "acceptance"

    # ------------------------------------------------------------------
    # Trap/exhaustion event: strong negative evidence
    # ------------------------------------------------------------------
    if is_trap_event:
        if current_state in ("healthy", "recovering"):
            return TransitionResult(
                next_state="trapped",
                confidence_delta=-0.30,
                observation_weight=obs_weight,
                reasoning=(
                    f"Trap/exhaustion event ({volatility_event}) during {current_state}. "
                    "Expansion punished participants. Downgrading to trapped."
                ),
            )
        return TransitionResult(
            next_state="exhausted",
            confidence_delta=-0.20,
            observation_weight=obs_weight,
            reasoning=(
                f"Trap/exhaustion event ({volatility_event}) confirmed exhaustion. "
                "Continuation failed after volatility expansion."
            ),
        )

    # Acceptance event: positive continuation evidence
    if is_acceptance_event and participation_confirmed:
        upgrade_to = "healthy" if current_state in ("recovering", "weakening", "unstable_transition") else current_state
        return TransitionResult(
            next_state=upgrade_to,
            confidence_delta=+0.20,
            observation_weight=obs_weight,
            reasoning="Acceptance expansion with persistent participation. Continuation strengthening.",
        )

    # ------------------------------------------------------------------
    # State-specific transitions
    # ------------------------------------------------------------------

    if current_state == "healthy":
        if participation_confirmed:
            return TransitionResult("healthy", +0.10, obs_weight,
                "Participation persisting. Healthy continuation confirmed by observation.")
        if participation_denied and obs_weight == "complete":
            return TransitionResult("weakening", -0.15, obs_weight,
                "Complete observation: all participation signals weakening.")
        if participation_denied and obs_weight == "partial":
            return TransitionResult("weakening", -0.08, obs_weight,
                "Partial evidence of participation weakness. Downgrading to weakening.")
        # Uncertain or minimal — hold state, small confidence erosion
        return TransitionResult("healthy", -0.05, obs_weight,
            "Insufficient observation to confirm continuation. Holding with slight uncertainty increase.")

    if current_state == "weakening":
        if participation_confirmed:
            return TransitionResult("recovering", +0.15, obs_weight,
                "Participation returning to weakening continuation. Recovery possible.")
        if participation_denied and obs_weight == "complete":
            return TransitionResult("decaying", -0.20, obs_weight,
                "Complete observation: all signals denying continuation. Decaying.")
        if participation_denied and obs_weight == "partial":
            return TransitionResult("decaying", -0.10, obs_weight,
                "Partial participation denial. Downgrading toward decay.")
        return TransitionResult("weakening", -0.03, obs_weight,
            "Continuation weakening. Insufficient evidence to resolve direction.")

    if current_state == "decaying":
        if participation_confirmed:
            return TransitionResult("recovering", +0.15, obs_weight,
                "Strong participation recovery from decay. Valid — continuation can re-emerge.")
        if obs_weight == "complete" and participation_denied:
            return TransitionResult("exhausted", -0.15, obs_weight,
                "Decay confirmed by complete observation. Exhaustion reached.")
        return TransitionResult("decaying", -0.05, obs_weight,
            "Decay continuing. Not yet exhausted.")

    if current_state == "exhausted":
        if participation_confirmed and obs_weight == "complete":
            return TransitionResult("recovering", +0.10, obs_weight,
                "Strong participation after exhaustion. Potential new continuation sequence forming.")
        return TransitionResult("exhausted", 0.0, obs_weight,
            "Exhaustion holding. No recovery evidence yet.")

    if current_state == "trapped":
        if participation_confirmed and obs_weight == "complete":
            return TransitionResult("recovering", +0.10, obs_weight,
                "Participation recovering after trap. Monitoring for new continuation.")
        return TransitionResult("trapped", 0.0, obs_weight,
            "Trap persisting. No recovery evidence yet.")

    if current_state == "unstable_transition":
        if participation_confirmed:
            return TransitionResult("recovering", +0.20, obs_weight,
                "Participation confirmed during unstable transition. Recovery hypothesis strengthening.")
        if participation_denied and obs_weight == "complete":
            return TransitionResult("false_recovery", -0.15, obs_weight,
                "Complete observation confirms participation absent during structure recovery. "
                "W19 pattern: false recovery. "
                "Note: false_recovery can still resolve positively if participation emerges.")
        # Uncertain or partial — preserve uncertainty, do not rush to false_recovery
        return TransitionResult("unstable_transition", -0.05, obs_weight,
            "Transition continuing. Insufficient observation to resolve. Preserving uncertainty.")

    if current_state == "false_recovery":
        if participation_confirmed and obs_weight == "complete":
            return TransitionResult("recovering", +0.20, obs_weight,
                "Strong participation emerging from false recovery. State upgrading. "
                "W19 pattern CAN resolve positively when participation genuinely arrives.")
        if participation_denied and follow_through is False and obs_weight in ("complete", "partial"):
            return TransitionResult("decaying", -0.15, obs_weight,
                "False recovery confirmed by continued participation absence and no follow-through.")
        return TransitionResult("false_recovery", -0.05, obs_weight,
            "False recovery persisting. Monitoring for participation change.")

    if current_state == "recovering":
        if participation_confirmed:
            return TransitionResult("healthy", +0.20, obs_weight,
                "Participation persisting through recovery. Upgrading to healthy continuation.")
        if participation_denied and obs_weight == "complete":
            return TransitionResult("weakening", -0.15, obs_weight,
                "Recovery participation fading. Downgrading to weakening.")
        return TransitionResult("recovering", -0.03, obs_weight,
            "Recovery in progress. Waiting for persistence confirmation.")

    # Unknown state
    return TransitionResult("unstable_transition", 0.0, "minimal",
        f"Unknown state '{current_state}'. Resetting to unstable_transition.")


# ---------------------------------------------------------------------------
# State metadata
# ---------------------------------------------------------------------------

# Typical survivability score per state.
# These are informational — actual survivability is determined by lifecycle evolution.
# A signal in false_recovery CAN improve. A signal in healthy CAN decay.
# Use these for reporting and pattern analysis only.
SURVIVABILITY_SCORE: dict[str, int] = {
    "healthy": 5,
    "recovering": 4,
    "weakening": 3,
    "unstable_transition": 2,
    "false_recovery": 1,
    "decaying": 1,
    "exhausted": 0,
    "trapped": 0,
}

STATE_DESCRIPTIONS: dict[str, str] = {
    "healthy": "Participation expanding persistently. Volatility accepted. Follow-through persisting.",
    "weakening": "Participation remains but slowing. Follow-through thinning. OI growth fading.",
    "decaying": "Price moving but market commitment not compounding. Follow-through failing.",
    "exhausted": "Volatility spiked but continuation failed. Reversion risk high.",
    "trapped": "Expansion punished one-sided participation. Immediate follow-through failure.",
    "unstable_transition": (
        "Regime shifting. Structure and participation disagree. "
        "Opening hypothesis unresolved — requires lifecycle evidence."
    ),
    "recovering": "Participation returning after weakness. Rebuilding over multiple candles.",
    "false_recovery": (
        "Structure improved but participation not yet confirmed. "
        "W19 prior tendency — can resolve positively if real participation emerges. "
        "Monitor OI and volume closely."
    ),
}

# States that require lifecycle confirmation before being treated as meaningful.
# All states technically require confirmation in Phase 1.
# This set flags which states have the highest uncertainty at opening.
HIGH_UNCERTAINTY_STATES: set[str] = {
    "unstable_transition",
    "false_recovery",
    "recovering",
}


def survivability_score(state: str) -> int:
    """
    Typical survivability score for this state.
    Informational only — actual survivability evolves through lifecycle observation.
    """
    return SURVIVABILITY_SCORE.get(state, 0)


def state_description(state: str) -> str:
    return STATE_DESCRIPTIONS.get(state, "Unknown state.")


def is_high_uncertainty(state: str) -> bool:
    """True when the opening hypothesis for this state has high uncertainty."""
    return state in HIGH_UNCERTAINTY_STATES
