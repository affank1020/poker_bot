"""V3 experiment: confidence-gated, partially uniform HU shove ranges."""
import pickle

import pytest

from all_in_equity import expected_call_payout
from card_utils import Card
from game_state import GameState, GameStateView
from model import Model
from player_action import PlayerAction
from preflop_policy import HU_THREE_BET, hand_class


def cards(text):
    return [Card.parse(c) for c in text.split()]


def trained_bot(hole="AH JD", hands=40, raises=4):
    bot = Model()
    for i in range(hands):
        bot.opponent_model.new_hand()
        action = (PlayerAction.raise_to(0, 60) if i < raises
                  else PlayerAction.fold(0))
        bot.opponent_model.end_hand(
            [action], GameStateView(GameState([500, 500])), 1,
        )
    bot.seed(cards(hole), 1)
    return bot


def shove_state():
    state = GameState([500, 500])
    shove = PlayerAction.raise_to(0, 500)
    state.process_action(shove)
    return state, shove


@pytest.mark.parametrize("hands,raises", [(0, 0), (23, 3), (40, 2), (40, 25)])
def test_sparse_or_wide_profiles_keep_uniform_prior(hands, raises):
    bot = trained_bot(hands=hands, raises=raises)
    state, shove = shove_state()
    bot.opponent_model.observe([shove], GameStateView(state), 1)
    assert bot._tight_shove_range(GameStateView(state)) is None


def test_observed_tight_prior_is_half_uniform_and_blocks_hero():
    bot = trained_bot()
    state, shove = shove_state()
    assert bot._tight_shove_range(GameStateView(state)) is None
    bot.opponent_model.observe([shove], GameStateView(state), 1)
    weights = bot._tight_shove_range(GameStateView(state))
    assert len(weights) == 1225
    assert all(not set(pair).intersection(bot.hand) for pair in weights)
    assert sum(weights.values()) == pytest.approx(1225)
    assert min(weights.values()) == 0.5
    strong = [w for pair, w in weights.items() if hand_class(pair) in HU_THREE_BET]
    # Added value-range component supplies exactly half the total mass.
    assert sum(w - 0.5 for w in strong) == pytest.approx(612.5)


def test_same_shove_and_hand_tightens_only_with_supported_history():
    state, shove = shove_state()
    unknown = trained_bot(hands=0, raises=0)
    tight = trained_bot()
    wide = trained_bot(hands=40, raises=25)
    for bot, expected in [(unknown, "call"), (tight, "fold"), (wide, "call")]:
        action = bot.run([shove], GameStateView(state))
        state.validate_action(action)
        assert action.kind == expected


def test_premiums_still_call_and_learned_state_survives_pickle():
    bot = pickle.loads(pickle.dumps(trained_bot("AS AD"), protocol=4))
    state, shove = shove_state()
    assert bot.run([shove], GameStateView(state)).kind == "call"
    assert bot.opponent_model.profile("seat 0")["preflop_opportunities"] == 40


def test_multiway_prior_is_never_conditioned():
    bot = trained_bot()
    state = GameState([500, 500, 500])
    assert bot._tight_shove_range(GameStateView(state)) is None


def test_weighted_payout_obeys_card_removal_and_uncalled_excess():
    args = dict(
        hand=cards("AS AD"), board=cards("2H 3D 4C 8S 9H"),
        contributed=[120, 20], folded=[False, False], button=0,
        hero_id=1, cost=50, samples=20, seed=7,
        opponent_range={tuple(cards("KH KD")): 1, tuple(cards("AS AH")): 100},
    )
    # Blocked AA is removed. Hero receives 140, not villain's excess 50.
    assert expected_call_payout(**args) == 140
    assert expected_call_payout(**args) == expected_call_payout(**args)


def test_weighted_payout_rejects_multiway_conditioning():
    with pytest.raises(ValueError, match="one opponent"):
        expected_call_payout(
            cards("AS AD"), [], [100, 20, 100], [False]*3, 0, 1, 80,
            opponent_range={tuple(cards("KH KD")): 1},
        )
