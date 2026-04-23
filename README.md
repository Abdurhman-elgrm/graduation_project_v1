# AI SOC Analyst Platform

A real-time Security Operations Centre (SOC) platform powered by Claude AI. Automatically ingests system logs, analyses each entry for threats using the Claude API, and generates full incident response playbooks for HIGH and CRITICAL findings.

---

## Features

- **Live log ingestion** — tails Linux syslog/auth.log, Windows Event Log, or generates synthetic events in DEMO MODE
- **AI threat analysis** — every log entry is analysed by Claude (criticality, threat type, IOCs, recommended actions)
- **Auto playbook generation** — HIGH/CRITICAL findings trigger a full Claude-generated incident response playbook
- **Real-time dashboard** — live WebSocket feed with colour-coded severity, analysis panel, and playbook tracker
- **Interactive playbooks** — step-by-step SOC runbooks with completion tracking, status management, and PDF export

---

## Prerequisites

- Python 3.11+
- A [Supabase](https://supabase.com) account (free tier works)
- An [Anthropic](https://console.anthropic.com) API key

---

## 1 — Supabase Setup

1. **Create a new Supabase project** at [supabase.com/dashboard](https://supabase.com/dashboard).

2. **Run the migration SQL**:
   - Go to **SQL Editor** in your Supabase dashboard
   - Open `supabase/migrations/001_init.sql`
   - Paste the entire contents and click **Run**

3. **Enable Realtime** on the tables (optional — used for future Supabase Realtime features):
   - Go to **Database → Replication**
   - Enable replication for: `log_entries`, `analysis_results`, `playbooks`, `playbook_steps`

4. **Copy your API keys**:
   - Go to **Settings → API**
   - Copy `Project URL`, `anon public` key, and `service_role` key

---

## 2 — Environment Setup

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
DEMO_MODE=true
```

---

## 3 — Install Dependencies

```bash
cd soc_analyst
pip install -r requirements.txt
```

> **Windows users**: If you want to read real Windows Event Logs (not needed in DEMO MODE), also run:
> ```bash
> pip install pywin32
> ```

---

## 4 — Run the Application

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser at: **http://localhost:8000**

---

## 5 — DEMO MODE

DEMO MODE generates realistic synthetic security log events (SSH brute force, privilege escalation, firewall blocks, etc.) every 2-3 seconds. No real log files are required.

**Enable it** by setting in `.env`:
```env
DEMO_MODE=true
```

**Disable it** for production by setting:
```env
DEMO_MODE=false
```

When DEMO MODE is disabled, the platform automatically detects your OS:
- **Linux** → tails `/var/log/syslog` and `/var/log/auth.log`
- **Windows** → reads Application, Security, and System Event Logs via `pywin32`
- **macOS / other** → attempts `/var/log/system.log`, falls back to DEMO MODE

---

## 6 — Adding New Log Sources

### Linux: add more files to tail

Edit `app/log_tailer.py`, find the `start_log_tailer` function, and add your file to the `log_files` list:

```python
log_files = [
    "/var/log/syslog",
    "/var/log/auth.log",
    "/var/log/nginx/access.log",   # add this
    "/var/log/apache2/error.log",  # or this
]
```

### Custom parser

Add a new parser function following the pattern of `parse_syslog_line()` and route to it based on `source_file`.

### Push logs via API

You can also POST log entries directly:

```bash
curl -X POST http://localhost:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"source_file": "custom.log", "raw_message": "...", "timestamp": "2024-04-23T10:00:00Z"}'
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/api/logs` | List logs (supports `?page`, `?limit`, `?criticality`, `?source`) |
| GET    | `/api/logs/{id}` | Single log with analysis |
| POST   | `/api/logs/{id}/reanalyze` | Re-queue AI analysis |
| GET    | `/api/analysis` | List analysis results (supports `?criticality`) |
| GET    | `/api/analysis/stats` | Dashboard statistics |
| GET    | `/api/playbooks` | List playbooks (supports `?status`) |
| GET    | `/api/playbooks/{id}` | Single playbook with steps |
| PATCH  | `/api/playbooks/{id}/status` | Update playbook status |
| PATCH  | `/api/playbooks/{id}/steps/{step_id}` | Toggle step completion |
| WS     | `/ws/live` | Real-time event stream |
| GET    | `/health` | Health check |

---

## WebSocket Event Types

```json
{ "type": "log_ingested",    "data": { ... } }
{ "type": "analysis_ready",  "data": { ... } }
{ "type": "playbook_created","data": { ... } }
{ "type": "step_completed",  "data": { ... } }
{ "type": "playbook_updated","data": { ... } }
```

---

## Project Structure

```
soc_analyst/
├── main.py                      # FastAPI app, startup, WebSocket route
├── requirements.txt
├── .env.example
├── supabase/
│   └── migrations/
│       └── 001_init.sql         # Full DB schema
├── app/
│   ├── config.py                # Env vars + Supabase client
│   ├── log_tailer.py            # Async log ingestion + DEMO MODE
│   ├── ai_analyzer.py           # Claude threat analysis engine
│   ├── playbook_generator.py    # Claude playbook generator
│   ├── routes/
│   │   ├── logs.py              # /api/logs endpoints
│   │   ├── analysis.py          # /api/analysis endpoints
│   │   └── playbooks.py         # /api/playbooks endpoints
│   └── websocket/
│       └── manager.py           # WebSocket connection manager
└── templates/
    ├── dashboard.html            # Live SOC dashboard (single-page)
    └── playbook_detail.html      # Playbook detail + step tracker
```
