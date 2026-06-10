#!/usr/bin/env python3
"""
Phase 0 — Step 4: Analyze the logs. The whole point of Phase 0.

Answers three questions that gate the entire project:

  1. DEPTH/SPREAD: Are these markets liquid enough to trade? What's the median
     spread, and how much size rests near the top of book?
  2. STALENESS: When a discontinuous event happens (penalty, red card), how long
     does the HIP-4 book stay stale before it reprices? That window is the
     "non-suspension edge" your friend stumbled into. We measure it in seconds.
  3. ACTIVITY: How often does the book update, how often do trades print?

Reads the JSONL produced by logger.py + mark_event.py.

Usage:
    python analyze.py --data ./data
    python analyze.py --data ./data --coin "#10"
    python analyze.py --data ./data --move-threshold 0.02   # what counts as a reprice

Pure stdlib. No external deps.
"""
import argparse
import glob
import json
import os
import statistics as st
from datetime import datetime, timezone


def load_jsonl(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def parse_book(msg):
    """Return (best_bid, best_ask, bid_levels, ask_levels) from an l2Book data msg.
    HL format: data['levels'] = [bids, asks]; each level {'px','sz','n'}."""
    levels = msg.get("levels") or msg.get("data", {}).get("levels")
    if not levels or len(levels) < 2:
        return None
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        return (None, None, bids or [], asks or [])
    bb = float(bids[0]["px"])
    ba = float(asks[0]["px"])
    return bb, ba, bids, asks


def depth_near_top(side_levels, best, band):
    """Sum size within `band` (price units) of the best price on one side."""
    total = 0.0
    for lvl in side_levels:
        px = float(lvl["px"])
        if abs(px - best) <= band:
            total += float(lvl["sz"])
    return total


def fmt_secs(ns):
    return f"{ns / 1e9:.2f}s"


def analyze_book_file(path, band):
    rows = load_jsonl(path)
    spreads, mids, bid_depth, ask_depth = [], [], [], []
    update_gaps = []
    prev_ns = None
    snapshots = []  # (recv_ns, mid, bb, ba) for staleness alignment

    for r in rows:
        recv_ns = r.get("recv_ns")
        parsed = parse_book(r.get("msg", {}))
        if not parsed:
            continue
        bb, ba, bids, asks = parsed
        if bb is None or ba is None:
            continue
        spread = ba - bb
        mid = (ba + bb) / 2
        spreads.append(spread)
        mids.append(mid)
        bid_depth.append(depth_near_top(bids, bb, band))
        ask_depth.append(depth_near_top(asks, ba, band))
        snapshots.append((recv_ns, mid, bb, ba, spread))
        if prev_ns is not None:
            update_gaps.append(recv_ns - prev_ns)
        prev_ns = recv_ns

    return {
        "n": len(spreads),
        "spreads": spreads,
        "mids": mids,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "update_gaps": update_gaps,
        "snapshots": snapshots,
    }


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def summarize_liquidity(name, a, band):
    if a["n"] == 0:
        print(f"\n[{name}] no parseable book snapshots.")
        return
    sp, md = a["spreads"], a["mids"]
    print(f"\n=== {name} — liquidity ({a['n']} snapshots) ===")
    print(f"  mid price:      median {pct(md,.5):.4f}   range [{min(md):.4f}, {max(md):.4f}]")
    print(f"  spread:         median {pct(sp,.5):.4f}   p90 {pct(sp,.9):.4f}   "
          f"(as % of mid: {100*pct(sp,.5)/max(pct(md,.5),1e-9):.2f}%)")
    print(f"  depth +/-{band} of top:  bid median {pct(a['bid_depth'],.5):.1f}   "
          f"ask median {pct(a['ask_depth'],.5):.1f}")
    if a["update_gaps"]:
        g = [x / 1e9 for x in a["update_gaps"]]
        print(f"  book update gap:  median {pct(g,.5):.3f}s   p90 {pct(g,.9):.3f}s")
    print(f"  --> Edge gate: your model edge must exceed ~{pct(sp,.5)/2:.4f} (half-spread) "
          f"per side just to trade at mid.")


def find_book_at(snapshots, t_ns):
    """Last snapshot at or before t_ns."""
    best = None
    for snap in snapshots:
        if snap[0] is None:
            continue
        if snap[0] <= t_ns:
            best = snap
        else:
            break
    return best


def staleness_around_events(events, books_by_coin, move_threshold, pre_s=90.0, post_s=240.0):
    if not events:
        print("\n(no events.jsonl marks — run auto_events.py or mark_event.py during a match)")
        return
    print(f"\n=== BOOK REACTION around events (reprice threshold = {move_threshold}) ===")
    print(f"  Baseline = book mid {int(pre_s)}s before the event timestamp.")
    print("  OFFSET = (first book reprice time) - (event timestamp). Read it as:")
    print("    positive  -> book LAGGED the feed by that many seconds  = exploitable staleness window")
    print("    negative  -> book moved BEFORE the feed reported  = feed too slow to be the trigger")
    for ev in events:
        coin = ev.get("coin")
        snaps = books_by_coin.get(coin, {}).get("snapshots", [])
        if not coin or not snaps:
            print(f"  - {ev.get('event')} on {coin}: no book data for this coin.")
            continue
        mark_ns = ev["mark_ns"]
        feed_delay_ns = int(ev.get("feed_delay_s", 0) * 1e9)
        event_t = mark_ns - feed_delay_ns  # detection time, optionally shifted

        pre_t = event_t - int(pre_s * 1e9)
        post_t = event_t + int(post_s * 1e9)
        base = find_book_at(snaps, pre_t) or find_book_at(snaps, event_t)
        if not base:
            print(f"  - {ev.get('event')} on {coin}: no book snapshot near event.")
            continue
        base_mid = base[1]

        # First snapshot in [pre_t, post_t] whose mid moved >= threshold vs baseline.
        reprice = None
        for snap in snaps:
            if snap[0] is None or snap[0] < pre_t or snap[0] > post_t:
                continue
            if abs(snap[1] - base_mid) >= move_threshold:
                reprice = snap
                break

        src = ev.get("source", "manual")
        clk = ev.get("match_clock", "")
        print(f"\n  • {ev.get('event')}  ({ev.get('note','')})  coin {coin}  [{src} {clk}]")
        print(f"      baseline mid ({int(pre_s)}s pre): {base_mid:.4f}")
        if reprice is None:
            print(f"      no move >= {move_threshold} within [-{int(pre_s)}s, +{int(post_s)}s] of the event.")
        else:
            offset_ns = reprice[0] - event_t
            tag = ("book LAGGED feed" if offset_ns > 0 else "book LED feed")
            print(f"      reprice mid:          {reprice[1]:.4f}  (moved {reprice[1]-base_mid:+.4f})")
            print(f"      >>> OFFSET: {offset_ns/1e9:+.1f}s   <-- {tag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--coin", default=None, help="restrict to one coin")
    ap.add_argument("--band", type=float, default=0.02,
                    help="price band around top-of-book for depth sums")
    ap.add_argument("--move-threshold", type=float, default=0.02,
                    help="mid move that counts as a reprice, in price (0-1) units")
    args = ap.parse_args()

    book_files = sorted(glob.glob(os.path.join(args.data, "l2book_*.jsonl")))
    if args.coin:
        safe = args.coin.replace("#", "o")
        book_files = [p for p in book_files if safe in os.path.basename(p)]

    if not book_files:
        print(f"No l2book_*.jsonl files found in {args.data}. Run logger.py first.")
        return

    books_by_coin = {}
    for path in book_files:
        a = analyze_book_file(path, args.band)
        # recover coin from first row
        coin = None
        rows = load_jsonl(path)
        for r in rows:
            if r.get("coin"):
                coin = r["coin"]
                break
        coin = coin or os.path.basename(path)
        books_by_coin[coin] = a
        summarize_liquidity(coin, a, args.band)

    events_path = os.path.join(args.data, "events.jsonl")
    events = load_jsonl(events_path) if os.path.exists(events_path) else []
    staleness_around_events(events, books_by_coin, args.move_threshold)

    print("\n=== Phase 0 gate ===")
    print("  GO if: spreads tight relative to your model edge AND a measurable staleness")
    print("  window exists after events. NO-GO if: spreads swamp any plausible edge or")
    print("  the book reprices effectively instantly (no exploitable lag).")


if __name__ == "__main__":
    main()
