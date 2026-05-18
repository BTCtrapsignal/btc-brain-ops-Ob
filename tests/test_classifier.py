"""
tests/test_classifier.py

Tests for the continuation state classifier.

These tests verify the PROBABILISTIC, ADAPTIVE philosophy —
not deterministic hardcoded outcomes.

Key principles tested:
  - Opening hypotheses are provisional, not truths
  - W19 patterns produce cautious priors, not permanent labels
  - Lifecycle observations can UPGRADE as well as degrade state
  - Missing evidence preserves uncertainty (does not auto-degrade)
  - false_recovery can resolve into recovering with real participation
"""

import pytest
from app.continuation_state_logger.classifier import (
    classify_initial_state,
    classify_initial_hypothesis,
    classify_transition,
    classify_transition_full,
    survivability_score,
    is_high_uncertainty,
    OpeningHypothesis,
    TransitionResult,
)


# ---------------------------------------------------------------------------
# Opening hypothesis: structure
# ---------------------------------------------------------------------------

class TestOpeningHypothesisStructure:

    def test_returns_opening_hypothesis_object(self):
        result = classify_initial_hypothesis("LONG", "RANGE", "neutral", "BULLISH")
        assert isinstance(result, OpeningHypothesis)
        assert result.state is not None
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning  # must have reasoning
        assert result.requires_confirmation is True  # always True in Phase 1

    def test_confidence_ceiling_is_below_certainty(self):
        """No single snapshot justifies certainty before lifecycle evidence."""
        result = classify_initial_hypothesis("LONG", "TREND", "expanding", "BULLISH")
        assert result.confidence <= 0.65, (
            "Opening confidence must stay below 0.65 — "
            "lifecycle observations are required before higher certainty."
        )

    def test_all_states_require_confirmation(self):
        """Phase 1: all states are provisional at ingestion."""
        cases = [
            ("LONG", "TREND", "expanding", "BULLISH"),
            ("LONG", "RANGE", "neutral", "BULLISH"),
            ("SHORT", "TREND", "contracting", "BEARISH"),
            ("LONG", None, None, None),
        ]
        for args in cases:
            result = classify_initial_hypothesis(*args)
            assert result.requires_confirmation is True, (
                f"requires_confirmation must be True for {args}"
            )


# ---------------------------------------------------------------------------
# Opening hypothesis: W19 memory encoded as tendency, not law
# ---------------------------------------------------------------------------

class TestW19MemoryEncoding:

    def test_long_neutral_oi_range_is_not_false_recovery_at_opening(self):
        """
        W19 pattern (LONG + neutral OI + RANGE) should produce a cautious prior
        but NOT hardcode false_recovery as the opening state.
        The opening state should preserve room for lifecycle resolution.
        """
        result = classify_initial_hypothesis("LONG", "RANGE", "neutral", "BULLISH")
        assert result.state != "false_recovery", (
            "Opening hypothesis must not hardcode false_recovery for the W19 pattern. "
            "This would prejudge the outcome before lifecycle evidence exists. "
            f"Got: {result.state}"
        )
        # Should be a cautious state — unstable_transition or weakening
        assert result.state in ("unstable_transition", "weakening"), (
            f"Expected a cautious uncertain state, got: {result.state}"
        )

    def test_w19_pattern_has_low_confidence(self):
        """W19 prior territory should have lower confidence than favorable conditions."""
        w19_result = classify_initial_hypothesis("LONG", "RANGE", "neutral", "BULLISH")
        favorable_result = classify_initial_hypothesis("LONG", "TREND", "expanding", "BULLISH")
        assert w19_result.confidence < favorable_result.confidence, (
            "W19 pattern should produce lower opening confidence than fully aligned conditions."
        )

    def test_w19_pattern_flags_memory_anchor(self):
        """W19 pattern should be traceable to its memory source."""
        result = classify_initial_hypothesis("LONG", "RANGE", "neutral", "BULLISH")
        assert result.memory_anchor == "W19", (
            "W19-influenced hypotheses should flag memory_anchor='W19' for traceability."
        )

    def test_w19_short_pattern_flags_memory_anchor(self):
        """W19 also showed SHORT behavior — should be traceable."""
        result = classify_initial_hypothesis("SHORT", "RANGE", "neutral", "BEARISH")
        assert result.memory_anchor == "W19"


# ---------------------------------------------------------------------------
# Opening hypothesis: favorable conditions
# ---------------------------------------------------------------------------

class TestFavorableConditions:

    def test_long_expanding_trend_bullish_is_healthy(self):
        result = classify_initial_hypothesis("LONG", "TREND", "expanding", "BULLISH")
        assert result.state == "healthy"

    def test_short_contracting_trend_bearish_is_healthy(self):
        result = classify_initial_hypothesis("SHORT", "TREND", "contracting", "BEARISH")
        assert result.state == "healthy"

    def test_healthy_still_requires_confirmation(self):
        """Even healthy opening hypothesis must be confirmed by lifecycle."""
        result = classify_initial_hypothesis("LONG", "TREND", "expanding", "BULLISH")
        assert result.requires_confirmation is True


# ---------------------------------------------------------------------------
# Opening hypothesis: insufficient context
# ---------------------------------------------------------------------------

class TestInsufficientContext:

    def test_missing_all_context_is_unstable_low_confidence(self):
        result = classify_initial_hypothesis("LONG", None, None, None)
        assert result.state == "unstable_transition"
        assert result.confidence <= 0.35, "Missing context should produce low confidence."

    def test_string_wrapper_returns_state_only(self):
        state = classify_initial_state("LONG", "RANGE", "neutral", "BULLISH")
        assert isinstance(state, str)
        assert state in (
            "healthy", "weakening", "decaying", "exhausted",
            "trapped", "unstable_transition", "recovering", "false_recovery"
        )


