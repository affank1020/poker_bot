"""End-to-end wiring regressions for Model; organiser modules are untouched."""
from itertools import combinations
import pickle
from types import SimpleNamespace

import pytest

from card_utils import Card
from model import Model
from opponent_model import _blank_record
from player_action import PlayerAction


def cards(value):
    return tuple(Card.parse(card) for card in value.split())


def view(*, street="preflop", board=(), teams=("hero", "villain"),
         button=0, bets=(10, 20), stacks=(490, 480), pot=30,
         call_amount=0, current_bet=20, can_raise=True,
         raise_range=(40, 500), players_in_hand=None):
    return SimpleNamespace(
        street=street, board=tuple(board), teams=tuple(teams),
        button=button, small_blind=10, big_blind=20,
        bets=tuple(bets), stacks=tuple(stacks), pot=pot,
        call_amount=call_amount, current_bet=current_bet,
        can_raise=can_raise, raise_range=raise_range,
        players_in_hand=(tuple(range(len(teams))) if players_in_hand is None
                         else tuple(players_in_hand)),
        num_players=len(teams),
    )


@pytest.fixture
def preflop_stub(monkeypatch):
    monkeypatch.setattr(
        Model, "_preflop",
        lambda self, game_state: PlayerAction.call(self.player_id)
    )


def test_a1_is_called_once_per_delivery_and_persists_across_seat_changes(preflop_stub):
    bot = Model()
    hand = cards("AS KH")
    bot.seed(hand, 0)
    first = view(bets=(10, 60), stacks=(490, 440), pot=70, current_bet=60)
    bot.run([PlayerAction.raise_to(1, 60)], first)
    bot.run([], first)
    assert bot.opponent_model.profile("villain")["streets"]["preflop"]["actions"] == 1
    bot.end_game([
        PlayerAction.reveal_card(Card.parse("QD"), 1),
        PlayerAction.reveal_card(Card.parse("JC"), 1),
    ], first)
    profile = bot.opponent_model.profile("villain")
    assert profile["hands_seen"] == 1
    assert profile["showdown_hands"] == 1
    assert profile["pfr_hands"] == 1

    # The same opponent is now in seat 0; the bot moves to seat 1.
    bot.seed(hand, 1)
    second = view(
        teams=("villain", "hero"), bets=(60, 20),
        stacks=(440, 480), pot=80, current_bet=60,
    )
    bot.run([PlayerAction.raise_to(0, 60)], second)
    bot.end_game([], second)
    profile = bot.opponent_model.profile("villain")
    assert profile["hands_seen"] == 2
    assert profile["pfr_hands"] == 2
    assert profile["streets"]["preflop"]["actions"] == 2
    assert bot.opponent_model.profile("hero")["hands_seen"] == 0


def test_weighted_ranges_follow_live_seat_order_and_cache(preflop_stub):
    bot = Model()
    bot.seed(cards("AS KH"), 1)
    names = ("villain", "hero", "unknown")
    opening = view(
        teams=names, button=2, bets=(60, 20, 10),
        stacks=(440, 480, 490), pot=90, current_bet=60,
    )
    record = _blank_record()
    record["preflop_opportunities"] = 20
    record["vpip_hands"] = 8
    bot.opponent_model._records["villain"] = record
    bot.run([PlayerAction.raise_to(0, 60)], opening)
    ranges = bot._opponent_ranges(opening)
    assert len(ranges) == 2
    assert isinstance(ranges[0], dict)
    assert ranges[1] is None
    assert len(ranges[0]) == len(list(combinations(range(50), 2)))
    assert all(not any(card in bot.hand for card in pair) for pair in ranges[0])
    assert len(set(ranges[0].values())) > 1
    assert all(weight > 0 for weight in ranges[0].values())
    assert bot._opponent_ranges(opening)[0] is ranges[0]

    bot.seed(cards("AS KH"), 1)
    assert bot._range_cache == {}
    # Incomplete hand history must not fabricate an action-conditioned range.
    assert bot._opponent_ranges(opening) is None


