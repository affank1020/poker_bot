"""Development-only paired poker benchmark; never imported by the submission bot.

Usage:
    python tools/benchmark.py --smoke
    python tools/benchmark.py --deals 1000 --times 3
    python tools/benchmark.py --candidate-root /path/to/integrated/checkout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

# The exact V1 snapshot, committed before any V2 code was merged.
V1_SHA = "83f2ffb4ac2bdfa6f4371f0a4c610b558f47d893"
OPPONENTS = ("tight", "equity", "shove", "call", "fold", "random")
# A difference here invalidates a strict paired comparison.
EVALUATION_FILES = (
    "arena/match.py", "arena/dummies.py", "arena/remote.py",
    "arena/worker.py", "engine.py", "card_utils.py", "game_state.py",
    "player_action.py", "evals/score.py", "evals/stats.py",
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def _archive_v1(repository: Path, destination: Path) -> None:
    """Extract tracked bytes at V1_SHA; never copy/alter a candidate strategy."""
    resolved = _git(repository, "rev-parse", V1_SHA + "^{commit}")
    if resolved != V1_SHA:
        raise RuntimeError("V1 commit did not resolve to the pinned SHA")
    archive = destination.parent / "v1.tar"
    with archive.open("wb") as stream:
        subprocess.run(
            ["git", "-C", str(repository), "archive", "--format=tar", V1_SHA],
            stdout=stream, check=True,
        )
    destination.mkdir()
    with tarfile.open(archive) as stream:
        for member in stream:
            # Extract ordinary tracked files only, with path traversal blocked.
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError("unexpected path in V1 Git archive")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise RuntimeError("missing archived file")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            else:
                raise RuntimeError("unexpected entry in V1 Git archive")


def _check_same_evaluation(baseline: Path, candidate: Path) -> None:
    """A change in dealing, scoring, or dummies makes paired results unsafe."""
    changed = []
    for name in EVALUATION_FILES:
        a = baseline / name
        b = candidate / name
        if not a.is_file() or not b.is_file():
            changed.append(name)
        elif hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(
            b.read_bytes()
        ).digest():
            changed.append(name)
    if changed:
        raise RuntimeError(
            "V1 and candidate evaluation code differ; paired-deal comparison "
            "is not valid without investigating: " + ", ".join(changed)
        )


def _fault_kind(reason: str) -> str:
    reason = reason.lower()
    if "timeout" in reason or "timed out" in reason:
        return "timeouts"
    if "invalidaction" in reason or "illegal" in reason:
        return "illegal"
    return "other"


def _fault_counts(logs, name: str) -> dict[str, int]:
    counts = {"faults": 0, "illegal": 0, "timeouts": 0, "other": 0}
    for log in logs:
        for hand in log.hands:
            for seat, reason in hand.faults.items():
                if hand.seats[seat] == name:
                    counts["faults"] += 1
                    counts[_fault_kind(reason)] += 1
    return counts


def _hand_outcomes(logs, name: str) -> dict[str, int]:
    counts = {"wins": 0, "losses": 0, "even": 0}
    for log in logs:
        for hand in log.hands:
            net = hand.net[hand.seats.index(name)]
            counts["wins" if net > 0 else "losses" if net < 0 else "even"] += 1
    return counts


def _deal_samples(logs, name: str) -> tuple[list[str], list[float]]:
    """Net big blinds per hand, paired by (match seed, duplicate deck index).

    Each deck is played once per seat rotation, so the mean of these
    samples times 100 is the familiar bb/100.
    """
    keys, values = [], []
    for log in logs:
        deals = log.config.deals
        rotations = len(log.names)
        totals = [0] * deals
        visits = [0] * deals
        for hand in log.hands:
            seat = hand.seats.index(name)
            totals[hand.deal] += hand.net[seat]
            visits[hand.deal] += 1
        if any(count != rotations for count in visits):
            raise RuntimeError("incomplete duplicate seat rotation")
        for deal, chips in enumerate(totals):
            keys.append(f"{log.config.seed}:{deal}")
            values.append(chips / (rotations * log.config.big_blind))
    return keys, values


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _worker(checkout: Path, output: Path, settings: dict) -> None:
    """One separate interpreter per checkout to prevent model.Model collision.

    Worker, arena.worker, and the pickle all load strategy modules from the
    requested checkout, not from the location of this benchmarking script.
    """
    os.chdir(checkout)
    sys.path.insert(0, str(checkout))
    from dataclasses import replace
    from arena.match import MatchConfig, play_match
    from arena.players import player
    from evals.score import score
    from model import Model

    config = MatchConfig(
        deals=settings["deals"],
        seed=settings["seed"],
        timeout=settings["timeout"],
    )
    results = {}
    with tempfile.TemporaryDirectory(prefix="poker-bench-model-") as scratch:
        model_path = Path(scratch) / "candidate.pkl"
        blob = pickle.dumps(Model(), protocol=4)
        pickle.loads(blob)  # ensure the object is loadable before spawning
        model_path.write_bytes(blob)
        hero = player(str(model_path))
        for name in settings["opponents"]:
            start = time.perf_counter()
            logs = [
                play_match(
                    [hero, player(name)],
                    replace(config, seed=settings["seed"] + i),
                )
                for i in range(settings["times"])
            ]
            result = score(logs)
            keys, samples = _deal_samples(logs, hero.name)
            faults = _fault_counts(logs, hero.name)
            if faults["faults"] != result.faults[0]:
                raise RuntimeError("fault accounting disagrees with evals.score")
            results[name] = {
                "hands": result.hands,
                "decks": len(samples),
                "bb_per_100": result.bb_per_100[0],
                "ci95_bb_per_100": _finite(result.ci95[0]),
                "hand_outcomes": _hand_outcomes(logs, hero.name),
                "faults": faults,
                "first_fault": result.first_faults[0],
                # Includes local IPC overhead; maximum, not mean/p95.
                "max_decision_ms": 1000 * result.max_move_seconds[0],
                "elapsed_seconds": time.perf_counter() - start,
                "deal_keys": keys,
                "deal_bb": samples,
            }
    output.write_text(json.dumps(results, indent=2, allow_nan=False))


def _run_worker(script: Path, checkout: Path, result_path: Path,
                settings: dict) -> dict:
    env = os.environ.copy()
    # arena.remote spawns "python -m arena.worker" with checkout as cwd.
    env["PYTHONPATH"] = str(checkout) + os.pathsep + env.get("PYTHONPATH", "")
    run = subprocess.run(
        [sys.executable, str(script), "--_worker", str(checkout),
         str(result_path), json.dumps(settings)],
        cwd=checkout, env=env, text=True, capture_output=True,
    )
    if run.returncode:
        raise RuntimeError(
            f"evaluation failed in {checkout} (exit {run.returncode}):\n"
            + run.stdout + run.stderr
        )
    return json.loads(result_path.read_text())


def _paired_delta(v1: dict, candidate: dict) -> tuple[float, float | None]:
    if v1["deal_keys"] != candidate["deal_keys"]:
        raise ValueError("cannot pair unequal seed/deal sequences")
    from evals.stats import ci95, mean_and_se

    values = [
        new - old for old, new in zip(v1["deal_bb"], candidate["deal_bb"])
    ]
    mean, se = mean_and_se(values)
    if mean is None:
        raise ValueError("no deals measured")
    return 100 * mean, _finite(100 * ci95(se, len(values)))


def _format_ci(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "n/a"


def _print_report(report: dict) -> None:
    print(f"V1 {report['v1_commit']}")
    print(f"Candidate {report['candidate_commit']}"
          + (" (DIRTY checkout)" if report["candidate_dirty"] else ""))
    print(f"Duplicate deals: {report['settings']['deals']} per rotation, "
          f"{report['settings']['times']} seed(s); "
          "W/L/E = chip-positive / chip-negative / zero-net hands")
    print(f"{'Opponent':<10} {'Bot':<5} {'bb/100 ±95%CI':>20} "
          f"{'W/L/E':>15} {'fault':>6} {'ill.':>5} {'t/o':>4} {'max ms':>9}")
    for opponent, entry in report["comparisons"].items():
        for label, key in (("V1", "v1"), ("V2*", "candidate")):
            item = entry[key]
            outcomes = item["hand_outcomes"]
            ratio = (
                f"{outcomes['wins']}/{outcomes['losses']}/{outcomes['even']}"
            )
            bb = f"{item['bb_per_100']:+.1f} ± {_format_ci(item['ci95_bb_per_100'])}"
            faults = item["faults"]
            print(f"{opponent:<10} {label:<5} {bb:>20} {ratio:>15} "
                  f"{faults['faults']:>6} {faults['illegal']:>5} "
                  f"{faults['timeouts']:>4} {item['max_decision_ms']:>9.1f}")
        print(f"    paired V2* - V1: {entry['delta_bb_per_100']:+.1f} "
              f"± {_format_ci(entry['delta_ci95_bb_per_100'])} bb/100 (95% CI)")
    print("* V2 means the candidate checkout's integrated model.Model, "
          "not unmerged/unwired modules.")
    print("A short run is a fault/packaging smoke test, not proof of strength.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true",
                        help="quick run (8 decks per opponent by default)")
    parser.add_argument("--deals", type=int,
                        help="decks per rotation (default: 8 smoke, 1000 full)")
    parser.add_argument("--times", type=int, default=1,
                        help="number of repeated independent seeds")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--opponents", nargs="+", default=list(OPPONENTS),
                        choices=OPPONENTS)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--candidate-root", type=Path,
                        help="checkout whose current model.Model is V2")
    parser.add_argument("--output", type=Path,
                        help="optional JSON report path OUTSIDE the checkout")
    args = parser.parse_args(argv)
    deals = args.deals if args.deals is not None else (8 if args.smoke else 1000)
    if deals < 1 or args.times < 1 or args.timeout <= 0:
        parser.error("--deals, --times and --timeout must be positive")
    script = Path(__file__).resolve()
    repository = script.parents[1]
    # Running a file in tools/ makes sys.path[0] tools/, not the repo root.
    # Only the benchmark parent needs the unchanged evals.stats helper.
    sys.path.insert(0, str(repository))
    candidate = (args.candidate_root or repository).resolve()
    if not (candidate / "model.py").is_file():
        parser.error("--candidate-root must contain model.py")
    if args.output:
        dest = args.output.resolve()
        if dest.is_relative_to(candidate) or dest.is_relative_to(repository):
            parser.error("--output must be outside both repository checkouts")
    settings = {
        "deals": deals, "times": args.times, "seed": args.seed,
        "opponents": list(dict.fromkeys(args.opponents)),
        "timeout": args.timeout,
    }
    try:
        commit = _git(candidate, "rev-parse", "HEAD")
        dirty = bool(_git(candidate, "status", "--porcelain"))
        with tempfile.TemporaryDirectory(prefix="poker-benchmark-") as scratch:
            temp = Path(scratch)
            baseline = temp / "v1"
            _archive_v1(repository, baseline)
            _check_same_evaluation(baseline, candidate)
            v1 = _run_worker(script, baseline, temp / "v1.json", settings)
            v2 = _run_worker(script, candidate, temp / "candidate.json", settings)
            comparisons = {}
            for opponent in settings["opponents"]:
                delta, interval = _paired_delta(v1[opponent], v2[opponent])
                comparisons[opponent] = {
                    "v1": v1[opponent],
                    "candidate": v2[opponent],
                    "delta_bb_per_100": delta,
                    "delta_ci95_bb_per_100": interval,
                }
            report = {
                "v1_commit": V1_SHA,
                "candidate_commit": commit,
                "candidate_dirty": dirty,
                "settings": settings,
                "comparisons": comparisons,
            }
            _print_report(report)
            if args.output:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(report, indent=2, allow_nan=False))
                print(f"JSON report: {dest}")
    except (OSError, subprocess.CalledProcessError, RuntimeError,
            ValueError) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        print("Use a full Git checkout containing the pinned V1 commit; "
              "if shallow, fetch its history first.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--_worker":
        _worker(Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]))
    else:
        sys.exit(main())
