"""Side-pot Monte Carlo must agree with organiser's payout accounting."""

from all_in_equity import expected_call_payout, hero_showdown_payout
from card_utils import Card
from engine import payouts
from game_state import GameState, GameStateView
from model import Model
from player_action import PlayerAction


def cards(text):
    return [Card.parse(value) for value in text.split()]


def test_exact_side_pot_award_even_when_hero_loses_main_pot():
    """Short AA wins main; KQ beats deep JJ for 960-chip side pot."""
    contributions = [20, 500, 500]
    folded = [False, False, False]
    hands = [cards("AS AD"), cards("JH JD"), cards("KH QS")]
    board = cards("KC 7D 6C 2S 3H")
    expected = payouts(contributions, folded, hands, board, button=0)
    assert expected[2] == 960
    assert hero_showdown_payout(
        contributions, folded, hands, board, 0, 2
    ) == expected[2]


def test_side_pot_split_matches_engine_with_odd_chip_rules():
    """Hero ties deep opponent for side pot while short AA wins main."""
    contributions = [21, 500, 500]
    folded = [False, False, False]
    hands = [cards("AS AD"), cards("KD QD"), cards("KS QS")]
    board = cards("KC 7D 6C 2S 3H")
    expected = payouts(contributions, folded, hands, board, button=0)
    assert hero_showdown_payout(
        contributions, folded, hands, board, 0, 2
    ) == expected[2]


def test_folded_dead_money_included_in_every_eligible_tier():
    contributions = [20, 500, 500, 40]
    folded = [False, False, False, True]
    hands = [
        cards("AS AD"), cards("JH JD"), cards("KH QS"), cards("5C 5D")
    ]
    board = cards("KC 7D 6C 2S 3H")
    expected = payouts(contributions, folded, hands, board, button=0)
    assert expected[2] == 980
    assert hero_showdown_payout(
        contributions, folded, hands, board, 0, 2
    ) == expected[2]


def test_profitable_side_pot_call_when_main_pot_equity_is_low():
    """3-seat [20,500,500] example from reviewer: KQo calls for side pot."""
    state = GameState([20, 500, 500])
    short_call = PlayerAction.call(0)
    state.process_action(short_call)
    large_shove = PlayerAction.raise_to(1, 500)
    state.process_action(large_shove)

    assert state.to_act == 2
    assert state.call_amount == 480
    assert state.pot == 540
    assert state.can_raise is False
    assert state.status(0) == state.status(1) == "all_in"

    hero_hand = cards("KH QS")
    estimated_payout = expected_call_payout(
        hero_hand, [], state.bets, state.folded, state.button,
        hero_id=2, cost=480, samples=1000, seed=20261107,
    )
    assert estimated_payout > 500  # far above the 480-chip call cost

    bot = Model()
    bot.seed(hero_hand, 2)
    decision = bot.run([short_call, large_shove], GameStateView(state))
    state.validate_action(decision)
    assert decision.kind == "call"


def test_two_seat_short_shove_still_calls_premiums():
    """Side-pot estimator also works when the only pot is the main pot."""
    state = GameState([120, 500])
    shove = PlayerAction.raise_to(0, 120)
    state.process_action(shove)
    for hand in ["AH AD", "9H 9D"]:
        bot = Model()
        bot.seed(cards(hand), 1)
        action = bot.run([shove], GameStateView(state))
        assert action.kind == "call"
        state.validate_action(action)
