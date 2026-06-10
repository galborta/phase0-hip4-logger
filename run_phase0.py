#!/usr/bin/env python3
"""
Phase 0 — ONE-COMMAND, HANDS-OFF orchestrator.

Launch this once (any time before kickoff) and walk away. It will:
  1. Wait quietly until ~15 min before kickoff (polling ESPN for match state).
  2. Log the HIP-4 order book (l2Book + trades) over WebSocket.
  3. Auto-record goals / cards / penalties + live odds from ESPN — no typing.
  4. Detect full-time, keep a short buffer, then stop everything.
  5. Run the analysis automatically and write data/REPORT.txt.

You come back to a finished report. No manual commands during the match.

    python run_phase0.py --config config.json --outdir data

Options:
    --buffer 180     seconds to keep logging after full-time (default 180)
    --pre 15         minutes before kickoff to start book logging (default 15)
    --force-start    start logging immediately, don't wait for the kickoff window

Trade-off: hands-off means ESPN-only (it reports a penalty only AFTER the kick).
The pre-kick penalty window needs a human; if you're AFK you give that up.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    print("Missing dependency. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

import auto_events as ae  # reuse ESPN helpers
from logger import Writer, WS_URL, now_iso


def parse_kickoff(ev):
    try:
        d = ev.get("date") or ev["competitions"][0]["date"]
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except Exception:
        return None


def lifecycle_decision(now, kickoff, state, post_since, pre_s, buffer_s, max_s, force):
    """Return 'wait' | 'active' | 'stop'. Pure function, unit-tested."""
    if state == "post" and post_since is not None and (now - post_since) >= buffer_s:
        return "stop"
    if force or state == "in" or state == "post":
        return "active"
    if kickoff is not None:
        if now >= kickoff - pre_s:
            return "active"
        return "wait"
    # no kickoff known yet
    return "wait"


async def book_logger(coins, writer, active, stop, log):
    """Subscribe + log book/trades once `active` is set, until `stop`."""
    await active.wait()
    backoff = 1
    count = 0
    while not stop.is_set():
        try:
            log(f"book: connecting for {len(coins)} coins")
            async with websockets.connect(WS_URL, ping_interval=None, max_size=None) as ws:
                for coin in coins:
                    for t in ("l2Book", "trades"):
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "subscription": {"type": t, "coin": coin}}))
                backoff = 1

                async def hb():
                    while not stop.is_set():
                        try:
                            await ws.send(json.dumps({"method": "ping"}))
                            await asyncio.sleep(30)
                        except Exception:
                            return
                h = asyncio.create_task(hb())
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
                            coin = data.get("coin") if isinstance(data, dict) else None
                            kind = "l2book" if chan == "l2Book" else "trades"
                            writer.write(kind, coin or "?", {"recv_ns": recv_ns,
                                                             "recv_iso": now_iso(),
                                                             "coin": coin, "msg": data})
                            count += 1
                finally:
                    h.cancel()
        except asyncio.CancelledError:
            break
        except Exception as e:
            if stop.is_set():
                break
            log(f"book: dropped ({e!r}); reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
    log(f"book: stopped, {count} messages")


async def espn_poller(cfg, outdir, interval, pre_s, buffer_s, max_s, force, active, stop, log):
    espn = cfg["espn"]
    league, date, event_id = espn["league"], espn["date"], espn["event_id"]
    seen = set()
    events_path = os.path.join(outdir, "events.jsonl")
    odds_path = os.path.join(outdir, "odds.jsonl")
    kickoff = None
    post_since = None
    started = time.time()
    last_state = None
    loop = asyncio.get_event_loop()

    while not stop.is_set():
        try:
            sb = await loop.run_in_executor(None, ae.fetch_scoreboard, league, date)
            ev = ae.find_event(sb, event_id)
            recv_ns = time.time_ns()
            now = time.time()
            state = None
            if ev is not None:
                if kickoff is None:
                    kickoff = parse_kickoff(ev)
                    if kickoff:
                        log(f"kickoff at {kickoff.isoformat()} (UTC)")
                comp = ev["competitions"][0]
                st = (comp.get("status", {}) or {}).get("type", {}) or {}
                state = st.get("state")
                disp = (comp.get("status", {}) or {}).get("displayClock", "")
                if state != last_state:
                    log(f"match state = {st.get('description')} ({disp})")
                    last_state = state
                if state == "post" and post_since is None:
                    post_since = now
                    log(f"full time detected; logging {int(buffer_s)}s more then analyzing")

                od = ae.extract_odds(ev)
                if od:
                    ae.write_jsonl(odds_path, {"recv_ns": recv_ns, "recv_iso": now_iso(),
                                               "clock": disp, **od})
                for d in comp.get("details", []) or []:
                    k = ae.detail_key(d)
                    if k in seen:
                        continue
                    seen.add(k)
                    kind = ae.classify(d)
                    coin = ae.coin_for(d, cfg)
                    ath = (d.get("athletesInvolved") or [{}])[0].get("displayName", "")
                    ae.write_jsonl(events_path, {
                        "mark_ns": recv_ns, "mark_iso": now_iso(), "coin": coin, "event": kind,
                        "note": f"{(d.get('type',{}) or {}).get('text','')} {ath}".strip(),
                        "feed_delay_s": 0, "source": "espn",
                        "match_clock": (d.get("clock", {}) or {}).get("displayValue"),
                        "team_id": (d.get("team", {}) or {}).get("id"),
                        "penaltyKick": d.get("penaltyKick", False), "redCard": d.get("redCard", False),
                    })
                    log(f">> EVENT {kind} coin={coin} {ath} @{(d.get('clock',{}) or {}).get('displayValue')}")

            # lifecycle
            kick_ts = kickoff.timestamp() if kickoff else None
            decision = lifecycle_decision(now, kick_ts, state, post_since,
                                          pre_s * 60, buffer_s, max_s, force)
            if decision == "active" and not active.is_set():
                log("entering ACTIVE window — book logging on")
                active.set()
            if decision == "stop" or (now - started) > max_s:
                if (now - started) > max_s:
                    log("max duration hit; stopping")
                stop.set()
                active.set()  # release book logger if it was still waiting
                break
        except Exception as e:
            log(f"espn: poll error {e!r}")
        await asyncio.sleep(interval)


async def amain(args):
    with open(args.config) as f:
        cfg = json.load(f)
    if "espn" not in cfg:
        raise SystemExit("config.json needs an 'espn' block.")
    os.makedirs(args.outdir, exist_ok=True)
    writer = Writer(args.outdir)
    active, stop = asyncio.Event(), asyncio.Event()
    logfile = open(os.path.join(args.outdir, "run.log"), "a", buffering=1)

    def log(line):
        s = f"{now_iso()}  {line}"
        print(s, flush=True)
        logfile.write(s + "\n")

    log(f"=== Phase 0 orchestrator start; coins={cfg['coins']} ===")
    if not args.force_start:
        log("waiting for kickoff window... (safe to leave running; come back to data/REPORT.txt)")

    tasks = [
        asyncio.create_task(book_logger(cfg["coins"], writer, active, stop, log)),
        asyncio.create_task(espn_poller(cfg, args.outdir, args.interval, args.pre,
                                        args.buffer, args.max_hours * 3600,
                                        args.force_start, active, stop, log)),
    ]
    await stop.wait()
    await asyncio.sleep(1)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer.close()

    log("running analysis...")
    report = os.path.join(args.outdir, "REPORT.txt")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(report, "w") as rf:
        proc = subprocess.run([sys.executable, os.path.join(here, "analyze.py"),
                               "--data", args.outdir],
                              stdout=rf, stderr=subprocess.STDOUT)
    log(f"=== DONE. Report written to {report} (exit {proc.returncode}) ===")
    logfile.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--interval", type=float, default=3.0, help="ESPN poll seconds")
    ap.add_argument("--pre", type=float, default=15.0, help="minutes before kickoff to start logging")
    ap.add_argument("--buffer", type=float, default=180.0, help="seconds to log after full time")
    ap.add_argument("--max-hours", type=float, default=5.0, help="hard safety stop")
    ap.add_argument("--force-start", action="store_true", help="log now, skip the wait")
    args = ap.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
