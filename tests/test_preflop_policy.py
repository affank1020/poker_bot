"""Preflop policy regression tests (not included in bot submission)."""

import pytest

from card_utils import Card
from game_state import GameState, GameStateView
from model import Model
from player_action import PlayerAction
from preflop_policy import (
    HU_OPEN, HU_THREE_BET, expand_range, hand_class,
)


def _cards(text):
    return [Card.parse(token) for token in text.split()]


def _decision(hole, state, previous_actions=()):
    me = state.to_act
    bot = Model()
    bot.seed(_cards(hole), me)
    action = bot.run(list(previous_actions), GameStateView(state))
    state.validate_action(action)
    return action


def test_range_parser_and_hand_classes():
    assert len(expand_range("22+")) == 13
    assert len(expand_range("A5s+")) == 9
    assert expand_range("KQo") == {"KQo"}
    assert "AA" in expand_range("TT+")
    assert "AKs" in HU_OPEN
    assert "AKo" in HU_THREE_BET
    assert hand_class(_cards("AS KH")) == "AKo"
    assert hand_class(_cards("3H 2H")) == "32s"
    assert hand_class(_cards("7D 7C")) == "77"
    with pytest.raises(ValueError):
        expand_range("AAs+")


@pytest.mark.parametrize(
    ("hole", "kind", "amount"),
    [
        ("AH 3H", "raise", 46),  # A3s opens
        ("9H 5H", "raise", 46),  # suited connected-ish
        ("7H 2C", "fold", 0),   # weakest offsuit junk folds
        ("5H 4C", "call", 0),   # speculative hand completes
    ],
)
def test_heads_up_small_blind_unopened(hole, kind, amount):
    state = GameState([500, 500])
    action = _decision(hole, state)
    assert (action.kind, action.amount) == (kind, amount)


def test_big_blind_can_isolate_limper():
    state = GameState([500, 500])
    state.process_action(PlayerAction.call(0))  # SB completes
    action = _decision("AS TD", state)
    assert (action.kind, action.amount) == ("raise", 64)


@pytest.mark.parametrize(
    ("hole", "kind"),
    [
        ("AH KH", "raise"),  # value three-bet
        ("7H 6H", "call"),   # defend playable suited connector
        ("7H 2C", "fold"),  # fold a weak offsuit hand
    ],
)
def test_big_blind_faces_open(hole, kind):
    state = GameState([500, 500])
    state.process_action(PlayerAction.raise_to(0, 46))
    action = _decision(hole, state)
    assert action.kind == kind


def test_short_stack_open_shove():
    state = GameState([140, 140])
    action = _decision("8H 8D", state)
    assert (action.kind, action.amount) == ("raise", 140)


def test_multiway_uses_tighter_range():
    state = GameState([500, 500, 500])
    action = _decision("7H 2C", state)
    assert action.kind == "fold"
    state = GameState([500, 500, 500])
    action = _decision("AH AD", state)
    assert action.kind == "raise"
    assert action.amount == 60


def test_six_big_blind_first_open_is_not_mistaken_for_three_bet():
    """AJs should value-reraise the first opener, even when it is large."""
    state = GameState([500, 500])
    opener = PlayerAction.raise_to(0, 120)
    state.process_action(opener)
    action = _decision("AH JH", state, [opener])
    assert (action.kind, action.amount) == ("raise", 336)


def test_small_real_three_bet_uses_three_bet_defence_range():
    """A 4-BB three-bet is still a three-bet, even below 5.5 BB."""
    state = GameState([500, 500])
    opener = PlayerAction.raise_to(0, 40)
    three_bet = PlayerAction.raise_to(1, 80)
    state.process_action(opener)
    state.process_action(three_bet)
    action = _decision("AH JH", state, [opener, three_bet])
    assert action.kind == "fold"


@pytest.mark.parametrize("three_bet_total", [180, 200])
def test_premium_four_bet_shove_precedes_large_bet_guard(three_bet_total):
    """AA must jam over either a 9-BB or 10-BB actual three-bet."""
    state = GameState([500, 500])
    opener = PlayerAction.raise_to(0, 46)
    three_bet = PlayerAction.raise_to(1, three_bet_total)
    state.process_action(opener)
    state.process_action(three_bet)
    action = _decision("AH AD", state, [opener, three_bet])
    assert (action.kind, action.amount) == ("raise", 500)


def test_raise_history_carries_across_our_turns_and_resets_on_seed():
    bot = Model()
    state = GameState([500, 500])
    bot.seed(_cards("AH AD"), 0)

    first_action = bot.run([], GameStateView(state))
    assert first_action.kind == "raise"
    state.process_action(first_action)

    opponent_three_bet = PlayerAction.raise_to(1, 200)
    state.process_action(opponent_three_bet)

    # The engine replays our previous action as well as the opponent's.
    second_action = bot.run(
        [first_action, opponent_three_bet], GameStateView(state)
    )
    state.validate_action(second_action)
    assert (second_action.kind, second_action.amount) == ("raise", 500)
    assert bot.preflop_raises_seen == 2

    bot.seed(_cards("KH KD"), 0)
    assert bot.preflop_raises_seen == 0


@pytest.mark.parametrize("hole", ["AH AD", "9H 9D"])
def test_big_blind_calls_six_bb_short_stack_shove(hole):
    """Regression: no folding AA/99 to a 6-BB SB shove at 25-BB BB stack."""
    state = GameState([120, 500])
    shove = PlayerAction.raise_to(0, 120)
    state.process_action(shove)

    assert state.to_act == 1
    assert state.call_amount == 100
    assert state.current_bet == 120
    assert state.status(0) == "all_in"
    assert state.can_raise is False

    bot = Model()
    bot.seed(_cards(hole), 1)
    response = bot.run([shove], GameStateView(state))
    state.validate_action(response)
    assert response.kind == "call"

    # The response is legal and actually advances the hand to showdown.
    state.process_action(response)
    assert state.is_street_closed
    assert state.stacks[1] == 380


def test_below_large_bet_threshold_all_in_still_uses_equity():
    """We shouldn't blindly call every short shove with offsuit junk."""
    state = GameState([120, 500])
    shove = PlayerAction.raise_to(0, 120)
    state.process_action(shove)
    action = _decision("7H 2C", state, [shove])
    assert action.kind == "fold"
