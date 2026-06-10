#!/usr/bin/env python3
"""
Phase 0 — Step 3 (AUTOMATED): live event feed from ESPN's free soccer API.

Replaces the manual marker. Polls ESPN's scoreboard for one match and writes a
row to events.jsonl the instant a new goal / card / penalty appears in the feed,
stamped with the SAME time.time_ns() clock the order-book logger uses. Also
writes the live DraftKings moneyline to odds.jsonl as a free cross-market
reference (useful later for the consensus model).

No key required. Read-only.

HONEST LIMITATIONS (read these):
  * ESPN reports a penalty as "Penalty - Scored" AFTER the kick, not when it's
    awarded. The pre-kick window (the best edge) is invisible here. For that you
    still want a human marker or a faster feed.
  * ESPN's own publish latency is unknown and variable (seconds to tens of
    seconds). The analyzer reports the book's move time relative to ESPN
    detection as a SIGNED number: if the book moved BEFORE ESPN reported, you'll
    see a negative offset -- which is itself the finding ("ESPN is too slow to
    be the trigger; the book leads it").

Usage:
    python auto_events.py --config config.json --outdir data
    python auto_events.py --config config.json --once     # one poll, print, exit (debug)
    python auto_events.py --config config.json --interval 3
"""
import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

SB_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_scoreboard(league, date):
    url = SB_URL.format(league=league, date=date)
    req = urllib.request.Request(url, headers={"User-Agent": "phase0-logger/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def find_event(sb, event_id):
    for ev in sb.get("events", []):
        if str(ev.get("id")) == str(event_id):
            return ev
    return None


def detail_key(d):
    """Stable-ish dedupe key for a details[] item (no unique id is provided)."""
    t = d.get("type", {}) or {}
    clk = d.get("clock", {}) or {}
    team = d.get("team", {}) or {}
    ath = (d.get("athletesInvolved") or [{}])[0].get("id", "")
    return f"{t.get('id')}:{clk.get('value')}:{team.get('id')}:{ath}"


def classify(d):
    t = (d.get("type", {}) or {}).get("text", "")
    if d.get("redCard"):
        return "red_card"
    if d.get("penaltyKick"):
        return "penalty_scored" if d.get("scoringPlay") else "penalty_taken"
    if d.get("scoringPlay") or "Goal" in t:
        return "goal"
    if d.get("yellowCard"):
        return "yellow_card"
    return (t or "event").lower().replace(" ", "_")


def coin_for(d, cfg):
    """Pick the coin most affected by this event.
    Goal/penalty -> scoring team's win coin. Red card -> opponent's win coin."""
    espn = cfg["espn"]
    team_coin = {str(k): v for k, v in espn.get("team_coin", {}).items()}
    team_id = str((d.get("team", {}) or {}).get("id", ""))
    kind = classify(d)
    if kind == "red_card":
        # opponent benefits
        others = [c for tid, c in team_coin.items() if tid != team_id]
        return others[0] if others else team_coin.get(team_id)
    return team_coin.get(team_id)


def write_jsonl(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def extract_odds(ev):
    try:
        comp = ev["competitions"][0]
        odds = comp.get("odds") or []
        for o in odds:
            if not o:
                continue
            ml = o.get("moneyline") or {}
            home = ((ml.get("home") or {}).get("close") or (ml.get("home") or {}).get("open") or {}).get("odds")
            away = ((ml.get("away") or {}).get("close") or (ml.get("away") or {}).get("open") or {}).get("odds")
            draw = ((ml.get("draw") or {}).get("close") or (ml.get("draw") or {}).get("open") or {}).get("odds")
            return {"home_ml": home, "away_ml": away, "draw_ml": draw, "provider": (o.get("provider") or {}).get("name")}
    except Exception:
        pass
    return None


def run(cfg, outdir, interval, once):
    os.makedirs(outdir, exist_ok=True)
    espn = cfg["espn"]
    league, date, event_id = espn["league"], espn["date"], espn["event_id"]
    seen = set()
    events_path = os.path.join(outdir, "events.jsonl")
    odds_path = os.path.join(outdir, "odds.jsonl")
    last_status = None

    print(f"Auto-feed: league={league} date={date} event={event_id}. Polling every {interval}s. Ctrl-C to stop.")
    while True:
        try:
            sb = fetch_scoreboard(league, date)
            ev = find_event(sb, event_id)
            recv_ns = time.time_ns()
            if ev is None:
                print(f"{now_iso()}  event {event_id} not on scoreboard for {date} yet.")
            else:
                comp = ev["competitions"][0]
                status = (comp.get("status", {}) or {}).get("type", {}) or {}
                disp_clock = (comp.get("status", {}) or {}).get("displayClock", "")
                state = status.get("state")
                if state != last_status:
                    print(f"{now_iso()}  match state = {status.get('description')} ({disp_clock})")
                    last_status = state

                # live odds snapshot
                od = extract_odds(ev)
                if od:
                    write_jsonl(odds_path, {"recv_ns": recv_ns, "recv_iso": now_iso(),
                                            "clock": disp_clock, **od})

                # new key events
                for d in comp.get("details", []) or []:
                    k = detail_key(d)
                    if k in seen:
                        continue
                    seen.add(k)
                    kind = classify(d)
                    coin = coin_for(d, cfg)
                    ath = (d.get("athletesInvolved") or [{}])[0].get("displayName", "")
                    row = {
                        "mark_ns": recv_ns,
                        "mark_iso": now_iso(),
                        "coin": coin,
                        "event": kind,
                        "note": f"{(d.get('type',{}) or {}).get('text','')} {ath}".strip(),
                        "feed_delay_s": 0,            # unknown for ESPN; analyzer reports signed offset
                        "source": "espn",
                        "match_clock": (d.get("clock", {}) or {}).get("displayValue"),
                        "team_id": (d.get("team", {}) or {}).get("id"),
                        "penaltyKick": d.get("penaltyKick", False),
                        "redCard": d.get("redCard", False),
                    }
                    write_jsonl(events_path, row)
                    print(f"  >> EVENT {kind}  coin={coin}  {row['note']}  @{row['match_clock']}  ({row['mark_iso']})")

            if once:
                if ev:
                    print(json.dumps(ev["competitions"][0].get("details", []), indent=2)[:4000])
                return
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:
            print(f"{now_iso()}  poll error: {e!r}; retrying in {interval}s")
            time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    if "espn" not in cfg:
        raise SystemExit("config.json needs an 'espn' block (see config.example.json).")
    run(cfg, args.outdir, args.interval, args.once)


if __name__ == "__main__":
    main()
