"""A1 opponent intelligence, deliberately independent of the preflop PR.

These protocol-shaped fixtures avoid coupling to any changes in model.py.
The interface exposes only the public action fields available from the arena.
"""

import pickle
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from opponent_model import OpponentModel, PRIOR_RATES, STREETS


VALUES = {rank: i + 2 for i, rank in enumerate("23456789TJQKA")}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    @property
    def value(self):
        return VALUES[self.rank]


def cards(text):
    return [Card(rank, suit) for rank, suit in text.split()]


def act(pid, kind, amount=0, card=None):
    return SimpleNamespace(player_id=pid, kind=kind, amount=amount, card=card)


def reveal(text):
    return act(-1, "reveal_card", card=cards(text)[0])


def shown(pid, text):
    return act(pid, "reveal_card", card=cards(text)[0])


def state(teams=("hero", "villain"), button=0, sb=10, bb=20):
    return SimpleNamespace(teams=teams, button=button, small_blind=sb, big_blind=bb)


def test_unknown_opponent_has_smoothed_rates_and_no_memory():
    m = OpponentModel()
    p = m.profile("unknown")
    assert p["hands_seen"] == 0
    assert p["preflop_opportunities"] == 0
    for metric in ("vpip", "pfr", "three_bet"):
        assert p["rates"][metric] == pytest.approx(PRIOR_RATES[metric])
    for street in STREETS:
        assert p["streets"][street]["rates"]["fold_to_bet"] == pytest.approx(0.42)
    assert m.preflop_range_weight("unknown", cards("7H 2D")) == 1.0
    assert m.hand_line("unknown") == {}


def test_sb_limp_is_vpip_but_not_fold_to_a_bet():
    m, s = OpponentModel(), state(button=1)
    m.new_hand()
    m.observe([act(1, "call")], s, 0)
    assert m.hand_line("villain")["preflop"] == ["call"]
    m.end_hand([act(0, "call"), reveal("2C"), reveal("7D"), reveal("9H")], s, 0)
    p = m.profile("villain")
    assert (p["hands_seen"], p["preflop_opportunities"], p["vpip_hands"], p["pfr_hands"]) == (1, 1, 1, 0)
    assert p["streets"]["preflop"]["facing_bet"] == 0
    assert p["streets"]["preflop"]["call_facing_bet"] == 0
    assert p["showdown_hands"] == 0


def test_bb_check_is_not_vpip_and_blind_fold_is_not_fold_to_bet():
    m, s = OpponentModel(), state(button=0)
    m.new_hand()
    m.observe([act(0, "call"), act(1, "call")], s, 0)
    m.end_hand([], s, 0)
    p = m.profile("villain")
    assert (p["preflop_opportunities"], p["vpip_hands"], p["pfr_hands"]) == (1, 0, 0)
    assert p["streets"]["preflop"]["checks"] == 1

    m.new_hand()
    m.end_hand([act(1, "fold")], state(button=1), 0)
    p = m.profile("villain")
    assert p["preflop_opportunities"] == 2
    assert p["streets"]["preflop"]["fold_to_bet"] == 0


def test_raising_sequence_counts_threebet_by_prior_raises_not_size():
    m, s = OpponentModel(), state(button=0)
    m.new_hand()
    # Hero open to 120 chips is a first open, not a 3-bet.
    m.observe([act(0, "raise", 120), act(1, "raise", 280)], s, 0)
    m.end_hand([act(0, "fold")], s, 0)
    p = m.profile("villain")
    assert p["preflop_opportunities"] == 1
    assert p["vpip_hands"] == 1
    assert p["pfr_hands"] == 1
    assert p["three_bet_opportunities"] == 1
    assert p["three_bets"] == 1
    assert p["streets"]["preflop"]["facing_bet"] == 1
    assert p["streets"]["preflop"]["raise_facing_bet"] == 1
    assert p["streets"]["preflop"]["large_raises"] == 1


