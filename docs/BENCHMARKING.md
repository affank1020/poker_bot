# V1 versus integrated-candidate benchmarking

This is **development-only tooling**. It must not be copied into the submission
directory or imported from the bot. No strategy or organiser source file is
modified by the benchmark.

## What is compared?

- **V1** is the exact Git tree at
  `83f2ffb4ac2bdfa6f4371f0a4c610b558f47d893`
  (the frozen "Save baseline poker bot and verbose evaluator" commit).
- **Candidate** is whatever `model.Model` exists in the selected **checkout**
  when the command runs. Before final integration, that is *not* the entire V2
  design: unmerged policy modules are not silently included.
- Each is pickled and loaded by the repository's actual separate arena worker
  process. V1 is extracted with `git archive` from the exact SHA; its code is
  never reconstructed or renamed. The candidate and V1 run in distinct
  Python interpreter processes with distinct module search paths, avoiding
  the `model.Model` import collision.
- Both run the six organiser dummies through unchanged
  `arena.match.play_match` and `evals.score.score`. Every deck is played
  under both heads-up seat rotations. The same seeds/deck indices are used
  on both checkouts. The harness **refuses** a paired comparison if evaluation
  engine, dummies, worker or scoring source files differ between checkouts.

These are **separate V1-vs-dummy and candidate-vs-dummy evaluations**, not a
direct V1-vs-V2 head-to-head game. Card deals are paired; the action paths,
number of decisions and random-bot draw counts can differ by strategy.

## Commands

Run from a full Git checkout containing the frozen V1 commit:

```sh
# Fast legality/fault and end-to-end smoke: 8 decks x 2 rotations per
# opponent and bot, six opponents (numbers are NOT reliable strength estimates).
python tools/benchmark.py --smoke

# Faster targeted check when racing the clock.
python tools/benchmark.py --smoke --deals 4 --opponents fold call shove

# Longer comparison after the final integration PR is merged:
python tools/benchmark.py --deals 1000 --times 3 \
  --opponents tight equity shove call fold random \
  --output /tmp/poker-v1-v2-benchmark.json

# Script can live on its own PR checkout while evaluating a different,
# fully integrated candidate checkout; this does NOT change either checkout.
python tools/benchmark.py --smoke --candidate-root /path/to/integrated/repo
```

`--times 3` repeats the comparison at seeds `--seed`,
`--seed + 1`, `--seed + 2`. `--deals` is **unique decks per seat
rotation**, so heads-up hands per opponent = `2 * deals * times` per bot.
Both models are reset/recreated at each rotation in the original arena
duplicate mode; tournament cross-hand learning must be evaluated separately.
`--timeout` defaults to the organiser's current 10-second-per-call limit.

The scripts create temporary V1 checkouts, pickles, and intermediate results
in system temporary directories and remove them after the run. An optional
JSON report must be written **outside both checkouts**. It contains raw
per-deck numbers and metadata for later analysis. The script prints if the
candidate Git checkout is dirty (then the commit SHA alone does not fully
describe the candidate).

If your clone is shallow and doesn't contain the pinned V1 SHA, fetch the
history before running (e.g. `git fetch --unshallow`); do **not** replace
the baseline with a copied or guessed older policy. GitHub Actions uses
`fetch-depth: 0` for its independent smoke run.

## Interpreting columns

- **bb/100**: mean net big blinds per 100 played hands, pooled over duplicate
  seat rotations. A positive value versus a dummy is good; a negative value
  is bad. The accompanying interval is the existing `evals.score`
  95% Student-t CI, where each duplicated deck is one sample.
- **paired delta**: candidate bb/100 minus V1 bb/100. Its 95% interval is
  computed from **per-seed, per-deck differences**, not by subtracting the
  two independent CI widths. Same engine and deck seeds are checked before
  pairing. An interval crossing zero means insufficient evidence of a gain.
- **W/L/E**: number of individual *played hands* where hero's net chips
  were positive / negative / exactly zero. This is not the percentage of
  showdowns won, nor a tournament-match win rate. Duplicated deals contribute
  one hand per seat rotation.
- **fault**: number of hands with a first recorded hero fault; **ill.**:
  first faults containing `InvalidAction` or `illegal`; **t/o**: first
  faults containing `TimeoutError`, `timeout` or `timed out`.
  Unclassified faults are included in the JSON `other` field and all
  categories sum to the fault count. See `first_fault` in JSON for detail.
- **max ms**: longest observed `run` call in wall-clock milliseconds,
  including worker transport and potentially worker startup. This is the
  maximum measured decision time, **not average or p95 latency**. Total
  evaluation elapsed time is reported separately in JSON.

At 4-8 decks, expected confidence intervals are extremely wide. Results
from tiny runs should be used to detect integration, legality and timing
regressions—not to pick a better strategy. Poker has high variance; even
1,000 deals may be too few to establish small gains, especially against
stronger dummies. Repeat with more independent seeds and inspect
opponent-specific tradeoffs. A strategy that beats `fold` but loses badly
to `equity` may not generalise to the tournament. Always investigate
**any** faults/timeouts before considering bb/100.

The dummy results are not a prediction of performance against actual
opponents; multiway and carried-stack tournament scenarios require separate
validity checks using existing `tests/test_model.py` and organiser tooling.

## Submission safety and validation (separate operation)

**The benchmark command does not run or edit `submit.py`.** Once the
integrated strategy is chosen, from that candidate checkout run the existing
organiser-provided workflow:

```sh
python -m pytest -q
mkdir -p /tmp/poker-submit-check
python -m submit --team benchcheck --out /tmp/poker-submit-check
```

Inspect the printed upload-file list, vet findings, and
`OK: played legally against every dummy.` The existing
`.github/workflows/bot-ci.yml` already runs the full test suite and
`python -m submit` on pull requests. The independent benchmarking-smoke
workflow records tiny, **non-decisive** V1/candidate numbers; it is not a
replacement for the normal packaging check.

The submission should contain only what `submit.py` lists. Nothing under
`tools/`, `tests/`, `docs/` or GitHub Actions is imported by the
submitted bot or included in its pickle's local-module traversal.

## Assumptions and limitations

- Requires a local Git installation, a checkout containing V1 history,
  Python, and the repository's existing dependencies.
- The benchmark targets two-player **duplicate** matches against the
  provided dummy opponents. It does not simulate the organiser's full
  round-robin or prove optimality. It does not benchmark unintegrated modules.
- Version provenance uses the candidate HEAD SHA plus a dirty flag. For
  reproducible published results, compare **clean committed** candidates.
- Equality of evaluation-source bytes is necessary for our claimed paired
  deal setup, but identical seeds do not guarantee identical *action RNG*
  consumption after different policy decisions.
- The arena currently records the **slowest** `run` call only, not per-call
  timing samples. Timeout and illegal-action counts are classified from the
  first fault string per hand; a second fault in the same hand cannot be
  counted separately.
- No numerical performance estimate from a very small smoke run should be
  used to accept or reject a strategy. Run full comparisons only after
  integration; retain the final chosen bot's separate submission check.
