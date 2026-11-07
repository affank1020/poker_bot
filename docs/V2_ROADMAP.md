# Poker Bot V2 — canonical implementation roadmap

_Last updated: 7 November 2026._

**This file is the cross-chat source of truth.** New chats/agents should read this file, the latest `main`, and all open PRs before starting work. Update this roadmap when decisions or status change; do not rely on chat history.

## Goal / implementation philosophy

Deliver the strongest **working, legal, submission-ready** no-limit Texas Hold'em bot we can within the hackathon window. Favor safe, benchmarked incremental improvements over untested complexity.

**Hybrid rather than training from scratch:** deterministic, auditable street policies + equity calculation (Monte Carlo/exact as appropriate) + **persistent opponent statistics and weighted ranges**. Our hand-designed charts are approximate, not certified solver/GTO charts; avoid unrealistic claims. A large new neural agent / runtime LLM dependency is not a good fit for the current deadline, data, runtime and packaging limits.

The **four stages** refer to the four betting streets: **preflop → flop → turn → river**. Cross-cutting opponent modelling, equity helpers, tests, CI and integration support all four stages.

### Non-negotiable constraints

- Edit bot implementation in `model.py` and new local modules; **never edit organiser-controlled** `card_utils.py`, `player_action.py`, `game_state.py`, or `game_interface.py`.
- Model is pickled; imports, attributes and execution must be self-contained and serializable. No network, external service, secrets, background runtime or file-based model loading.
- `seed(hand, player_id)` resets **hand-local state only**. Preserve learned per-opponent data across hands. `run(actions, game_state)` gets **incremental actions since previous turn**, including own actions and dealer reveals; `end_game` gets remaining stream including any public showdown.
- Opponents are identified by `game_state.teams[player_id]` (**stable team name**, even when seat changes).
- Do not assume hidden opponent cards; only public actions or legitimately shown showdown cards.
- All returns must be valid `PlayerAction` objects; `raise_to` is a street total; respect `can_raise`, `raise_range`, `call_amount`, stack limits, and timeout.
- Check both heads-up and multiway/fallback behavior. Head-up 25-BB is the primary target; avoid hard-coding every decision to that state.

## Stage 0 — V1 baseline, testing and submission

- [x] Preserve V1 implementation on `main` as baseline before V2 merges.
- [x] Create CI workflow in **PR #2**: pytest regression suite and submission packaging check.
- [ ] Record exact baseline commit SHA and reproducible V1 performance versus provided opponents on seeded/duplicate deals.
- [ ] Run full tests, legality/fault checks, runtime and pickle/clean-package checks for every integrated change.

**Gate:** a green isolated unit test is not enough: measure bb/100, error/fault rate, runtime, and package validity.

## Stage 1 — PREFLOP (current PR #2, under review)