def test_all_three_streets_use_new_policy_with_changing_seed_and_ledger(
    monkeypatch, preflop_stub
):
    seen = []

    def spy(hero, player_id, game_state, **kwargs):
        seen.append((game_state.street, player_id, kwargs))
        return PlayerAction.call(player_id)

    monkeypatch.setattr("model.decide_postflop", spy)
    bot = Model()
    bot.seed(cards("AS KH"), 0)
    opening = view()
    bot.run([], opening)

    flop_cards = cards("2H 7D JC")
    flop = view(
        street="flop", board=flop_cards, bets=(0, 40),
        stacks=(480, 440), pot=80, call_amount=40, current_bet=40,
        raise_range=(80, 480),
    )
    bot.run(
        [*(PlayerAction.reveal_card(card) for card in flop_cards),
         PlayerAction.raise_to(1, 40)], flop,
    )
    turn = view(
        street="turn", board=flop_cards + cards("QS"),
        bets=(0, 0), stacks=(440, 440), pot=120, current_bet=0,
    )
    bot.run([
        PlayerAction.call(0), PlayerAction.reveal_card(Card.parse("QS"))
    ], turn)
    river = view(
        street="river", board=turn.board + cards("3C"),
        bets=(0, 0), stacks=(440, 440), pot=120, current_bet=0,
    )
    bot.run([PlayerAction.reveal_card(Card.parse("3C"))], river)

    assert [entry[0] for entry in seen] == ["flop", "turn", "river"]
    assert [entry[2]["contributions"] for entry in seen] == [
        (20, 60), (60, 60), (60, 60),
    ]
    assert [entry[2]["facing_aggression"] for entry in seen] == [
        True, False, False,
    ]
    assert len({entry[2]["seed"] for entry in seen}) == 3


def test_short_allin_sidepot_commitments_are_not_guessed_from_street_bets(
    monkeypatch, preflop_stub
):
    seen = []

    def spy(hero, player_id, game_state, **kwargs):
        seen.append(kwargs["contributions"])
        return PlayerAction.call(player_id)

    monkeypatch.setattr("model.decide_postflop", spy)
    bot = Model()
    bot.seed(cards("AH 7C"), 0)
    # Three players start with stacks 1000, 30 and 1000.
    opening = view(
        teams=("hero", "short", "deep"), bets=(20, 20, 20),
        stacks=(980, 10, 980), pot=60,
    )
    bot.run([], opening)
    final_board = cards("2H 3D 4C 5S 9H")
    river = view(
        street="river", board=final_board,
        teams=("hero", "short", "deep"),
        bets=(0, 0, 100), stacks=(880, 0, 780),
        pot=370, call_amount=100, current_bet=100,
        can_raise=False, raise_range=None,
    )
    bot.run([PlayerAction.reveal_card(c) for c in final_board], river)
    assert seen == [(120, 30, 220)]
    assert bot.contribution_ledger.observe(river) == (120, 30, 220)

    # No first-street snapshot: safely pass None rather than guessing.
    new_bot = Model()
    new_bot.seed(cards("AH 7C"), 0)
    new_bot.run([PlayerAction.reveal_card(c) for c in final_board], river)
    assert seen[-1] is None


def test_raise_total_clamped_and_model_pickles_after_state_accumulates(preflop_stub):
    bot = Model()
    bot.seed(cards("AS KH"), 0)
    opening = view()
    bot.run([], opening)
    constrained = view(raise_range=(60, 85))
    assert bot._raise(constrained, 5).amount == 60
    assert bot._raise(constrained, 1000).amount == 85
    constrained.can_raise = False
    assert bot._raise(constrained, 1000).kind == "call"

    recovered = pickle.loads(pickle.dumps(bot, protocol=4))
    assert recovered.opponent_model.profile("villain")["hands_seen"] == 0
    assert recovered.contribution_ledger.observe(opening) == (10, 20)
    assert recovered.player_id == bot.player_id
