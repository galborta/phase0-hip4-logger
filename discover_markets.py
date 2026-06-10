#!/usr/bin/env python3
"""
Phase 0 — Step 1: Discover live HIP-4 outcome markets.

Fetches the `outcomeMeta` info response from Hyperliquid, lists every outcome
market with its encoding, coin name (#<encoding>), asset id, and any human title
the API exposes. Use this to find the World Cup match markets you want to log,
then copy their coin names into config.json.

No keys, no signing — read-only.

Usage:
    python discover_markets.py                 # list everything
    python discover_markets.py --grep psg      # filter titles/symbols
    python discover_markets.py --json out.json # dump raw outcomeMeta
"""
import argparse
import json
import sys
import urllib.request

INFO_URL = "https://api.hyperliquid.xyz/info"


def post_info(body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        INFO_URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def encoding(outcome_id: int, side: int) -> int:
    return 10 * outcome_id + side


def walk(obj, path=""):
    """Yield (path, value) for scalar leaves, so we can robustly find titles
    regardless of the exact outcomeMeta schema."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", default=None, help="case-insensitive filter on the raw text")
    ap.add_argument("--json", default=None, help="write raw outcomeMeta to this file")
    args = ap.parse_args()

    try:
        meta = post_info({"type": "outcomeMeta"})
    except Exception as e:
        print(f"ERROR calling outcomeMeta: {e}", file=sys.stderr)
        print(
            "If this fails, the request type name may have changed. Inspect the API "
            "docs at hyperliquid.gitbook.io/.../api/asset-ids and adjust.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"Raw outcomeMeta written to {args.json}")

    # The exact schema may evolve; print it raw first so you can eyeball it,
    # then a best-effort flattened view to spot titles + ids.
    raw = json.dumps(meta, indent=2)
    if args.grep:
        g = args.grep.lower()
        lines = [ln for ln in raw.splitlines() if g in ln.lower()]
        print("\n".join(lines) if lines else f"(no lines match '{args.grep}')")
        print("\n--- showing matching lines only; rerun without --grep for full schema ---")
        return

    print("=== RAW outcomeMeta (inspect the structure) ===")
    print(raw[:6000] + ("\n... (truncated, use --json to dump full)" if len(raw) > 6000 else ""))

    print("\n=== Flattened leaves mentioning id / name / title / outcome ===")
    for path, val in walk(meta):
        low = path.lower()
        if any(t in low for t in ("name", "title", "id", "outcome", "side", "symbol", "coin")):
            print(f"{path} = {val!r}")

    print(
        "\nReminder: coin name for subscriptions is '#<encoding>' where "
        "encoding = 10*outcome + side (side 0 or 1). Asset id = 100_000_000 + encoding."
    )


if __name__ == "__main__":
    main()
