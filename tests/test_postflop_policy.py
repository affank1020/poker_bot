"""Independent postflop policy tests without editing the organiser runtime."""
from dataclasses import dataclass

import pytest

from card_utils import Card
from equity_engine import EquityResult
from postflop_policy import _contestable_pot, _draw_potential, decide_postflop


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
        lambda *a, **k: EquityResult(0.41, 0.41, 0, 120, False)
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
        lambda *a, **k: EquityResult(0.31, 0.31, 0, 120, False)
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


def test_short_stack_uncallable_overbet_is_not_counted_in_pot_odds(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(0.07, 0.07, 0.0, 120, False),
    )
    # 40 from preflop, 500 opponent flop bet, hero has 5 remaining.
    view = View(
        street="flop", board=cards("2S 7D JC"), pot=540,
        call_amount=5, current_bet=500, can_raise=False,
        raise_range=None, bets=(0, 500), stacks=(5, 480),
    )
    assert _contestable_pot(view, 0, 5) == 45
    # Only 50 chips are in the pot the hero could win by calling.
    assert 5 / (_contestable_pot(view, 0, 5) + 5) == 0.1
    assert decide_postflop(cards("AS KS"), 0, view, seed=1).kind == "fold"


def test_multiway_uncallable_side_pot_bets_removed_from_pot_odds(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(0.04, 0.04, 0.0, 65, False),
    )
    # 60 previous pot; two opponents bet/call 300; hero has 5 left.
    view = View(
        street="turn", board=cards("2S 7D JC QH"), pot=660,
        call_amount=5, current_bet=300, can_raise=False,
        raise_range=None, players_in_hand=(0, 1, 2),
        bets=(0, 300, 300), stacks=(5, 500, 500),
    )
    assert _contestable_pot(view, 0, 5) == 70
    assert decide_postflop(cards("AS KS"), 0, view, seed=1).kind == "fold"


def test_low_trial_conditioned_equity_falls_back_to_uniform(monkeypatch):
    seen = []

    def fake_equity(*args, **kw):
        seen.append(kw.get("ranges"))
        if kw.get("ranges") is not None:
            return EquityResult(1.0, 1.0, 0.0, 1, False)
        return EquityResult(0.20, 0.20, 0.0, 65, False)

    monkeypatch.setattr("postflop_policy.estimate_equity", fake_equity)
    view = View(
        street="turn", board=cards("2S 7D JC QH"),
        players_in_hand=(0, 1, 2), bets=(0, 0, 0),
        stacks=(400, 400, 400),
    )
    narrow = {cards("KC KD"): 1.0}
    action = decide_postflop(
        cards("AS KS"), 0, view, ranges=[narrow, narrow], seed=7,
    )
    assert seen == [[narrow, narrow], None]
    assert view.validate(action).kind == "call"  # Check, not a fake-equity bet.


def test_no_reliable_trial_estimate_falls_back_to_safe_action(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(1.0, 1.0, 0.0, 1, False),
    )
    view = View(street="turn", board=cards("2S 7D JC QH"))
    assert decide_postflop(cards("AS KS"), 0, view, seed=7).kind == "call"
    view.call_amount = 40
    assert decide_postflop(cards("AS KS"), 0, view, seed=7).kind == "fold"


def test_default_seed_changes_semibluff_choices_but_explicit_seed_is_stable(monkeypatch):
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(0.45, 0.45, 0.0, 120, False),
    )
    view = View(street="flop", board=cards("2S 7S JC"))
    hero = cards("AS KS")
    assert decide_postflop(hero, 0, view, seed=2) == decide_postflop(
        hero, 0, view, seed=2
    )
    sampled_seeds = iter(range(200))
    monkeypatch.setattr(
        "postflop_policy.random.getrandbits", lambda _: next(sampled_seeds)
    )
    kinds = [decide_postflop(hero, 0, view).kind for _ in range(200)]
    assert 15 <= kinds.count("raise") <= 60
    assert kinds.count("call") + kinds.count("raise") == 200


def test_actual_game_state_allin_short_call_regression(monkeypatch):
    from game_state import GameState, GameStateView
    from player_action import PlayerAction

    state = GameState(stacks=[25, 1000])
    state.process_action(PlayerAction.call(0))
    state.process_action(PlayerAction.call(1))  # BB check
    for card in cards("2S 7D JC"):
        state.deal_card(card)
    assert state.to_act == 1 and state.street == "flop"
    state.process_action(PlayerAction.raise_to(1, 500))
    view = GameStateView(state)
    assert view.to_act == 0 and view.call_amount == 5
    assert view.pot == 540 and tuple(view.bets) == (0, 500)
    assert _contestable_pot(view, 0, view.call_amount) == 45
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(0.07, 0.07, 0.0, 120, False),
    )
    action = decide_postflop(cards("AS KS"), 0, view, seed=10)
    assert action.kind == "fold"
    state.validate_action(action)


def test_actual_game_state_multiway_side_pot_regression(monkeypatch):
    from game_state import GameState, GameStateView
    from player_action import PlayerAction

    state = GameState(stacks=[25, 1000, 1000])
    assert state.to_act == 0
    state.process_action(PlayerAction.call(0))
    state.process_action(PlayerAction.call(1))
    state.process_action(PlayerAction.call(2))
    assert state.is_street_closed
    for card in cards("2S 7D JC"):
        state.deal_card(card)
    assert state.to_act == 1
    state.process_action(PlayerAction.raise_to(1, 300))
    state.process_action(PlayerAction.call(2))
    view = GameStateView(state)
    assert view.to_act == 0 and view.call_amount == 5
    assert view.pot == 660 and tuple(view.bets) == (0, 300, 300)
    assert _contestable_pot(view, 0, view.call_amount) == 70
    monkeypatch.setattr(
        "postflop_policy.estimate_equity",
        lambda *a, **kw: EquityResult(0.04, 0.04, 0.0, 65, False),
    )
    action = decide_postflop(cards("AS KS"), 0, view, seed=10)
    assert action.kind == "fold"
    state.validate_action(action)


