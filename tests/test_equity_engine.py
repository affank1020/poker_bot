"""Cross-street equity contracts, blockers, weighted ranges, and runtime sanity."""
import time
from dataclasses import FrozenInstanceError

import pytest

from card_utils import Card
from equity_engine import estimate_equity, showdown_equity, MAX_SAMPLES


def cards(text):
    return tuple(Card.parse(item) for item in text.split())


def test_unbeatable_quads_exact_hu_river():
    result = estimate_equity(cards("AS AD"), cards("AH AC 2D 3H 4S"))
    assert result.exact and result.trials == 990
    assert result.equity == result.win_rate == 1.0
    assert result.tie_rate == 0


def test_royal_flush_board_splits_every_deal():
    hero, board = cards("2C 3D"), cards("AS KS QS JS TS")
    result = estimate_equity(hero, board)
    assert result.exact and result.equity == 0.5
    assert result.win_rate == 0 and result.tie_rate == 1
    multi = estimate_equity(hero, board, opponents=2, samples=50, seed=19)
    assert not multi.exact and multi.equity == pytest.approx(1 / 3)
    assert multi.trials == 50


def test_weighted_river_equity_reweights_and_blocks():
    hero, board = cards("AH AD"), cards("2C 7D 9H JS QC")
    losing = cards("9S 9D")  # Opponent trips beat our pair.
    winning = cards("KH KD")  # Opponent kings lose to our pair.
    result = estimate_equity(
        hero, board, ranges=[{losing: 1, winning: 3}]
    )
    assert result.exact and result.trials == 2
    assert result.equity == pytest.approx(0.75)
    assert result.win_rate == pytest.approx(0.75)


def test_duplicate_combo_order_is_normalised():
    hero, board = cards("AH AD"), cards("2C 7D 9H JS QC")
    opponents = {
        cards("9S 9D"): 1,
        cards("9D 9S"): 2,
        cards("KH KD"): 3,
    }
    result = estimate_equity(hero, board, ranges=[opponents])
    assert result.trials == 2
    assert result.equity == pytest.approx(0.5)


def test_multiway_exact_small_weighted_ranges_skips_overlap():
    hero, board = cards("AS AD"), cards("AH AC 2D 3H 4S")
    first = {cards("KC KD"): 2, cards("QC QD"): 1}
    second = {cards("KC KS"): 3, cards("JC JD"): 1}
    # Only 3/4 combinations are legal: two proposed hands share KC.
    result = estimate_equity(
        hero, board, opponents=2, ranges=[first, second]
    )
    assert result.exact and result.trials == 3
    assert result.equity == 1.0


def test_all_overlapping_multiway_ranges_are_invalid():
    hero, board = cards("AS AD"), cards("AH AC 2D 3H 4S")
    fixed = {cards("KC KD"): 1}
    with pytest.raises(ValueError, match="collision-free"):
        estimate_equity(
            hero, board, opponents=2, ranges=[fixed, fixed], samples=4
        )


def test_blocked_combo_rejected_and_zero_weights_ignored():
    hero, board = cards("AS AD"), cards("2C 7D 9H JS QC")
    with pytest.raises(ValueError, match="no possible"):
        estimate_equity(hero, board, ranges=[{cards("AS 8S"): 1}])
    with pytest.raises(ValueError, match="no possible"):
        estimate_equity(hero, board, ranges=[{cards("KH KD"): 0}])


def test_seeded_flop_and_turn_deterministic():
    hero = cards("AS KS")
    for board in (cards("2S 7D JC"), cards("2S 7D JC QH")):
        first = estimate_equity(hero, board, samples=40, seed=101)
        assert first == estimate_equity(
            hero, board, samples=40, seed=101
        )
        assert first.trials == 40 and not first.exact
        assert 0 <= first.equity <= 1
        assert showdown_equity(
            hero, board, samples=40, seed=101
        ) == first.equity


def test_river_sampling_when_enumeration_budget_tiny():
    result = estimate_equity(
        cards("2C 3D"), cards("AS KS QS JS TS"),
        samples=25, seed=1, exact_limit=1
    )
    assert result.trials == 25 and not result.exact
    assert result.equity == pytest.approx(0.5)


def test_validation_of_inputs_and_nonfinite_weights():
    hero, board = cards("AH AD"), cards("2C 7D 9H JS QC")
    bad = [
        dict(hero=cards("AH AH"), board=board),
        dict(hero=hero, board=cards("2C 2C 9H")),
        dict(hero=hero, board=cards("2C 7D")),
        dict(hero=hero, board=board, opponents=0),
        dict(hero=hero, board=board, ranges=[None, None]),
        dict(hero=hero, board=board, samples=MAX_SAMPLES + 1),
        dict(hero=hero, board=board, ranges=[{cards("KH KD"): float("nan")}]),
        dict(hero=hero, board=board, ranges=[{cards("KH KD"): -1}]),
    ]
    for kwargs in bad:
        with pytest.raises(ValueError):
            estimate_equity(**(dict(hero=hero, board=board) | kwargs))


def test_result_frozen_and_bounded_runtime():
    started = time.monotonic()
    result = estimate_equity(
        cards("AS KS"), cards("2D 7H JC"), opponents=3, samples=50
    )
    assert result.trials == 50
    with pytest.raises(FrozenInstanceError):
        result.equity = 0.1
    assert time.monotonic() - started < 10  # Loose bound for shared CI


def test_relative_weight_scaling_and_duplicate_orientation_cannot_overflow():
    hero, board = cards("AH AD"), cards("2C 7D 9H JS QC")
    for scale in (1.0, 1e200, 1e-200):
        result = estimate_equity(hero, board, ranges=[{
            cards("9S 9D"): scale, cards("KH KD"): 3 * scale,
        }])
        assert result.exact and result.equity == pytest.approx(0.75)
    # The reversed pair keys describe the same hand; their raw weight
    # sum overflows a float, but the resulting exact equity must be valid.
    result = estimate_equity(hero, board, ranges=[{
        cards("9S 9D"): 1e308, cards("9D 9S"): 1e308,
        cards("KH KD"): 1e308,
    }])
    assert result.exact and result.equity == pytest.approx(1 / 3)


def test_exact_multiway_handles_underflowing_joint_weight():
    hero, board = cards("AS AD"), cards("AH AC 2D 3H 4S")
    # Only the joint deal made of two 1e-200 entries can exist:
    # its product is 1e-400, which underflows ordinary floating-point.
    first = {cards("KC KD"): 1.0, cards("QC QD"): 1e-200}
    second = {cards("KC QC"): 1.0, cards("KD JD"): 1e-200}
    result = estimate_equity(hero, board, opponents=2, ranges=[first, second])
    assert result.exact and result.trials == 1 and result.equity == 1.0


def test_huge_requested_exact_limit_cannot_bypass_hard_cap():
    # Uniform three-opponent river ranges yield 990^3 candidate deals.
    # Even a very large caller override must use bounded MC instead.
    result = estimate_equity(
        cards("2C 3D"), cards("AS KS QS JS TS"), opponents=3,
        samples=4, seed=5, exact_limit=1_000_000_000
    )
    assert not result.exact and result.trials == 4
    assert result.equity == pytest.approx(0.25)
