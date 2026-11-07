# Poker Bot V2 — canonical implementation roadmap

_Last updated: 7 November 2026._

**This file is the cross-chat source of truth.** New chats/agents should read this file, the latest `main`, and all open PRs before starting work. Update this roadmap when decisions or status change; do not rely on chat history.

## Goal / implementation philosophy

Deliver the strongest **working, legal, submission-ready** no-limit Texas Hold'em bot we can within the hackathon window. Favor safe, benchmarked incremental improvements over untested complexity.

**Hybrid rather than training from scratch:** deterministic, auditable street policies + equity calculation (Monte Carlo/exact as appropriate) + **persistent opponent statistics, opponent-conditioned ranges and confidence-gated counter-strategies**. Our hand-designed charts are approximate, not certified solver/GTO charts; avoid unrealistic claims. A large new neural agent / runtime LLM dependency is not a good fit for the current deadline, data, runtime and packaging limits.

**Opponent modelling is a first-class strategy pillar, not a passive statistics collector.** The bot should learn from successive hands against each team, infer actionable tendencies and, with enough evidence, *change its preflop/flop/turn/river decisions to exploit that specific opponent*. It must remain competitive on hand 1 against an unknown opponent. The first safe implementation is a robust persistent baseline; adaptive exploitation is an independently testable enhancement, not a prerequisite for every street to work.

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

## Shared workstream A — opponent intelligence and adaptation (**strategic priority**)

### A1. Persistent observation + inference — **strong baseline, build first**

- [ ] **Standalone module + tests first; no `model.py` modification while PR #2 is being repaired.**
- [ ] Persist independent profiles **per team name**, not seat, across all hands in a match. Reset hand-local trackers in `seed` without resetting match memory. Tournament mode keeps the same model instance across hands; **do not assume memory is automatically shared between separate matches/rounds** unless the organiser explicitly restores a checkpoint.
- [ ] Track **opportunities and outcomes**, not just actions: e.g. preflop voluntarily entering a pot / raises / 3-bets; facing bets and folding or calling on each street; postflop aggression; showdown cards *only when actually revealed*. Separate preflop/flop/turn/river and bet-size buckets where sample count justifies it.
- [ ] Consume the incremental `run`/`end_game` action streams exactly once; preserve hand context/board progression, including dealer vs player `reveal_card`, blind posting, and seat changes.
- [ ] Estimate tendencies with smoothed rates (e.g. Beta/binomial prior or empirical-Bayes/shrinkage) and confidence / observation counts. **Unknown opponents use the broad default prior**; never label someone a habitual bluffer from one hand.
- [ ] Convert public preflop and later street action history into **opponent-conditioned plausible hand ranges**, with card removal, street-by-street weighting and a safe fallback for sparse data. Do not pretend to know folded cards or unshown hole cards.
- [ ] Provide a small stable API (illustrative): `observe(actions, view, hero_id)`, `profile(team)`, `range_weights(team, street, public_context)`, `confidence(team, feature)`. Keep data pickle-safe and bounded.
- [ ] Test across hands, seat swaps, multiway, action-stream boundaries, sparse observations, showdown reveals, pickle/restore, and zero illegal actions when integrated.

**A1 completion gate:** model actually updates its estimates from repeated hands; four street policies can consume them; tests demonstrate hand-to-hand persistence and correct priors. This is the **baseline intelligent opponent model**, even if no explicit exploit overlay is enabled.

### A2. Confidence-gated counter-strategy — **high-value incremental improvement**

- [ ] Start with a small *interpretable* set of tendencies, not a heavyweight classifier: **tight/passive**, **loose/passive (calling station)**, **aggressive / over-bluffing**, and **over-folding**. These are descriptive hypotheses, not ground truth; model each by observable rates and sample confidence.
- [ ] Expose an `exploit_adjustment(team, street, context)` (or equivalent) that makes **bounded changes** to default ranges, equity thresholds and bet/bluff sizing. Base every adjustment on the applicable opportunity denominator and enough evidence.
- [ ] Concrete, testable counters:
  - **Calls too often / folds rarely:** bluff less; value bet stronger and sometimes thinner; do not blindly fold good value hands.
  - **Folds too often to bets:** increase selective steals / bluffs when range and stack permit, with capped sizing/frequency.
  - **Raises/3-bets tightly:** defend tighter against credible aggression while stealing more when they surrender the blinds.
  - **Raises extremely widely:** value-bet/3-bet stronger hands more and bluff-catch more *only when observed evidence supports a wider range*.