def test_short_call_and_fold_to_open_create_correct_opportunity_denominators():
    m, s = OpponentModel(), state(button=0)
    m.new_hand()
    m.end_hand([act(0, "raise", 60), act(1, "fold")], s, 0)
    p = m.profile("villain")
    assert p["preflop_opportunities"] == 1
    assert p["vpip_hands"] == 0
    assert p["streets"]["preflop"]["facing_bet"] == 1
    assert p["streets"]["preflop"]["fold_to_bet"] == 1
    assert p["streets"]["preflop"]["checks"] == 0


def test_four_street_history_and_incremental_batches():
    m, s = OpponentModel(), state(button=0)
    m.new_hand()
    # P1 BB calls an open then checks flop, bets turn, raises river.
    m.observe([act(0, "raise", 50), act(1, "call")], s, 0, batch_id="first")
    m.observe([reveal("2H"), reveal("7D"), reveal("QS"), act(1, "call")], s, 0,
              batch_id="flop-check")
    m.observe([act(0, "raise", 70), act(1, "call"), reveal("8D")], s, 0,
              batch_id="turn")
    m.observe([act(1, "raise", 80), act(0, "call"), reveal("AS")], s, 0,
              batch_id="river")
    m.end_hand([act(1, "call"), act(0, "raise", 140), act(1, "fold")], s, 0,
               batch_id="tail")
    p = m.profile("villain")
    assert p["hands_seen"] == 1
    assert p["streets"]["preflop"]["call_facing_bet"] == 1
    assert p["streets"]["flop"]["checks"] == 1
    assert p["streets"]["flop"]["call_facing_bet"] == 1
    assert p["streets"]["turn"]["bets_unopened"] == 1
    assert p["streets"]["river"]["fold_to_bet"] == 1
    assert m.hand_line("villain") == {
        "preflop": ["call"], "flop": ["call", "call"],
        "turn": ["raise"], "river": ["call", "fold"],
    }


def test_optional_batch_id_prevents_recounting_a_replayed_incremental_batch():
    m, s = OpponentModel(), state(button=0)
    m.new_hand()
    batch = [act(0, "raise", 60), act(1, "call")]
    m.observe(batch, s, 0, batch_id=7)
    m.observe(batch, s, 0, batch_id=7)
    m.end_hand([], s, 0)
    p = m.profile("villain")
    assert p["streets"]["preflop"]["actions"] == 1
    assert p["vpip_hands"] == 1


def test_changing_seats_and_multiplayer_identity_is_name_based():
    m = OpponentModel()
    s = state(("hero", "alice", "bob"), button=0)
    m.new_hand()
    # Button 0, SB 1, BB 2. Alice calls 20, Bob checks.
    m.end_hand([act(0, "call"), act(1, "call"), act(2, "call")], s, 0)
    assert m.profile("alice")["vpip_hands"] == 1
    assert m.profile("bob")["vpip_hands"] == 0

    # Same teams in rotated seats; our seat is now 2.
    m.new_hand()
    r = state(("bob", "alice", "hero"), button=0)
    m.end_hand([act(0, "raise", 60), act(1, "fold"), act(2, "call")], r, 2)
    assert m.profile("bob")["vpip_hands"] == 1
    assert m.profile("bob")["pfr_hands"] == 1
    assert m.profile("bob")["hands_seen"] == 2
    assert m.profile("alice")["hands_seen"] == 2
    assert m.profile("hero")["hands_seen"] == 0


def test_showdown_records_only_two_real_player_reveals_not_board_cards():
    m, s = OpponentModel(), state()
    m.new_hand()
    events = [reveal("2C"), reveal("4H"), reveal("8D"), reveal("TD"),
              reveal("JS"), shown(1, "AH"), shown(1, "KH"), act(1, "win", 60)]
    m.end_hand(events, s, 0)
    p = m.profile("villain")
    assert p["showdown_hands"] == 1
    assert p["shown_hand_classes"] == {"AKs": 1}
    assert p["streets"]["river"]["actions"] == 0

    m.new_hand()
    m.end_hand([shown(1, "AS")], s, 0)
    assert m.profile("villain")["showdown_hands"] == 1


