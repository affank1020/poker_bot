# Workstream B: equity/range integration contract

This is a **cross-agent interface**, not an opponent-profiling implementation.
PR introducing this contract deliberately leaves model.py untouched so preflop
and opponent-intelligence branches can proceed independently.

## Producer: A1 opponent intelligence

For each player still in the hand *other than the hero*, in seat/list order,
produce either:

- None: insufficient observations, use the uniform uninformative prior.
- A mapping from (Card, Card) to nonnegative finite relative weight.

The keys represent **specific two-card combinations**, e.g. (Card.parse("AH"),
Card.parse("KD")). The producer may begin with 169-class starting-hand
weights but must expand them into real, blocker-respecting card combinations
at the integration boundary.

Action history, board texture, team-name identity, smoothing, confidence and
across-hand persistence remain A1 responsibilities. The equity engine never
looks at opponents' hidden cards or action streams.

## Consumer: postflop street policies (flop / turn / river)

Use:

    from equity_engine import estimate_equity

    opponents = [
        p for p in game_state.players_in_hand if p != self.player_id
    ]
    weights = [
        opponent_model.range_weights(
            game_state.teams[p], game_state.street, context
        )
        for p in opponents
    ]
    result = estimate_equity(
        self.hand, game_state.board, opponents=len(opponents),
        ranges=weights, samples=120, seed=self.random_seed
    )
    showdown_share = result.equity

**Actual A1 PR #5 API:** opponent_model.py currently exposes
preflop_range_weight(team, two_cards, action=None) as a positive relative
weight for a concrete combo, plus hand_line(team), profile(team), and public
street-action history. An integration adapter can build the mapping without
coupling either module:

    from itertools import combinations
    from card_utils import full_deck
    dead = set(self.hand) | set(game_state.board)
    available = [c for c in full_deck() if c not in dead]
    weights = [
        {
            combo: opponent_model.preflop_range_weight(
                game_state.teams[p], combo
            )
            for combo in combinations(available, 2)
        }
        for p in opponents
    ]

For unknown opponents or insufficient observations, prefer None / broad
priors; avoid treating preflop-only action weights as a calibrated postflop
posterior. The producer may refine the distribution using street lines later.
Each mapping must contain concrete 2-Card tuple keys, not chart labels.
A1 does not currently expose range_weights(), so the earlier example is a
future-facing interface, not an implemented method.

The result contains equity (expected fraction of pot won, correctly splitting
ties), win_rate, tie_rate, trials and exact. With a complete river board and
small enough range product the result is exact; otherwise it is reproducible
Monte Carlo. If opponents' conditioned ranges are effectively impossible
to reconcile, the engine may raise ValueError or return fewer trials than
requested. The integration layer should **explicitly** fall back to wider
uninformative priors when evidence is sparse or inconsistent.

Equity estimates evaluate showdown outcomes, **not betting EV**: street
policies must separately calculate pot odds, fold equity, future action costs,
all-in commitment, and legal raise sizing.

## Packaging and safety

- The engine imports only stdlib and organiser card_utils.
- The organiser's submit.py discovers imported local modules transitively from
  model.py. Once model.py imports equity_engine, include equity_engine.py in the
  upload list shown by python -m submit.
- No files are read at model runtime; no network, seeds or hidden opponent
  cards are assumed available.
- Keep one branch per workstream. Do not edit model.py in this PR.
- Run python -m pytest tests/test_equity_engine.py -q. Full CI/submit checks
  must run on the integrated main once preflop PR #2 is merged.
