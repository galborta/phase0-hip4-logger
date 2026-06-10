#!/usr/bin/env python3
"""
Phase 0 — Step 3: Mark match events with a precise local timestamp.

Run this in a second terminal WHILE the logger is running. When you SEE a
match event happen on a low-latency feed (or the fastest one you have), hit
enter / type the event. It records the same time.time_ns() clock the logger
uses, so we can align "the moment the event was visible to you" against the
order book and measure how long the HIP-4 book stays stale.

Two modes:

  Interactive (recommended during a match):
      python mark_event.py --coin "#10"
      > [enter shortcut] then type:  pen   (penalty awarded)
      Shortcuts: pen, red, goal, var, sub, note
      Anything else is recorded as a free-text note.

  One-shot:
      python mark_event.py --coin "#10" --event pen --note "PSG penalty"

Output: appends to data/events.jsonl
    {"mark_ns","mark_iso","coin","event","note","feed_delay_s"}

IMPORTANT: tell the truth about your feed delay. If you're watching a stream
that's ~30s behind, pass --feed-delay 30 so analysis can shift your marks back
toward real event time. The cleaner your reference feed, the better the result.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

SHORTCUTS = {
    "pen": "penalty_awarded",
    "red": "red_card",
    "goal": "goal",
    "var": "var_review",
    "sub": "substitution",
    "note": "note",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def record(outdir, coin, event, note, feed_delay):
    os.makedirs(outdir, exist_ok=True)
    row = {
        "mark_ns": time.time_ns(),
        "mark_iso": now_iso(),
        "coin": coin,
        "event": event,
        "note": note,
        "feed_delay_s": feed_delay,
    }
    with open(os.path.join(outdir, "events.jsonl"), "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"  recorded: {event}  {row['mark_iso']}  (feed_delay={feed_delay}s)  {note}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", required=True, help="coin name this event applies to, e.g. #10")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--event", default=None, help="one-shot event (shortcut or text)")
    ap.add_argument("--note", default="")
    ap.add_argument("--feed-delay", type=float, default=0.0,
                    help="seconds your reference feed lags real life")
    args = ap.parse_args()

    if args.event is not None:
        ev = SHORTCUTS.get(args.event, args.event)
        record(args.outdir, args.coin, ev, args.note, args.feed_delay)
        return

    print(f"Marking events for {args.coin}. Shortcuts: {', '.join(SHORTCUTS)}")
    print("Type a shortcut (or free text) + Enter the instant you see the event. Ctrl-C to quit.")
    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            key = parts[0].lower()
            note = parts[1] if len(parts) > 1 else args.note
            ev = SHORTCUTS.get(key, line)
            record(args.outdir, args.coin, ev, note, args.feed_delay)
    except (KeyboardInterrupt, EOFError):
        print("\nDone.")


if __name__ == "__main__":
    main()
