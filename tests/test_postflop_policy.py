"""Independent postflop policy tests without editing the organiser runtime."""
from dataclasses import dataclass

import pytest

from card_utils import Card
from equity_engine import EquityResult
from postflop_policy import _draw_potential, decide_postflop


def cards(text):
    return tuple(Card.parse(x) for x in text.split())


@dataclass
class View:
    street: str
    board: tuple[Card, ...]
    pot: int = 100
    call_amount: int = 0
    big_blind: int = 20
    button: int = 0
    current_bet: int = 0
    can_raise: bool = True
    raise_range: tuple[int, int] | None = (20, 150)
    players_in_hand: tuple[int, ...] = (0, 1)
    bets: tuple[int, ...] = (0, 0)
    stacks: tuple[int, ...] = (400, 400)

    def effective_stack(self, player_id):
        return min(self.stacks[player_id], *(
            self.stacks[p] for p in self.players_in_hand if p != player_id
        ))

    def validate(self, action):
        assert action.player_id in self.players_in_hand
        if action.kind == "raise":
            assert self.can_raise and self.raise_range is not None
            assert self.raise_range[0] <= action.amount <= self.raise_range[1]
        else:
            assert action.kind in ("fold", "call")
        return action


def test_strong_river_value_bets_and_raise_total_is_legal():
    view = View(street="river", board=cards("AH AC 2D 3H 4S"))
    action = decide_postflop(cards("AS AD"), 0, view)
    assert view.validate(action).kind == "raise"
    assert action.amount == 60  # total street bet, not chips added to pot


def test_river_board_chop_checks_and_folds_when_price_is_bad():
    hero, board = cards("2C 3D"), cards("AS KS QS JS TS")
    view = View(street="river", board=board)
    assert decide_postflop(hero, 0, view).kind == "call"
    view.call_amount, view.current_bet, view.raise_range = 100, 100, (200, 300)
    assert decide_postflop(hero, 0, view).kind == "fold"


def test_value_raising_respects_min_and_max_raise_to():
    view = View(
        street="turn", board=cards("AH AC 2D 3H"), call_amount=20,
        current_bet=20, raise_range=(80, 90)
    )
    action = decide_postflop(cards("AS AD"), 0, view)
    assert view.validate(action).kind == "raise"
    assert action.amount == 86


def test_free_check_when_raise_is_not_allowed():
    view = View(
        street="flop", board=cards("2D 7H JC"),
        can_raise=False, raise_range=None
    )
    action = decide_postflop(cards("AS KS"), 0, view)
    assert view.validate(action).kind == "call"


def test_impossible_range_falls_back_to_uniform():
    view = View(street="flop", board=cards("2D 7H JC"))
    hero = cards("AS KS")
    invalid_range = {cards("AS AD"): 1.0}  # blocked by hero's ace
    assert decide_postflop(hero, 0, view, ranges=[invalid_range], seed=17) == (
        decide_postflop(hero, 0, view, seed=17)
    )


def test_flush_draw_requires_hero_card():
    hero = cards("AS KS")
    assert _draw_potential(hero, cards("2S 7S JC"))
    assert not _draw_potential(hero, cards("2H 7D JC"))
    assert not _draw_potential(hero, cards("2S 7S JC QH 9D"))


def test_multiway_actions_are_legal_and_restrained():
    view = View(
        street="turn", board=cards("2S 7S JC QH"),
        players_in_hand=(0, 1, 2), bets=(0, 0, 0),
        stacks=(400, 400, 400)
    )
    for seed in range(10):
        action = decide_postflop(cards("AS KS"), 0, view, seed=seed)
        assert view.validate(action).kind in ("call", "raise")


def test_call_thresholds_use_correct_pot_odds(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **k: EquityResult(0.41, 0.41, 0, 10, False)
    )
    view = View(
        street="flop", board=cards("2S 7D JC"), pot=120,
        call_amount=40, current_bet=40,
        can_raise=False, raise_range=None
    )
    assert decide_postflop(cards("AS KS"), 0, view).kind == "call"
    view.street, view.board = "river", cards("2S 7D JC QH 6C")
    assert decide_postflop(cards("AS KS"), 0, view).kind == "call"
    view.call_amount = 100
    assert decide_postflop(cards("AS KS"), 0, view).kind == "fold"


def test_aggression_gate_makes_marginal_call_more_cautious(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **k: EquityResult(0.31, 0.31, 0, 10, False)
    )
    view = View(
        street="flop", board=cards("2S 7D JC"), pot=120,
        call_amount=40, current_bet=40,
        can_raise=False, raise_range=None
    )
    assert decide_postflop(cards("AS KS"), 0, view).kind == "call"
    assert decide_postflop(
        cards("AS KS"), 0, view, facing_aggression=True
    ).kind == "fold"


def test_preflop_rejected():
    view = View(street="preflop", board=())
    with pytest.raises(ValueError):
        decide_postflop(cards("AS KS"), 0, view)


def test_deterministic_seed_and_sample_budget():
    view = View(street="flop", board=cards("2D 7H JC"))
    first = decide_postflop(cards("AS KS"), 0, view, seed=115, samples=40)
    assert first == decide_postflop(
        cards("AS KS"), 0, view, seed=115, samples=40
    )
    assert view.validate(first).kind in ("fold", "call", "raise")
