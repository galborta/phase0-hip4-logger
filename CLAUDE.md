# CLAUDE.md — Project state & resume guide

**Read this first when re-entering this folder.** It is the single source of
truth for the HIP-4 World Cup trading project so a fresh context window can
continue without re-deriving anything.

---

## What this project is

Testing whether a program can trade **outcome markets on Hyperliquid HIP-4**
during the 2026 World Cup by reacting to live match events. The big idea the
user (Val) is chasing: HIP-4 is an order book, so **it never suspends** — unlike
sportsbooks, which freeze betting during penalties/red cards. That non-suspension
is a potential edge (Val's friend got filled on a PSG win right before a penalty).

The honest core finding (see `../worldcup_hip4_trading_viability.md` for the full
viability memo): the naive "point computer vision at the broadcast and trade what
you see" idea is **not viable** — a normal stream is 10–60s behind, and the
people pricing these books work off sub-second official feeds (Stats Perform is
the FIFA WC 2026 data distributor). A real edge requires either being near the
front of the latency stack or trading something that isn't a latency race
(modeling fair probability and trading a lazy/thin book maker-first).

**We are currently in Phase 0: measure, with zero capital, whether the edge
exists before building anything.**

---

## Phase 0 — what's running RIGHT NOW

A read-only harness is capturing the **tournament opener** to measure two things:
1. **Liquidity** — spreads/depth on the HIP-4 match books.
2. **Book staleness** — when a goal/card happens, how many seconds the HIP-4 book
   stays stale before repricing (the exploitable window), measured as a SIGNED
   offset vs the public ESPN feed.

### The match being captured
- **Mexico vs South Africa**, the World Cup opener
- **Kickoff: 2026-06-11 19:00 UTC (3:00 PM ET)**, Estadio Azteca
- ESPN event id: `760415` (league slug `fifa.world`)
- HIP-4 coins (Yes side, merged book): `#2480` = Mexico win, `#2490` = Draw,
  `#2500` = South Africa win
- ESPN team ids: Mexico `203`, South Africa `467`

### Where it runs
- **VPS (Hostinger):** `root@187.124.247.88` (hostname `srv1545664`),
  Ubuntu 24.04, project at `~/phase0-hip4-logger`
- Running as a **systemd service** named `phase0` (reboot-proof, auto-starts on
  boot). If only the nohup fallback was used, it survives logout but not reboot.
- **GitHub (private):** https://github.com/galborta/phase0-hip4-logger
- **Local copy (this folder):** the working source + docs

### Health checks (run on the VPS)
```bash
systemctl is-active phase0           # should print: active
tail -f data/run.log                 # should show "waiting for kickoff" then events
wc -l data/l2book_*.jsonl            # should climb once the match starts
```

---

## TOMORROW (after full-time) — how to resume

1. SSH to the VPS and grab the result:
   ```bash
   cd ~/phase0-hip4-logger
   cat data/REPORT.txt
   ```
   Optionally copy it back into this local folder under `results/`:
   ```bash
   scp root@187.124.247.88:~/phase0-hip4-logger/data/REPORT.txt ./results/
   scp root@187.124.247.88:~/phase0-hip4-logger/data/run.log    ./results/
   ```
2. Paste `REPORT.txt` to Claude. We read the **signed OFFSET** per event:
   - **positive** (e.g. `+12s`) → book LAGGED the feed = real exploitable
     staleness window. Combined with workable spreads, this is the **Phase 0 GO**.
   - **negative** (e.g. `−8s`) → book moved BEFORE ESPN reported = the free feed
     is too slow to be the trigger; we'd need a faster signal. Still a valid
     finding for $0.
3. Decide next step based on the verdict (see roadmap below).

---

## Files in this project

| File | Purpose |
|---|---|
| `run_phase0.py` | **Main orchestrator.** One process: waits for kickoff, logs book, auto-records events+odds, detects full-time, runs analysis, writes REPORT.txt |
| `run_on_vps.sh` | Detached launcher (nohup) for a VPS |
| `install_service.sh` | Installs the reboot-proof systemd `phase0` service |
| `run_opener.command` | macOS double-click launcher (laptop alternative) |
| `logger.py` | Read-only HL WebSocket logger (l2Book + trades) |
| `auto_events.py` | Automated ESPN event feed → events.jsonl + live odds.jsonl |
| `mark_event.py` | Manual event marker (fallback; catches penalty-AWARDED window ESPN misses) |
| `analyze.py` | Liquidity + signed book-reaction offset; the Phase 0 gate |
| `discover_markets.py` | Enumerate HIP-4 outcome markets / find coin names |
| `config.json` | Coins + ESPN event/team→coin mapping (currently the opener) |
| `data/` | Runtime output (gitignored): l2book_*.jsonl, trades_*.jsonl, events.jsonl, odds.jsonl, run.log, REPORT.txt |

Key reference doc (one folder up): `../worldcup_hip4_trading_viability.md`.

---

## Key technical facts (so we don't re-research)

- HL REST info: `POST https://api.hyperliquid.xyz/info`; WS:
  `wss://api.hyperliquid.xyz/ws`. Outcome book/trade subscriptions use coin
  name `#<encoding>` where `encoding = 10*outcome + side` (sides 0/1). Asset id
  `= 100_000_000 + encoding`. Discover markets via `{"type":"outcomeMeta"}`.
- HIP-4 contracts settle in [0,1] (= implied probability), no leverage, no
  liquidations, merged YES/NO order book, USDC quote. Sports markets resolve on
  the official final result; validators run the settlement.
- ESPN free API (no key): `https://site.api.espn.com/apis/site/v2/sports/soccer/
  {league}/scoreboard?dates=YYYYMMDD`. Live events are in
  `competitions[0].details[]` (type.text, clock, team.id, flags
  scoringPlay/redCard/penaltyKick). Also carries live DraftKings moneyline.
  Limitation: penalties appear only AFTER the kick ("Penalty - Scored"), so the
  pre-kick award window is invisible to the auto feed.

---

## Roadmap (phases, each with a kill-gate)

- **Phase 0 (now):** instrument + measure. GO if depth is workable AND a
  positive staleness offset exists after events. NO-GO if spreads swamp any edge
  or the book leads the feed.
- **Phase 1:** build a live win/draw/loss probability model + a consensus oracle
  from external markets (Polymarket/Kalshi/Betfair/DraftKings). Output a fair
  price vs the HIP-4 book.
- **Phase 2:** paper-trade the full loop against the live book; measure simulated
  edge net of fees+slippage. Gate before any real money.
- **Phase 3:** tiny live capital, maker-first, hard caps. Compare live fills to
  paper (the gap = adverse-selection cost).
- **Phase 4:** scale within depth limits; only then consider a faster paid feed.

**Legal note carried forward:** automated trading on event-outcome contracts is
contested and jurisdiction-dependent. Confirm with a lawyer before live capital.

---

## Decisions already made
- Capture the **opener** first (max liquidity), but ideally log other matches too
  for sample size.
- Automate via the **free ESPN feed** for now (vs paid fast feed / order-book
  self-detection / manual). Accept its latency; the analyzer reports a signed
  offset so we still learn the truth.
- Run on the **VPS, not the laptop**, for reliability; reboot-proof via systemd.
