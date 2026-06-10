# Phase 0 — HIP-4 Market Instrumentation (read-only)

The goal of Phase 0 is to answer, with real data and **zero capital at risk**,
the one question the whole project hinges on:

> On real World Cup HIP-4 markets, is there enough depth and enough book
> *staleness* after events that a modeled edge could survive the spread?

Nothing here signs a transaction or places an order. It only reads public
Hyperliquid market data over the WebSocket/REST APIs.

## What's measured

1. **Liquidity** — median spread, spread as % of mid, resting depth near top of
   book, and how often the book updates. This sets the *edge gate*: your model
   must beat roughly half the spread per side just to trade at mid.
2. **Staleness** — when a discontinuous event happens (penalty, red card, VAR),
   how many seconds the HIP-4 book sits unmoved before it reprices. This is the
   "non-suspension edge" — order books don't pause when sportsbooks do. We
   quantify the window in seconds.
3. **Activity** — update/trade frequency, useful for sizing and execution design.

## Setup

```bash
cd phase0_hip4_logger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run order

**1. Find the markets.** During a match (or before one), list outcome markets and
locate the ones you want:

```bash
python discover_markets.py --grep psg      # or a team / "world cup" / match name
python discover_markets.py --json meta.json # dump everything to inspect
```

Copy the coin name(s) — format `#<encoding>` — into `config.json`
(`cp config.example.json config.json` first).

**2. Start the logger** (leave it running for the whole match):

```bash
python logger.py --outdir data
```

**3a. Start the AUTOMATED event feed** (second terminal, hands-free — recommended):

```bash
python auto_events.py --config config.json --outdir data
```

This polls ESPN's free API and writes goals / cards / penalties to
`data/events.jsonl` automatically, plus live DraftKings moneyline to
`data/odds.jsonl`. The event id and team→coin map live in `config.json`'s `espn`
block. Two honest limits: ESPN reports a penalty only *after* the kick (it misses
the pre-kick award window), and its publish latency is unknown — which is why the
analyzer reports a SIGNED offset (see below). To smoke-test the feed any time:
`python auto_events.py --config config.json --once`.

**3b. (Optional) Also mark by hand** for the events ESPN can't see early —
specifically a penalty the instant it's *awarded*. Run in a third terminal off
the fastest feed you have:

```bash
python mark_event.py --coin "#2480" --feed-delay 8
# type:  pen   the instant the ref points to the spot
```

Use both: the auto feed for coverage, the human marker for the pre-kick penalty
window that's the real prize.

**4. After the match, analyze:**

```bash
python analyze.py --data data
```

## Caveat that decides whether the staleness number is real

The staleness window is only trustworthy if your reference feed is genuinely
near real time. If you mark a penalty off a 40-second stream and the book
already moved 35 seconds ago (because faster players saw it), the tool will
report a tiny or negative window — correctly telling you the edge isn't there
*for you* at that latency. A clean way to get an honest reference: mark off the
*fastest* source you can get, and cross-check event times against a separate
low-latency score source. If staleness windows are consistently large even off a
fast feed, that's a real, exploitable signal. If they vanish once your feed is
fast, the "edge" was just your own lag.

## The Phase 0 gate

- **GO** → spreads are tight relative to a plausible model edge AND a measurable
  staleness window exists after events. Proceed to Phase 1 (build the model).
- **NO-GO** → spreads swamp any plausible edge, or the book reprices effectively
  instantly. Stop here, cheaply, having risked nothing.

## Files

| File | Purpose |
|---|---|
| `discover_markets.py` | Enumerate HIP-4 outcome markets, find coin names |
| `logger.py` | Read-only WebSocket logger (book + trades), with reconnect |
| `auto_events.py` | Automated ESPN event feed → events.jsonl + live odds.jsonl |
| `mark_event.py` | Manual fallback: timestamp events (e.g. penalty awarded) |
| `analyze.py` | Liquidity + signed book-reaction offset; the Phase 0 gate |
| `config.json` | Coins to log + ESPN event/team→coin mapping |

### Reading the OFFSET number (the key result)

For each event the analyzer prints a signed OFFSET = (first book reprice) −
(event timestamp):

- **positive** (e.g. `+12s`) → the book lagged the feed by 12s. That's a real,
  measurable staleness window you could trade into. The bigger and more
  consistent, the stronger the edge.
- **negative** (e.g. `−8s`) → the book moved *before* ESPN reported. ESPN is too
  slow to be your trigger; you'd need a faster signal (or a human eye) to beat
  the book. This is a finding, not a bug — it tells you the free feed can't be
  the trading clock even if the staleness itself is real.

## Notes / things to verify against live data

- `outcomeMeta` schema: the discover script prints it raw so you can confirm the
  exact field names before relying on titles. Adjust if Hyperliquid has changed it.
- Merged order book: YES and NO share one book. Logging side 0 is usually
  sufficient; log both (`#10` and `#11`) if you want to confirm the relationship.
- The logger records a high-resolution **local receive timestamp** per message —
  that local clock is what the staleness math depends on, so run the logger and
  `mark_event.py` on the same machine.
