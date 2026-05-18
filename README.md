# btc-brain-ops

Parallel survivability intelligence system for BTC-Brain.

**Phase 1: OBSERVE ONLY.**

btc-brain-ops sits alongside the production trading bot as a silent observer.
It does not block signals, modify execution, or interfere with the production system in any way.

---

## Doctrine

> Structure gives directional permission.
> Participation gives continuation permission.
> Persistence gives survivability proof.

---

## What it does (Phase 1)

- **Log signals** received from the production bot
- **Track lifecycle** — how continuation quality evolves after entry
- **Classify continuation states** at each lifecycle checkpoint
- **Monitor participation persistence** — OI, volume, follow-through
- **Detect continuation decay** — when and how quality degrades
- **Export weekly intelligence** — markdown + Obsidian-compatible summaries

## What it does NOT do (Phase 1)

- Block signals
- Modify execution
- Predict direction
- Replace ChatGPT analysis
- Interfere with Telegram alerts
- Replace the production bot

---

## Architecture

```
Production Bot (btc_alert_bot_v7.py)
        │
        │  POST /signals/ingest
        ▼
btc-brain-ops (FastAPI + SQLite)
        │
        ├── Signal ingested
        ├── Initial continuation state classified
        ├── Lifecycle events appended (manual / future automation)
        ├── State path tracked: healthy → weakening → false_recovery → exhausted
        │
        └── Weekly export generated (Saturday)
                ├── markdown → ChatGPT analysis
                └── obsidian → BTC-Brain wiki vault
```

---

## Continuation States

| State | Survivability | Description |
|-------|--------------|-------------|
| healthy | 5/5 | Participation expanding, volatility accepted, follow-through persisting |
| recovering | 4/5 | Participation returning after weakness |
| weakening | 3/5 | Participation slowing, follow-through thinning |
| unstable_transition | 2/5 | Regime shifting, structure and participation disagree |
| false_recovery | 1/5 | Structure improved, participation absent |
| decaying | 1/5 | Price moving, market commitment not compounding |
| exhausted | 0/5 | Volatility spiked, continuation failed |
| trapped | 0/5 | Expansion punished one-sided participation |

---

## Continuation Half-Life

How long does continuation quality persist after signal activation?

| Half-Life | Meaning |
|-----------|---------|
| long | Quality persisted through most of the trade |
| moderate | Quality held for ~half the trade duration |
| short | Quality decayed within a few candles |
| immediate | Decay at or before entry |

---

## API Endpoints

### Signal ingestion
```
POST /signals/ingest           — receive new signal from production bot
POST /signals/{id}/close       — record trade outcome (WIN/LOSS)
POST /signals/{id}/lifecycle   — append a lifecycle observation
GET  /signals/                 — list signals (filter by week/direction/result)
GET  /signals/{id}             — get signal + full lifecycle history
```

### Weekly intelligence
```
POST /weekly/{week}/generate   — generate weekly markdown export
GET  /weekly/{week}            — get cached export
GET  /weekly/{week}/obsidian   — get Obsidian-formatted export
GET  /weekly/                  — list all exported weeks
```

### Health
```
GET /                          — system info
GET /health                    — health check
```

---

## Setup

### Local development

```bash
git clone https://github.com/yourname/btc-brain-ops
cd btc-brain-ops

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env

uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for interactive API docs.

### Run tests

```bash
pytest tests/ -v
```

### Railway deployment

1. Push to GitHub
2. Create new Railway project → Deploy from GitHub repo
3. Set environment variables in Railway dashboard:
   - `DB_PATH` — use `/data/btc_brain_ops.db` if using a Railway volume, or leave as default
4. Add a Railway Volume mounted at `/data` for persistent SQLite storage (recommended)
5. Deploy

---

## Weekly workflow integration

### Saturday (after production bot review)

1. Generate weekly export:
   ```
   POST /weekly/W20/generate
   ```
2. Fetch markdown:
   ```
   GET /weekly/W20
   ```
3. Paste markdown into ChatGPT alongside the Telegram log for analysis

### Sunday

1. Fetch Obsidian export:
   ```
   GET /weekly/W20/obsidian
   ```
2. Paste into BTC-Brain vault as `Week-W20-ops-summary.md`
3. Links automatically connect to existing wiki nodes

---

## Signal ingestion — production bot integration

Add this call to the production bot after each signal is sent:

```python
import httpx

def report_signal_to_ops(direction, entry_price, tp_price, sl_price, session, week,
                          regime=None, oi_state=None, rsi=None, trend_4h=None):
    try:
        httpx.post("https://your-btc-brain-ops.railway.app/signals/ingest", json={
            "week": week,
            "direction": direction,
            "entry_price": entry_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "session": session,
            "regime": regime,
            "oi_state": oi_state,
            "rsi_at_entry": rsi,
            "trend_4h": trend_4h,
        }, timeout=5)
    except Exception:
        pass  # Never let ops reporting break production
```

---

## Phase 2 (future — do not build yet)

- Automated lifecycle polling (fetch OI/volume data and append events automatically)
- Participation divergence detector
- Regime confidence scoring
- Historical pattern matching
- Real-time survivability dashboard

---

## File structure

```
btc-brain-ops/
├── app/
│   ├── main.py                          — FastAPI app entry point
│   ├── api/
│   │   ├── signals.py                   — signal ingestion + lifecycle API
│   │   └── weekly.py                    — weekly export API
│   ├── database/
│   │   ├── models.py                    — Signal, LifecycleEvent, WeeklyExport
│   │   └── engine.py                    — SQLite engine + session
│   ├── continuation_state_logger/
│   │   └── classifier.py               — continuation state classifier
│   ├── signal_lifecycle_tracker/
│   │   └── tracker.py                  — lifecycle analysis + half-life
│   ├── weekly_intelligence_exporter/
│   │   └── exporter.py                 — weekly markdown generator
│   └── obsidian_export/
│       └── formatter.py                — Obsidian wiki link formatter
├── tests/
│   └── test_classifier.py              — classifier smoke tests
├── requirements.txt
├── Procfile
├── railway.json
├── .env.example
└── README.md
```