# ---------------------------------------------------------------------------
# Transition: evidence drives state, not assumptions
# ---------------------------------------------------------------------------

class TestTransitionEvidenceDriven:

    def test_complete_positive_evidence_upgrades_state(self):
        """Strong lifecycle evidence should be able to upgrade any cautious state."""
        result = classify_transition_full(
            current_state="unstable_transition",
            oi_expanding=True,
            volume_persisting=True,
            follow_through=True,
            volatility_event=None,
        )
        assert result.next_state == "recovering", (
            "Strong participation evidence should upgrade unstable_transition to recovering."
        )
        assert result.confidence_delta > 0

    def test_false_recovery_can_resolve_to_recovering(self):
        """
        Critical test: false_recovery is NOT a terminal state.
        Real participation emerging must be able to upgrade it.
        """
        result = classify_transition_full(
            current_state="false_recovery",
            oi_expanding=True,
            volume_persisting=True,
            follow_through=True,
            volatility_event=None,
        )
        assert result.next_state == "recovering", (
            "false_recovery must be upgradeable to recovering when participation confirms. "
            "The system must not trap signals in pessimistic states permanently."
        )
        assert result.confidence_delta > 0

    def test_missing_evidence_does_not_auto_degrade_healthy(self):
        """
        Incomplete observation should NOT immediately degrade a healthy signal.
        Missing evidence ≠ negative evidence.
        """
        result = classify_transition_full(
            current_state="healthy",
            oi_expanding=None,   # not observed
            volume_persisting=None,
            follow_through=None,
            volatility_event=None,
        )
        assert result.next_state == "healthy", (
            "Missing observations should not degrade a healthy state. "
            "Only negative evidence should degrade."
        )
        assert result.observation_weight == "minimal"

    def test_unstable_transition_with_uncertain_evidence_preserves_uncertainty(self):
        """
        Partial evidence during unstable_transition should NOT rush to false_recovery.
        The system should preserve uncertainty rather than pessimistically conclude.
        """
        result = classify_transition_full(
            current_state="unstable_transition",
            oi_expanding=None,
            volume_persisting=None,
            follow_through=None,
            volatility_event=None,
        )
        assert result.next_state == "unstable_transition", (
            "Minimal evidence should preserve unstable_transition, not jump to false_recovery."
        )

    def test_partial_negative_evidence_degrades_conservatively(self):
        """Partial negative evidence should produce smaller confidence delta than complete denial."""
        partial_result = classify_transition_full(
            current_state="healthy",
            oi_expanding=False,
            volume_persisting=None,   # not observed
            follow_through=True,
            volatility_event=None,
        )
        full_denial_result = classify_transition_full(
            current_state="healthy",
            oi_expanding=False,
            volume_persisting=False,
            follow_through=False,
            volatility_event=None,
        )
        assert abs(partial_result.confidence_delta) < abs(full_denial_result.confidence_delta), (
            "Partial negative evidence should produce smaller confidence loss than complete denial."
        )


# ---------------------------------------------------------------------------
# Transition: specific state paths
# ---------------------------------------------------------------------------

class TestStatePaths:

    def test_trap_event_downgrades_healthy_to_trapped(self):
        result = classify_transition("healthy", True, True, True, "liquidity_sweep")
        assert result == "trapped"

    def test_trap_event_downgrades_weakening_to_exhausted(self):
        result = classify_transition("weakening", False, False, False, "liquidation")
        assert result == "exhausted"

    def test_complete_denial_downgrades_unstable_to_false_recovery(self):
        """Complete negative evidence resolves unstable_transition to false_recovery."""
        result = classify_transition("unstable_transition", False, False, False, None)
        assert result == "false_recovery"

    def test_healthy_with_strong_participation_stays_healthy(self):
        result = classify_transition("healthy", True, True, True, None)
        assert result == "healthy"

    def test_recovering_with_strong_participation_becomes_healthy(self):
        result = classify_transition("recovering", True, True, True, None)
        assert result == "healthy"

    def test_decaying_with_strong_participation_recovers(self):
        """Even from decay, genuine participation should allow recovery."""
        result = classify_transition("decaying", True, True, True, None)
        assert result == "recovering"


# ---------------------------------------------------------------------------
# Survivability scores (informational only)
# ---------------------------------------------------------------------------

class TestSurvivabilityScores:

    def test_healthy_is_max(self):
        assert survivability_score("healthy") == 5

    def test_exhausted_and_trapped_are_zero(self):
        assert survivability_score("exhausted") == 0
        assert survivability_score("trapped") == 0

    def test_false_recovery_is_low_not_zero(self):
        """false_recovery has low but non-zero score — it can still improve."""
        score = survivability_score("false_recovery")
        assert score == 1, "false_recovery should be low (1) not zero — it is improvable."

    def test_unknown_state_is_zero(self):
        assert survivability_score("nonexistent_state") == 0


# ---------------------------------------------------------------------------
# High uncertainty states
# ---------------------------------------------------------------------------

class TestHighUncertainty:

    def test_unstable_transition_is_high_uncertainty(self):
        assert is_high_uncertainty("unstable_transition") is True

    def test_false_recovery_is_high_uncertainty(self):
        assert is_high_uncertainty("false_recovery") is True

    def test_healthy_is_not_high_uncertainty(self):
        """Healthy is still provisional but not in the highest uncertainty tier."""
        assert is_high_uncertainty("healthy") is False

    def test_exhausted_is_not_high_uncertainty(self):
        """Exhausted is a confirmed terminal state, not high uncertainty."""
        assert is_high_uncertainty("exhausted") is False