def test_river_allin_side_pot_hero_wins_deep_player():
    """Reviewer regression: hero loses main to A, wins side pot vs B."""
    from sidepot_equity import estimate_call_payout

    board = cards("2H 3D 4C 5S 9H")
    hero = cards("AH 7C")  # 5-high straight.
    a = {cards("6H 8S"): 1}  # All-in 6-high straight.
    b = {cards("KC QD"): 1}  # Deep player has high card.
    view = View(
        street="river", board=board, pot=370, call_amount=100,
        current_bet=100, can_raise=False, raise_range=None,
        players_in_hand=(0, 1, 2), bets=(0, 0, 100),
        stacks=(200, 0, 200),
    )
    ledger = (120, 30, 220)
    result = estimate_call_payout(
        hero, board, 0, contributions=ledger,
        folded=(False, False, False), opponents=(1, 2),
        call_cost=100, button=0, ranges=[a, b],
    )
    assert result.exact and result.trials == 1
    assert result.eligible_pot == 470
    assert result.expected_chips == 380  # 90 main to A; 380 side to hero.
    action = decide_postflop(
        hero, 0, view, ranges=[a, b], contributions=ledger, seed=31
    )
    assert view.validate(action).kind == "call"


def test_river_allin_side_pot_hero_loses_both_tiers():
    from sidepot_equity import estimate_call_payout

    board = cards("2H 3D 4C 5S 9H")
    hero = cards("AH 7C")
    a = {cards("6H 8S"): 1}
    b = {cards("6C 8D"): 1}
    view = View(
        street="river", board=board, pot=370, call_amount=100,
        current_bet=100, can_raise=False, raise_range=None,
        players_in_hand=(0, 1, 2), bets=(0, 0, 100),
        stacks=(200, 0, 200),
    )
    ledger = (120, 30, 220)
    result = estimate_call_payout(
        hero, board, 0, contributions=ledger,
        folded=(False, False, False), opponents=(1, 2),
        call_cost=100, button=0, ranges=[a, b],
    )
    assert result.expected_chips == 0
    assert decide_postflop(
        hero, 0, view, ranges=[a, b], contributions=ledger, seed=31
    ).kind == "fold"


def test_side_pot_payout_matches_organiser_engine():
    engine = pytest.importorskip("engine")
    from sidepot_equity import estimate_call_payout
    board = cards("2H 3D 4C 5S 9H")
    hero = cards("AH 7C")
    a = cards("6H 8S")
    b = cards("KC QD")
    folded_cards = cards("JD TD")
    prior = (120, 30, 220, 40)  # Fourth seat folded but paid 40.
    after_call = [220, 30, 220, 40]
    result = estimate_call_payout(
        hero, board, 0, contributions=prior,
        folded=(False, False, False, True), opponents=(1, 2),
        call_cost=100, button=0, ranges=[{a: 1}, {b: 1}]
    )
    organiser = engine.payouts(
        after_call, [False, False, False, True],
        [list(hero), list(a), list(b), list(folded_cards)],
        list(board), button=0,
    )
    assert result.exact
    assert result.expected_chips == organiser[0] == 390


def test_side_pot_uniform_mc_is_bounded_and_seed_reproducible():
    from sidepot_equity import estimate_call_payout
    args = dict(
        hero=cards("AH 7C"), board=cards("2H 3D 4C 5S"),
        hero_id=0, contributions=(120, 30, 220),
        folded=(False, False, False), opponents=(1, 2),
        call_cost=100, button=0, samples=20, seed=5
    )
    x = estimate_call_payout(**args)
    assert x == estimate_call_payout(**args)
    assert not x.exact and x.trials == 20
    assert 0 <= x.expected_chips <= x.eligible_pot


def test_bad_sidepot_ledger_folds_safely_without_payout_estimation():
    board = cards("2H 3D 4C 5S 9H")
    view = View(
        street="river", board=board, pot=370, call_amount=100,
        current_bet=100, can_raise=False, raise_range=None,
        players_in_hand=(0, 1, 2), bets=(0, 0, 100),
        stacks=(200, 0, 200)
    )
    assert decide_postflop(
        cards("AH 7C"), 0, view, contributions=(120, 30, 100), seed=2
    ).kind == "fold"


def test_public_contribution_ledger_recovers_previous_street_all_in():
    from sidepot_equity import ContributionLedger

    tracker = ContributionLedger()
    late = View(
        street="river", board=cards("2H 3D 4C 5S 9H"),
        pot=370, bets=(0, 0, 100), stacks=(380, 0, 280),
        players_in_hand=(0, 1, 2),
    )
    assert tracker.observe(late) is None  # Refuse to invent initial stacks.
    opening = View(
        street="preflop", board=(), pot=50, bets=(10, 20, 20),
        stacks=(490, 10, 480), players_in_hand=(0, 1, 2),
    )
    assert tracker.observe(opening) == (10, 20, 20)
    assert tracker.observe(late) == (120, 30, 220)
    tracker.reset()
    assert tracker.observe(late) is None