- [ ] Use adjustments throughout **all four streets**: preflop opens/defends/3-bets, flop continuation-bet/call decisions, turn barreling/defence, and river value-vs-bluff-catching. Correct application matters more than complex taxonomy.
- [ ] **Safety gate:** when sample size is small, a stat's context is inapplicable, or opponents appear balanced, use A1/default policy. Smooth estimates, cap adjustment magnitude, and avoid conflicting overrides. No secret reads, unbounded growth or runtime complexity.
- [ ] Evaluate **A1 baseline vs A1+A2 adaptation** on repeated seeded matches against distinct known opponent styles, including changing styles and an unknown/balanced opponent. Require legal moves, stable runtime and credible gains without a severe balanced-opponent regression.

**A2 completion gate:** a reproducible test shows the **same hero hand + public state** can produce an appropriately different legal action against two opponents with well-supported different observed histories; live match tests show useful adaptation as more hands are played. If results are noisy/negative near the deadline, leave A2 behind a simple opt-out and ship A1.

### A3. Extended / recency-aware memory — **optional, not on critical path**

- [ ] **Only after A1+A2 are stable:** maintain bounded long-run statistics **plus a small recent-hands view** (EWMA or fixed-size rolling counts per opponent). Detect *possible* shifts from loose to tight or vice versa and let recent evidence cautiously influence the model.
- [ ] Avoid full hand-history storage, a learned opponent neural network, online gradient training, HMMs or giant archetype ensembles under this deadline. They add variance, state bugs and integration risk for uncertain benefit.
- [ ] Benchmark when opponents deliberately switch styles mid-match; verify false switch detection does not destabilise play. If not convincingly better, defer.

**Priority rule:** A1 must be integrated early, before all four street policies are finalized; A2 is a scoped, high-value upgrade; A3 is explicitly a stretch goal. *Persistent across-hands memory already belongs to A1.* “Extended memory” means additionally tracking recency/change, **not** making persistence work for the first time.

### How opponent intelligence informs the four streets

| Street | A1 inference used by baseline | A2 exploit (only with confidence) |
|---|---|---|
| Preflop | Position/action-aware opening, call and 3-bet ranges | Adjust steals, defends and value 3-bets versus opponent aggression/folds |
| Flop | Weight likely holdings from preflop and flop betting; derive equity | Selectively adjust continuation bets, value sizing and calling thresholds |
| Turn | Update posterior range after turn action and board card | Adjust second barrels, protection and defence against credible pressure |
| River | Estimate final showdown equity and opponent bet/value ranges | Choose thin value, folds, bluff-catches and rare bluffs based on call/fold/bluff tendencies |

**Important:** pure card equity is not equal to betting EV. Use observed action tendencies to condition ranges and fold-equity assumptions, rather than subtracting a universal constant from equity after a raise.

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
5. **Merge order** (development may overlap): **PR #2 preflop → A1 opponent observation/inference + range helpers → flop → turn → river → A2 confidence-gated exploits if benchmarked → final integration/validation**. A3 recency-aware memory is stretch only; A2 can be prototyped in parallel but must not block street functionality.
6. Every merge must clear review concerns, full CI + submit packaging and V1 comparative evaluation. Prefer to drop a risky feature rather than break a working final submission.
7. To coordinate across chats, link new PRs and record status/decisions **here**; do not use stale chat memory as a hidden dependency.

## Status log

- **2026-11-07:** Chose hybrid incremental V2 over training a new model. Prioritize four poker streets and persistent opponent modelling. V1 frozen on `main`; PR #2 is open, fixing review findings; independent opponent-model work can proceed without touching `model.py`. CI introduced in PR #2. Development can run in parallel; integration stays sequential.
- **2026-11-07:** GitHub repository has **Issues disabled** (HTTP 410 from issue creation). This tracked document is the durable alternative. If Issues are enabled later, open one canonical tracking issue linking this file.
- **2026-11-07 (opponent-model priority):** Promote opponent profiling and adaptation to a first-class pillar across preflop/flop/turn/river. Distinguish **A1 persistent cross-hand observations/ranges (baseline)**, **A2 bounded confidence-gated counter-strategy (high-value upgrade)** and **A3 recency-aware change detection (optional stretch)**. No heavy online training or forced A3 implementation in the critical path.

> **For a new agent/chat:** Read this roadmap and PR #2 (including current review threads), inspect repository and CI, claim a non-overlapping workstream, and update this document/PR links when the state changes.