def test_profiles_are_copy_safe_and_pickle_preserves_memory():
    m, s = OpponentModel(), state()
    m.new_hand()
    m.end_hand([act(0, "raise", 50), act(1, "fold")], s, 0)
    profile = m.profile("villain")
    profile["streets"]["preflop"]["fold_to_bet"] = 99999
    profile["rates"]["vpip"] = 0.99
    assert m.profile("villain")["streets"]["preflop"]["fold_to_bet"] == 1
    restored = pickle.loads(pickle.dumps(m))
    restored.new_hand()
    restored.end_hand([act(0, "call"), act(1, "call")], s, 0)
    assert restored.profile("villain")["hands_seen"] == 2
    assert m.profile("villain")["hands_seen"] == 1


def test_unknown_action_is_neutral_while_raised_range_prior_prefers_strong_hands():
    m = OpponentModel()
    strong, weak = cards("AS AH"), cards("7H 2D")
    assert m.preflop_range_weight("unknown", strong) == 1.0
    assert m.preflop_range_weight("unknown", strong, "raise") > m.preflop_range_weight(
        "unknown", weak, "raise")
    assert 0 < m.preflop_range_weight("unknown", weak, "call")


def test_persistent_vpip_modulates_range_without_extreme_overfitting():
    tight, loose = OpponentModel(), OpponentModel()
    s = state(button=1)
    for i in range(60):
        tight.new_hand()
        tight.end_hand([act(1, "fold")], s, 0)
        loose.new_hand()
        loose.end_hand([act(1, "raise", 50), act(0, "fold")], s, 0)
    assert tight.profile("villain")["rates"]["vpip"] < 0.2
    assert loose.profile("villain")["rates"]["vpip"] > 0.8
    # Tight raises should skew even more toward strong combinations.
    strong, weak = cards("AS AH"), cards("7H 2D")
    ratio_tight = (tight.preflop_range_weight("villain", strong, "raise") /
                   tight.preflop_range_weight("villain", weak, "raise"))
    ratio_loose = (loose.preflop_range_weight("villain", strong, "raise") /
                   loose.preflop_range_weight("villain", weak, "raise"))
    assert ratio_tight > ratio_loose
    assert 0.05 <= tight.preflop_range_weight("villain", weak, "raise") < 5


def test_new_hand_flushes_abandoned_hand_and_does_not_reset_history():
    m, s = OpponentModel(), state()
    m.new_hand()
    m.observe([act(0, "raise", 60), act(1, "call")], s, 0)
    m.new_hand()  # defensive flush when caller couldn't call end_hand
    assert m.profile("villain")["hands_seen"] == 1
    m.end_hand([act(0, "raise", 60), act(1, "fold")], s, 0)
    assert m.profile("villain")["hands_seen"] == 2
    assert m.profile("villain")["vpip_hands"] == 1


def test_duplicate_end_hand_does_not_recount_and_new_hand_empties_line():
    m, s = OpponentModel(), state()
    m.new_hand()
    m.end_hand([act(0, "raise", 50), act(1, "call")], s, 0)
    assert m.profile("villain")["hands_seen"] == 1
    m.end_hand([act(0, "raise", 50), act(1, "call")], s, 0)
    assert m.profile("villain")["hands_seen"] == 1
    with pytest.raises(ValueError, match="new_hand"):
        m.observe([], s, 0)
    m.new_hand()
    assert m.hand_line("villain") == {}
    m.end_hand([act(1, "fold")], state(button=1), 0)
    assert m.profile("villain")["hands_seen"] == 2
