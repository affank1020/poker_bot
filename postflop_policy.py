"""Conservative, equity-driven flop/turn/river betting policy.

Pure integration boundary: no persistent opponent state and no edits to the
organiser files. Caller passes concrete two-Card weight maps supplied by A1,
or None for an uninformed opponent. See docs/EQUITY_ENGINE_CONTRACT.md.

`decide_postflop(hero, player_id, view, *, ranges=None, seed=0)` returns a
legal PlayerAction. The engine equity is showdown pot share, not betting EV;
call thresholds account for the chips owed and an uncertainty premium.
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


def decide_postflop(
    hero: Sequence[Card],
    player_id: int,
    view,
    *,
    ranges: Sequence[ComboWeights | None] | None = None,
    seed: int = 0,
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
    try:
        result = estimate_equity(
            hero, view.board, opponents, ranges=ranges, samples=budget, seed=seed
        )
    except ValueError:
        if ranges is None:
            raise
        result = estimate_equity(
            hero, view.board, opponents, samples=budget, seed=seed
        )
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
    stack = view.stacks[player_id]
    spr = view.effective_stack(player_id) / pot
    category = evaluate(tuple(hero) + tuple(view.board))[0]

    if cost > 0:
        # `view.pot` already includes the outstanding enemy bet; calling
        # contributes only `cost` more to the final pot.
        required = cost / (pot + cost) + margin
        if cost >= max(view.big_blind * 3, int(0.8 * max(1, pot - cost))):
            required += 0.035
        if equity < required:
            return PlayerAction.fold(player_id)
        # Value raise only with convincingly strong showdown equity. The
        # minimum legal bet may be an all-in, handled by _raise_to.
        if equity >= raise_threshold and category >= 1 and view.can_raise:
            target = view.current_bet + round(pot_fraction * (pot + cost))
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
        and random.Random(seed ^ 0x5EED).random() < 0.18
    ):
        target = view.bets[player_id] + round(0.4 * pot)
        bet = _raise_to(view, player_id, target)
        if bet is not None:
            return bet
    return _safe_action(view, player_id)
