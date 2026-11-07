"""Small, deterministic checks for development-only benchmark accounting."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.benchmark import (
    EVALUATION_FILES,
    V1_SHA,
    _check_same_evaluation,
    _deal_samples,
    _fault_counts,
    _fault_kind,
    _hand_outcomes,
    _paired_delta,
)


def fake_log():
    # Two identical decks, played in both HU seat rotations, 20-chip BB.
    return SimpleNamespace(
        names=["candidate", "fold"],
        config=SimpleNamespace(deals=2, seed=17, big_blind=20),
        hands=[
            SimpleNamespace(deal=0, seats=["candidate", "fold"],
                            net=[20, -20], faults={}),
            SimpleNamespace(deal=1, seats=["candidate", "fold"],
                            net=[-10, 10], faults={0: "run: InvalidAction: bad raise"}),
            SimpleNamespace(deal=0, seats=["fold", "candidate"],
                            net=[-4, 4], faults={1: "run: TimeoutError: slow"}),
            SimpleNamespace(deal=1, seats=["fold", "candidate"],
                            net=[6, -6], faults={0: "run: RemoteError: fault in rival"}),
        ],
    )


def test_deal_samples_pair_rotations():
    keys, samples = _deal_samples([fake_log()], "candidate")
    assert keys == ["17:0", "17:1"]
    assert samples == pytest.approx([0.6, -0.4])


def test_fault_categories_are_hero_only():
    result = _fault_counts([fake_log()], "candidate")
    assert result == {"faults": 2, "illegal": 1, "timeouts": 1, "other": 0}
    assert _fault_kind("seed: RuntimeError") == "other"
    assert _fault_kind("run: RemoteError: timed out") == "timeouts"


def test_wins_losses_zero_are_per_played_hand():
    result = _hand_outcomes([fake_log()], "candidate")
    assert result == {"wins": 2, "losses": 2, "even": 0}


def test_paired_delta_uses_matching_keys_and_difference_samples():
    old = {"deal_keys": ["1:0", "1:1", "1:2"],
           "deal_bb": [-1.0, 0.0, 1.0]}
    new = {"deal_keys": ["1:0", "1:1", "1:2"],
           "deal_bb": [-0.5, 0.5, 1.5]}
    improvement, interval = _paired_delta(old, new)
    assert improvement == pytest.approx(50.0)
    assert interval == pytest.approx(0.0)
    new["deal_keys"] = ["1:0", "1:9", "1:2"]
    with pytest.raises(ValueError, match="unequal"):
        _paired_delta(old, new)


def test_incomplete_seat_rotation_rejected():
    match = fake_log()
    match.hands.pop()
    with pytest.raises(RuntimeError, match="incomplete"):
        _deal_samples([match], "candidate")


def _matching_evaluators(tmp_path):
    baseline, candidate = tmp_path / "v1", tmp_path / "candidate"
    for root in (baseline, candidate):
        for name in EVALUATION_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"same")
    return baseline, candidate


@pytest.mark.parametrize("name", EVALUATION_FILES)
def test_evaluation_mismatch_is_fail_closed(tmp_path, name):
    baseline, candidate = _matching_evaluators(tmp_path)
    _check_same_evaluation(baseline, candidate)
    (candidate / name).write_bytes(b"different")
    with pytest.raises(RuntimeError, match=name.replace(".", r"\\.").replace("/", r"\/")):
        _check_same_evaluation(baseline, candidate)


@pytest.mark.parametrize("name", ["game_interface.py", "arena/players.py"])
def test_guard_contains_critical_player_interface_dependencies(name):
    assert name in EVALUATION_FILES


def test_evaluation_missing_file_is_fail_closed(tmp_path):
    baseline, candidate = _matching_evaluators(tmp_path)
    (candidate / "game_interface.py").unlink()
    with pytest.raises(RuntimeError, match="game_interface.py"):
        _check_same_evaluation(baseline, candidate)


def test_pin_and_packaging_boundary():
    assert len(V1_SHA) == 40
    assert Path("tools/benchmark.py").exists()
    # submit.local_modules follows only imports from the actual model module;
    # development script is not a submission artifact.
    from submit import local_modules
    sources = [Path(path).name for path in local_modules({"model"}, ".")]
    assert "benchmark.py" not in sources
