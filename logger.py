#!/usr/bin/env python3
"""
Phase 0 — Step 2: Read-only order-book + trades logger for HIP-4 markets.

Subscribes to l2Book and trades for the coins listed in config.json and writes
every message to newline-delimited JSON with a high-resolution LOCAL receive
timestamp. The local timestamp is what lets us later measure how stale the book
goes around match events (penalties, red cards) versus when those events
actually happened.

This logger NEVER signs or sends orders. It only reads.

Output (in --outdir, default ./data):
    l2book_<coin>.jsonl   one line per book update
    trades_<coin>.jsonl   one line per trade print
    session.log           connection / reconnect log

Each line: {"recv_ns": <int>, "recv_iso": <str>, "coin": <str>, "msg": <raw>}

Usage:
    pip install websockets
    python logger.py                 # uses config.json
    python logger.py --config c.json --outdir ./data
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    print("Missing dependency. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

WS_URL = "wss://api.hyperliquid.xyz/ws"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Writer:
    """Append-only JSONL writer, one file handle per (kind, coin)."""

    def __init__(self, outdir: str):
        self.outdir = outdir
        os.makedirs(outdir, exist_ok=True)
        self._handles = {}

    def _safe(self, coin: str) -> str:
        return coin.replace("#", "o").replace("/", "_").replace(":", "_")

    def write(self, kind: str, coin: str, payload: dict):
        key = (kind, coin)
        if key not in self._handles:
            path = os.path.join(self.outdir, f"{kind}_{self._safe(coin)}.jsonl")
            self._handles[key] = open(path, "a", buffering=1)  # line-buffered
        self._handles[key].write(json.dumps(payload) + "\n")

    def close(self):
        for h in self._handles.values():
            try:
                h.close()
            except Exception:
                pass


def log_session(outdir: str, line: str):
    with open(os.path.join(outdir, "session.log"), "a") as f:
        f.write(f"{now_iso()}  {line}\n")
    print(line)


async def run(config: dict, outdir: str, stop: asyncio.Event):
    coins = config["coins"]
    if not coins:
        log_session(outdir, "No coins in config; nothing to do.")
        return
    writer = Writer(outdir)
    backoff = 1
    msg_count = 0
    last_report = time.time()

    while not stop.is_set():
        try:
            log_session(outdir, f"Connecting to {WS_URL} for {len(coins)} coins...")
            async with websockets.connect(WS_URL, ping_interval=None, max_size=None) as ws:
                # Subscribe to l2Book + trades for each coin.
                for coin in coins:
                    for sub_type in ("l2Book", "trades"):
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": sub_type, "coin": coin},
                        }))
                log_session(outdir, "Subscribed. Logging...")
                backoff = 1  # reset after a successful connect

                # Heartbeat: HL closes idle connections (~60s). Ping periodically.
                async def heartbeat():
                    while not stop.is_set():
                        try:
                            await ws.send(json.dumps({"method": "ping"}))
                            await asyncio.sleep(30)
                        except Exception:
                            return

                hb = asyncio.create_task(heartbeat())
                try:
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=90)
                        recv_ns = time.time_ns()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        chan = msg.get("channel")
                        if chan in ("l2Book", "trades"):
                            data = msg.get("data", {})
                            coin = (
                                data.get("coin")
                                if isinstance(data, dict)
                                else (data[0].get("coin") if data else "?")
                            )
                            kind = "l2book" if chan == "l2Book" else "trades"
                            writer.write(kind, coin or "?", {
                                "recv_ns": recv_ns,
                                "recv_iso": now_iso(),
                                "coin": coin,
                                "msg": data,
                            })
                            msg_count += 1
                        elif chan == "pong":
                            pass
                        elif chan == "subscriptionResponse":
                            log_session(outdir, f"ack: {json.dumps(msg.get('data'))[:200]}")
                        elif chan == "error":
                            log_session(outdir, f"server error: {msg}")

                        if time.time() - last_report > 30:
                            log_session(outdir, f"... {msg_count} messages logged so far")
                            last_report = time.time()
                finally:
                    hb.cancel()
        except asyncio.CancelledError:
            break
        except Exception as e:
            if stop.is_set():
                break
            log_session(outdir, f"connection dropped: {e!r}; reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    writer.close()
    log_session(outdir, f"Stopped. Total messages: {msg_count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_):
        log_session(args.outdir, "Shutdown signal received.")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())

    try:
        loop.run_until_complete(run(config, args.outdir, stop))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