[PR #2 — V2 phase 1: explicit 25-BB preflop range policy](https://github.com/affank1020/poker_bot/pull/2)

- [x] Replace opaque numeric hand strength thresholds with explicit **169-hand-class** range charts.
- [x] Implement HU SB/button open strategy, BB vs limp isolation, calling/3-bet defense, multiway defaults and effective-stack-aware 6/11-BB shove rules.
- [x] Introduce focused range/decision tests plus CI.
- [ ] Resolve reviewer findings: **count preflop raises by actual action sequence, not only raise size**; **premium 4-bets before large-bet guard**. Fixes were being validated as of 7 Nov; check current PR head/reviews.
- [ ] Benchmark V2 preflop against V1 using matched deals, confidence intervals where feasible, and zero faults.
- [ ] Merge once reviews, CI, submission and baseline comparison pass.

**Submitted strategy:** position/action/effective-stack-dependent range rules plus pot-odds/equity guard for very large bets. No GTO claim.

## Stage 2 — FLOP (next street feature)

- [ ] Implement flop policy as separately testable helper/module.
- [ ] Monte Carlo showdown equity from hero cards + public flop, with **weighted plausible opponent hands** informed by actions; conservative broad prior until data accumulates.
- [ ] Use board texture, number of opponents, position, betting sequence, pot odds, stack-to-pot ratio (SPR) and available raise sizing.
- [ ] Distinguish check, value/protection bet, semi-bluff, call, raise and fold. Keep pot/bet-total arithmetic correct.
- [ ] Tests: made hands, draws, wet/dry boards, multiway, large bets, short stacks, legal bet sizes.

**Submitted strategy:** range-aware sampled equity + transparent pot-odds / EV thresholds and restrained value/protection/semi-bluff sizing, not complex untested tree search.

## Stage 3 — TURN

- [ ] Reuse flop equity, public-action tracking and opponent range interfaces; no separate incompatible logic.
- [ ] Condition equity/ranges on the fourth community card and turn action; only one board card remains.
- [ ] Estimate commitment/call EV with proper cost/pot calculations and stronger caution versus credible bets.
- [ ] Value/protection/semi-bluff decisions considering draw odds, remaining stack/SPR and blockers where useful.
- [ ] Tests: missed/improving draws, paired and coordinated boards, 2-barrels, all-ins, large raises.

**Submitted strategy:** calibrated equity + narrower ranges / action history + stack-aware turn decisions.

## Stage 4 — RIVER

- [ ] **Exact showdown equity** against plausible opponent hole-card combos where computationally feasible (fixed board, no further draw); bounded fallback if runtime requires it.
- [ ] Explicit pot-odds and action-EV comparison for fold/check/call/value bet/bluff/raise, using conditioned fold/call frequencies only when evidence supports it.
- [ ] Reasonable sizing, thin value and bluff-catching; cautious bluffing when unknown.
- [ ] Tests: nuts, board plays, ties/split pots, counterfeited hands, river overbets, missed draws.

**Submitted strategy:** terminal equity + straightforward call/bet EV instead of projecting future-card equity.

## Shared workstream A — persistent opponent model (may run parallel with PR #2)

- [ ] **Standalone new module and tests first; no `model.py` modification while PR #2 is being repaired.**
- [ ] Maintain per-team statistics across hands; stratify by street and **opportunities**, not merely raw counts: VPIP/PFR-like signals, fold to bet, aggression, showdown tendencies when legally observed.
- [ ] Track hand-local action context and consume **each incremental action exactly once**; correctly distinguish dealer board reveals and player showdown reveals.
- [ ] Use shrinkage/smoothing and minimum sample thresholds; very low samples should behave near conservative default prior, not extreme exploit.
- [ ] Provide small API for opponent profiles/range weighting. Observe betting sequence without illicit information or post-hoc access to private state.
- [ ] Test changing seats, multiple opponents, new hands, duplicate action streams, late end-game streams, pickle preservation, unknown teams and legal public showdown data.
- [ ] Integrate after preflop review, by a coordinator-owned small `model.py` wiring PR.

## Shared workstream B — equity/range engine

- [ ] Share board/hole-card evaluation and compatible weighted-range abstraction across flop/turn/river.
- [ ] Exclude hero and public cards from opponent candidate combinations; sample without replacement, handle multiway and split pots.
- [ ] Run with bounded work per decision; preserve reproducibility and avoid slow exhaustive enumeration on earlier streets.
- [ ] Unit tests on canonical known equity scenarios, ties, card blockers, complete boards and impossible ranges.

## Shared workstream C — CI, benchmarks, submission

- [ ] `python -m pytest -q`
- [ ] `python -m evals --opponents tight equity shove call fold random --deals 1000` (increase / repeat as time permits); compare seeded duplicate deals V1 vs each milestone, with bb/100, CI/noise and faults.
- [ ] `python -m submit --team <team> --out <dir>` in a clean checkout; ensure all imported team-owned modules are present and pickle is loadable in organiser simulation.
- [ ] Confirm legality in varied stacks and board sizes, and no move exceeds runtime limit.
- [ ] **Feature freeze 30–40 minutes before submission**; use final window only for regression, benchmarks, review resolution and packaging.

## Parallel PR and integration protocol

1. One branch + distinct modules + focused tests per workstream. **Do not have competing chats edit `model.py` at once.** Coordinator owns the integration file and cross-policy dispatch.
2. Shared contract to converge on: `OpponentModel` consumes public incremental `actions` and view/own id, returns per-team/street smoothed profiles (and optional range weights); equity helper takes hero cards, public board, number of opponents and weighted priors with compute budget; policies operate on shared state and produce a legal `PlayerAction` or a validated recommendation.
3. Do not couple street policies to unmerged speculative internals. Write independent testable modules first and keep adapters narrow.
4. Each PR description documents: stage/scope, dependencies, design assumptions, review findings, tests, benchmark numbers, packaging impact, and exact integration points.
5. **Merge order** (development may overlap): **PR #2 preflop → opponent/range helpers → flop → turn → river → final integration/validation**.
6. Every merge must clear review concerns, full CI + submit packaging and V1 comparative evaluation. Prefer to drop a risky feature rather than break a working final submission.
7. To coordinate across chats, link new PRs and record status/decisions **here**; do not use stale chat memory as a hidden dependency.

## Status log

- **2026-11-07:** Chose hybrid incremental V2 over training a new model. Prioritize four poker streets and persistent opponent modelling. V1 frozen on `main`; PR #2 is open, fixing review findings; independent opponent-model work can proceed without touching `model.py`. CI introduced in PR #2. Development can run in parallel; integration stays sequential.
- **2026-11-07:** GitHub repository has **Issues disabled** (HTTP 410 from issue creation). This tracked document is the durable alternative. If Issues are enabled later, open one canonical tracking issue linking this file.

> **For a new agent/chat:** Read this roadmap and PR #2 (including current review threads), inspect repository and CI, claim a non-overlapping workstream, and update this document/PR links when the state changes.
