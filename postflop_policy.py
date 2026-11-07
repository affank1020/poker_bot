"""Conservative, equity-driven flop/turn/river betting policy.

Pure integration boundary: no persistent opponent state and no edits to the
organiser files. Caller passes concrete two-Card weight maps supplied by A1,
or None for an uninformed opponent. See docs/EQUITY_ENGINE_CONTRACT.md.

`decide_postflop(hero, player_id, view, *, ranges=None, seed=None)` returns a
legal PlayerAction. The engine equity is showdown pot share, not betting EV;
call thresholds account for the contestable chips and uncertainty premium.
Pass a distinct seed per Model decision for reproducible live action variation;
omitting it uses a new module-RNG seed for each invocation.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import random

from card_utils import Card, evaluate
from equity_engine import ComboWeights, estimate_equity
from player_action import PlayerAction

STREET_SETTINGS = {
    # street: value threshold, raise-for-value threshold, call margin, bet pot fraction
    "flop": (0.68, 0.85, 0.045, 0.48),
    "turn": (0.73, 0.87, 0.060, 0.55),
    "river": (0.78, 0.90, 0.075, 0.60),
}


def _draw_potential(hero: Sequence[Card], board: Sequence[Card]) -> bool:
    """Four-to-flush or four-to-straight involving a hero card."""
    if len(board) >= 5:
        return False
    combined = tuple(hero) + tuple(board)
    suits = Counter(card.suit for card in combined)
    if any(n >= 4 and any(card.suit == suit for card in hero)
           for suit, n in suits.items()):
        return True
    values = {card.value for card in combined}
    if 14 in values:
        values.add(1)
    own = {card.value for card in hero}
    if 14 in own:
        own.add(1)
    return any(
        len(values.intersection(range(start, start + 5))) >= 4
        and bool(own.intersection(range(start, start + 5)))
        for start in range(1, 11)
    )


def _raise_to(view, player_id: int, target_total: int) -> PlayerAction | None:
    """Clamp a raise-to street total; return None when raises are unavailable."""
    if not view.can_raise or view.raise_range is None:
        return None
    low, high = view.raise_range
    if low > high:
        return None
    return PlayerAction.raise_to(player_id, min(high, max(low, int(target_total))))


def _safe_action(view, player_id: int) -> PlayerAction:
    if view.call_amount == 0:
        return PlayerAction.call(player_id)  # Check.
    return PlayerAction.fold(player_id)


def _contestable_pot(view, player_id: int, cost: int) -> int:
    """Public pot minus current-street chips hero cannot match.

    At this decision hero's final street contribution on a call is
    bets[hero] + cost. Any opponent/folded seat chips beyond that cap
    belong to side pots hero cannot win. Prior streets' contributions
    already were contestable while hero remained active, and are retained.
    This does not require inaccessible historical per-seat contributions.
    """
    cap = view.bets[player_id] + cost
    excess = sum(
        max(0, amount - cap)
        for seat, amount in enumerate(view.bets) if seat != player_id
    )
    return max(0, view.pot - excess)


def decide_postflop(
    hero: Sequence[Card],
    player_id: int,
    view,
    *,
    ranges: Sequence[ComboWeights | None] | None = None,
    seed: int | None = None,
    samples: int | None = None,
    facing_aggression: bool = False,
) -> PlayerAction:
    """Make a legal, bounded postflop betting decision.

    `facing_aggression` means a credible same-street opponent raise was
    observed in the public action stream. It is not an inference of hidden
    cards or an opponent personality label. With no evidence it is False.

    If conditioning on opponent ranges yields an impossible/collision-heavy
    joint model, explicitly fall back to the broad prior rather than risking
    an illegal or missing action during a tournament call.
    """
    street = view.street
    if street not in STREET_SETTINGS or len(view.board) not in (3, 4, 5):
        raise ValueError("decide_postflop requires a flop, turn or river view")
    opponents = max(1, len(view.players_in_hand) - 1)
    budget = samples if samples is not None else (120 if opponents == 1 else 65)
    # Do not accidentally use the same random draw on every unseeded hand.
    # Integrators should instead pass a changing per-decision seed generated
    # by Model, so a replay remains deterministic.
    decision_seed = random.getrandbits(64) if seed is None else seed

    def sample_equity(assumed_ranges):
        return estimate_equity(
            hero, view.board, opponents, ranges=assumed_ranges,
            samples=budget, seed=decision_seed,
        )

    try:
        result = sample_equity(ranges)
    except ValueError:
        if ranges is None:
            raise
        result = sample_equity(None)
    # Weighted Monte Carlo can return a tiny number of accepted trials
    # when narrow multiway ranges collide. Never bet from a 1-trial estimate.
    # Exact enumeration remains reliable even with a single legal outcome.
    minimum_trials = max(1, (budget + 1) // 2)
    if not result.exact and result.trials < minimum_trials:
        if ranges is not None:
            result = sample_equity(None)
        if not result.exact and result.trials < minimum_trials:
            return _safe_action(view, player_id)
    equity = result.equity
    value_threshold, raise_threshold, margin, pot_fraction = STREET_SETTINGS[street]
    multiway = opponents > 1
    if multiway:
        value_threshold += 0.08
        raise_threshold += 0.06
        margin += 0.05
    in_position = view.button == player_id
    if in_position and not multiway:
        value_threshold -= 0.02
    if facing_aggression:
        margin += 0.035
        raise_threshold += 0.035
    pot = max(1, view.pot)
    cost = view.call_amount
    contestable_pot = _contestable_pot(view, player_id, cost)
    stack = view.stacks[player_id]
    spr = view.effective_stack(player_id) / pot
    category = evaluate(tuple(hero) + tuple(view.board))[0]

    if cost > 0:
        # Subtract opponent contributions above the hero's affordable
        # street total: they are side-pot chips, not contestable winnings.
        required = cost / (contestable_pot + cost) + margin
        if cost >= max(view.big_blind * 3, int(0.8 * max(1, contestable_pot - cost))):
            required += 0.035
        if equity < required:
            return PlayerAction.fold(player_id)
        # Value raise only with convincingly strong showdown equity. The
        # minimum legal bet may be an all-in, handled by _raise_to.
        if equity >= raise_threshold and category >= 1 and view.can_raise:
            target = view.current_bet + round(pot_fraction * (contestable_pot + cost))
            value_raise = _raise_to(view, player_id, target)
            if value_raise is not None:
                return value_raise
        return PlayerAction.call(player_id)

    if not view.can_raise:
        return PlayerAction.call(player_id)
    # Against multiple opponents require much stronger showdown equity.
    if equity >= value_threshold and (category >= 1 or equity >= 0.92):
        # Avoid huge overbetting when the effective stack is shallow.
        fraction = pot_fraction if spr >= 1.0 else min(pot_fraction, 0.4)
        target = view.bets[player_id] + round(fraction * pot)
        bet = _raise_to(view, player_id, target)
        if bet is not None:
            return bet

    # Limited semi-bluffs with real draw potential, heads-up, and room to
    # manoeuvre. No speculative river or multiway bluffs in baseline V2.
    draw = _draw_potential(hero, view.board)
    if (
        not multiway and street != "river" and draw and category <= 1
        and 0.38 <= equity < value_threshold and spr >= 2.5
        and stack >= 3 * view.big_blind
        and random.Random(decision_seed ^ 0x5EED).random() < 0.18
    ):
        target = view.bets[player_id] + round(0.4 * pot)
        bet = _raise_to(view, player_id, target)
        if bet is not None:
            return bet
    return _safe_action(view, player_id)
