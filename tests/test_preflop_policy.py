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
