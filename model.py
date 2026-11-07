"""Heads-up poker bot with position-aware preflop play and sampled postflop equity.

Only this Model is submitted. No files, network access, engine internals or
non-serialisable attributes are needed at runtime.
"""

import random

from card_utils import Card, evaluate, full_deck
from game_state import GameStateView
from player_action import PlayerAction


class Model:
    def __init__(self):
        # Persistent data survives between hands in a match.
        self.hand = []
        self.player_id = None
        self.opponents = {}
        self.random_seed = 20261107

        # Hand-local observations, reset by seed().
        self.board_reveals = 0
        self.opponent_raised_street = None

    def seed(self, hand: list[Card], player_id: int):
        self.hand = list(hand)
        self.player_id = player_id
        self.board_reveals = 0
        self.opponent_raised_street = None
        self.random_seed = (self.random_seed * 1664525 + 1013904223) & 0xFFFFFFFF

    def _observe(self, actions, game_state):
        """Read only the action stream since our last turn."""
        for action in actions:
            if action.kind == "reveal_card" and action.player_id == -1:
                self.board_reveals += 1
                continue
            if action.player_id == self.player_id or action.player_id < 0:
                continue
            if action.kind not in ("fold", "call", "raise"):
                continue
            name = game_state.teams[action.player_id]
            stats = self.opponents.setdefault(
                name, {"raises": 0, "calls": 0, "folds": 0, "large_raises": 0}
            )
            if action.kind == "raise":
                stats["raises"] += 1
                if action.amount >= 8 * game_state.big_blind:
                    stats["large_raises"] += 1
                self.opponent_raised_street = (
                    "preflop" if self.board_reveals == 0
                    else "flop" if self.board_reveals <= 3
                    else "turn" if self.board_reveals == 4 else "river"
                )
            elif action.kind == "call":
                stats["calls"] += 1
            else:
                stats["folds"] += 1

    def _primary_opponent_stats(self, game_state):
        for player in game_state.players_in_hand:
            if player != self.player_id:
                return self.opponents.get(game_state.teams[player], {})
        return {}

    def _score(self):
        """Fast starting-hand ranking, deliberately not a true equity estimate."""
        high, low = sorted((card.value for card in self.hand), reverse=True)
        suited = self.hand[0].suit == self.hand[1].suit
        if high == low:
            return 53.0 + 3.5 * (high - 2)
        gap = max(0, high - low - 1)
        score = (
            9.0 + high * 2.6 + low * 1.45
            + (8.0 if suited else 0.0)
            + (12.0 if high == 14 else 0.0)
            + (3.0 if high >= 11 and low >= 10 else 0.0)
            - min(8.0, gap * 2.0)
        )
        return score

    def _equity(self, board, opponents, samples=120):
        """Monte Carlo showdown equity vs uniform unknown hands; ties are split."""
        unavailable = set(self.hand + board)
        deck = [card for card in full_deck() if card not in unavailable]
        missing_board = 5 - len(board)
        needed = missing_board + 2 * opponents
        if needed > len(deck):
            return 0.0

        rng = random.Random(self.random_seed)
        self.random_seed = (self.random_seed * 1103515245 + 12345) & 0xFFFFFFFF
        earned = 0.0
        for _ in range(samples):
            drawn = rng.sample(deck, needed)
            finished_board = board + drawn[:missing_board]
            hero = evaluate(self.hand + finished_board)
            opponent_cards = drawn[missing_board:]
            ranks = [
                evaluate(opponent_cards[2 * i:2 * i + 2] + finished_board)
                for i in range(opponents)
            ]
            best = max(ranks, default=hero)
            if hero > best:
                earned += 1.0
            elif hero == best:
                earned += 1.0 / (1 + ranks.count(best))
        return earned / samples

    def _raise(self, game_state, total):
        if not game_state.can_raise or game_state.raise_range is None:
            return PlayerAction.call(self.player_id)
        low, high = game_state.raise_range
        total = max(low, min(high, int(total)))
        return PlayerAction.raise_to(self.player_id, total)

    def _check_or_fold(self, game_state):
        if game_state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _preflop(self, game_state):
        score = self._score()
        bb = game_state.big_blind
        call = game_state.call_amount
        stack = game_state.stacks[self.player_id]
        current = game_state.current_bet
        button = game_state.button == self.player_id
        opponents = max(1, len(game_state.players_in_hand) - 1)
        stats = self._primary_opponent_stats(game_state)

        # Unraised pot: the SB can open or complete; the BB can check or raise.
        if current <= bb:
            if button:
                if score >= 51 and game_state.can_raise:
                    return self._raise(game_state, round(2.4 * bb))
                return PlayerAction.call(self.player_id)
            if call == 0 and score >= 62 and game_state.can_raise:
                return self._raise(game_state, 3 * bb)
            return PlayerAction.call(self.player_id)

        # Large commitments / shoves need pot-odds-based decisions.
        # This is intentionally conservative against unknown opponents.
        if call >= max(4 * bb, int(0.3 * (stack + game_state.bets[self.player_id]))):
            equity = self._equity([], opponents, samples=180 if opponents == 1 else 75)
            observed = stats.get("raises", 0) + stats.get("calls", 0)
            frequent_shover = (
                observed >= 5
                and stats.get("large_raises", 0) >= 3
                and stats.get("large_raises", 0) / observed > 0.55
            )
            margin = 0.018 if frequent_shover else 0.075
            if equity >= game_state.pot_odds + margin:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        # Value 3-bet the top range; avoid spending the stack with weak cards.
        if score >= 84 and game_state.can_raise:
            if score >= 91 and game_state.effective_stack(self.player_id) <= 18 * bb:
                return self._raise(game_state, game_state.max_raise_to)
            return self._raise(game_state, round(2.7 * current))

        # Defend wider against small raises, but respect the size of the bet.
        threshold = 55 if button else 57
        if call <= 2 * bb:
            threshold -= 4
        elif call >= 4 * bb:
            threshold += 8
        if opponents > 1:
            threshold += 8
        if score >= threshold and call <= max(4 * bb, int(0.22 * stack)):
            return PlayerAction.call(self.player_id)
        return self._check_or_fold(game_state)

    def _postflop(self, game_state):
        board = list(game_state.board)
        opponents = max(1, len(game_state.players_in_hand) - 1)
        samples = 145 if opponents == 1 else 65
        equity = self._equity(board, opponents, samples=samples)
        stats = self._primary_opponent_stats(game_state)
        cost = game_state.call_amount
        category = evaluate(self.hand + board)[0]

        # A raise by an opponent is informative. Particularly discount the
        # uniform-hand estimate when the opponent rarely raises.
        if cost > 0 and self.opponent_raised_street == game_state.street:
            raises = stats.get("raises", 0)
            other = stats.get("calls", 0) + stats.get("folds", 0)
            if raises + other >= 4 and raises / (raises + other) < 0.25:
                equity -= 0.12
            else:
                equity -= 0.04

        pot = game_state.pot  # Already includes any pending opponent bet.
        rng = random.Random(self.random_seed)
        self.random_seed = (self.random_seed * 1664525 + 1013904223) & 0xFFFFFFFF

        if cost:
            if equity >= 0.79 and game_state.can_raise:
                # Pot-size calculations must include the chips we would call.
                target = game_state.current_bet + int(0.65 * (pot + cost))
                return self._raise(game_state, target)
            if equity >= game_state.pot_odds + 0.045:
                return PlayerAction.call(self.player_id)
            return PlayerAction.fold(self.player_id)

        # When checking is free, value-bet credible made hands, and use a
        # small amount of randomised aggression rather than always checking.
        if game_state.can_raise:
            if equity >= 0.73 and (category >= 1 or equity >= 0.90):
                target = game_state.bets[self.player_id] + round(0.6 * pot)
                return self._raise(game_state, target)
            if equity >= 0.59 and category >= 1 and rng.random() < 0.30:
                target = game_state.bets[self.player_id] + round(0.4 * pot)
                return self._raise(game_state, target)
            folds = stats.get("folds", 0)
            observations = folds + stats.get("calls", 0) + stats.get("raises", 0)
            if (
                opponents == 1
                and category == 0
                and 0.25 < equity < 0.50
                and observations >= 8
                and folds / observations > 0.45
                and rng.random() < 0.15
            ):
                return self._raise(
                    game_state, game_state.bets[self.player_id] + round(0.5 * pot)
                )
        return PlayerAction.call(self.player_id)

    def run(self, actions: list[PlayerAction], game_state: GameStateView) -> PlayerAction:
        self._observe(actions, game_state)
        if game_state.street == "preflop":
            return self._preflop(game_state)
        return self._postflop(game_state)

    def end_game(self, actions: list[PlayerAction], game_state: GameStateView):
        self._observe(actions, game_state)
