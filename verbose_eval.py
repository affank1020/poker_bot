"""Verbose local benchmark for the poker bot.

Development-only script. It imports the official engine without modifying it,
and is NOT part of the submitted model.
"""

import argparse
import pickle
import tempfile
from pathlib import Path

from arena.dummies import DUMMIES
from arena.match import MatchConfig, play_match
from arena.players import player
from arena.replays import site_hand
from evals.score import score
from model import Model


def show_hand(record, number):
    replay = site_hand(record, number - 1)
    hero = record.seats.index("you")
    cards = " ".join(str(card) for card in record.result.hands[hero])
    print(f"\n--- Hand {number} | seat {hero} | "
          f"{'small blind' if record.button == hero else 'big blind'} ---")
    print(f"Your cards: {cards}")
    board = replay["board"]
    street = None
    for seat, kind, amount, action_street, _fault in replay["actions"]:
        if action_street != street:
            street = action_street
            if street == "preflop":
                print("  PREFLOP")
            elif street == "flop":
                print("  FLOP  " + " ".join(board[:3]))
            elif street == "turn":
                print("  TURN  " + " ".join(board[:4]))
            elif street == "river":
                print("  RIVER " + " ".join(board[:5]))
        name = record.seats[seat]
        if kind.startswith("post_"):
            move = f"{kind.replace('_', ' ')} {amount}"
        elif kind in ("raise_to", "bet", "call"):
            move = f"{kind.replace('_', ' ')} {amount}"
        else:
            move = kind
        print(f"    {name}: {move}")

    print(f"  Result: {replay['end']}")
    if replay["hole"] is not None:
        for seat, cards in enumerate(replay["hole"]):
            print(f"    {record.seats[seat]}: {' '.join(cards)} "
                  f"({replay['res'][seat][1]})")
    print(f"  Net: you {record.result.net[hero]:+d} chips")
    if record.result.faults:
        print(f"  FAULTS: {record.result.faults}")


def main():
    parser = argparse.ArgumentParser(
        description="Live progress and hand replays against a dummy bot."
    )
    parser.add_argument("--opponent", choices=sorted(DUMMIES), default="equity")
    parser.add_argument("--deals", type=int, default=100,
                        help="Decks per seat rotation; total hands = 2 * deals")
    parser.add_argument("--progress", type=int, default=20,
                        help="Print a progress update every N hands")
    parser.add_argument("--show", type=int, default=3,
                        help="Print full actions for the first N hands")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.deals < 1 or args.progress < 1 or args.show < 0:
        parser.error("--deals and --progress must be positive; --show >= 0")

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "you.pkl"
        with path.open("wb") as handle:
            pickle.dump(Model(), handle)

        players = [player(str(path)), player(args.opponent)]
        total_hands = args.deals * 2
        count = 0
        net_chips = 0
        faults = 0
        print(f"Playing {total_hands} hands vs {args.opponent}; "
              f"same pickled-worker approach as the official evaluation.",
              flush=True)

        def on_hand(record):
            nonlocal count, net_chips, faults
            count += 1
            hero = record.seats.index("you")
            net_chips += record.result.net[hero]
            faults += int(hero in record.result.faults)
            if count <= args.show:
                show_hand(record, count)
            if count % args.progress == 0 or count == total_hands:
                running = 100 * net_chips / record.big_blind / count
                print(f"[{count}/{total_hands}] "
                      f"running {running:+.1f} bb/100 | "
                      f"faults {faults}", flush=True)

        result = play_match(
            players,
            MatchConfig(deals=args.deals, seed=args.seed),
            on_hand=on_hand,
        )
        measured = score([result])
        print("\nFINAL")
        print(f"{args.opponent}: {measured.bb_per_100[0]:+.1f} "
              f"± {measured.ci95[0]:.1f} bb/100 (95% CI)")
        print(f"Hands: {len(result.hands)}, faults: {measured.faults[0]}, "
              f"slowest move: {measured.max_move_seconds[0]:.3f}s")


if __name__ == "__main__":
    main()
