# NeuroCool — Backend

FastAPI service that runs the cluster digital twin and every engine on top of it.
See the [root README](../README.md) for the project overview and screenshots.

## Quick start

```bash
docker compose up --build        # or: podman compose up --build
```

Starts PostgreSQL, Redis, and the API (migrations run on entrypoint). API at
`http://localhost:8000`, docs at `http://localhost:8000/docs`.

No `.env` file is required — `docker-compose.yml` ships safe defaults. Copy
`.env.example` to `.env` to override the database password, add AI provider keys,
or tune timeouts.

## Without containers

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export POSTGRES_HOST=localhost REDIS_HOST=localhost   # point at your own services
alembic upgrade head
uvicorn app.main:app --reload
```

## Architecture

`app.main` builds the FastAPI app and, in its lifespan handler, creates a single
long-lived `SimulationService` (stored on `app.state`, not a module global) plus a
`NeuroCoreService`. The simulation is **initialized but not started** — the app always
boots into `IDLE`; a human starts the tick loop via `POST /api/simulation/start`.

`SimulationService` (`app/simulation/engine.py`) owns the tick loop and is the *only*
place that knows about more than one engine. Each tick, in order:

1. **Scenario engine** (`app/simulation/scenario_manager.py`) supplies this tick's
   per-rack drivers for the active scenario.
2. **Execution engine** (`app/execution/`) supplies remediation drivers for any
   in-flight executed decision.
3. **Physics** (`app/simulation/physics.py`) — pure, seedable math — folds all drivers
   into the next rack state (workload → power → heat → fan/cooling feedback →
   temperature → health → prediction → status).
4. **Forecasting engine** (`app/forecasting/`) ingests the post-physics racks and
   refreshes predictions.
5. **Optimization engine** (`app/optimization/`) generates, scores, and selects
   candidate plans.
6. **Decision engine** (`app/ai/`) evaluates the cluster (with the fresh forecast as a
   plain argument) and returns lifecycle events.
7. Significant transitions become durable `Event` rows and a `TelemetrySnapshot` is
   broadcast over `/ws/telemetry`.

Every engine is behind a contract (`app/*/base.py`) and injected into its service at
construction, so swapping `RuleBasedDecisionEngine`, `TrendForecastEngine`, or
`SimulationOptimizer` for something smarter changes one `engine=` argument and nothing
else.

### NeuroCore (the LLM layer)

`app/neurocore/` is independent of FastAPI — `app/api/ai.py` is a thin wrapper, the same
relationship every other engine has with its routes.

- **Providers** (`app/neurocore/providers/`): `anthropic`, `openai`, `gemini`, and a
  deterministic `mock`. Selection is config-driven (`AI_PROVIDER`); nothing outside this
  package imports a vendor SDK. If the selected provider has no API key,
  `build_provider_from_settings` returns `None`, `NeuroCoreService` runs provider-less,
  and `/api/ai/chat` reports a clear "unavailable" response — the rest of the backend is
  unaffected and still boots.
- **Grounding**: every chat turn is grounded in live cluster state before the model is
  called.
- **Tools** (`app/neurocore/tools/`): read tools run inline. A **write tool never
  executes inside a chat turn** — it only creates a `PendingAction`, and the turn returns
  its confirmation summary. The mutation happens later, only via an explicit
  `POST /api/ai/actions/{id}/confirm`.
- **Streaming**: `POST /api/ai/chat/stream` is an SSE endpoint — same tool loop and same
  confirmation boundary as the non-streaming path, surfaced as typed events. Exactly one
  assistant message is persisted per turn, win or lose.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | |
| `LOG_LEVEL` | `INFO` | |
| `BACKEND_CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated (also accepts a JSON array) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `neurocool` | |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `db` / `5432` | |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `redis` / `6379` / `0` | |
| `SIMULATION_TICK_SECONDS` | `1.0` | Recompute + broadcast interval |
| `AI_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `gemini` \| `mock` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-sonnet-5` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-flash-lite-latest` | Uses Google's `google-genai` SDK |
| `AI_REQUEST_TIMEOUT_SECONDS` | `30` | Per-LLM-call timeout |
| `AI_MAX_RESPONSE_TOKENS` | `800` | |
| `AI_TOOL_TIMEOUT_SECONDS` | `15` | Per tool call in a streamed turn |
| `AI_STREAM_TIMEOUT_SECONDS` | `60` | Hard ceiling on one streamed turn end-to-end |

## Migrations

```bash
alembic upgrade head            # apply
alembic revision --autogenerate -m "message"
alembic check                   # fail if models and migrations have drifted
```

## Tests

```bash
pytest                          # 319 tests, asyncio_mode=auto
pytest tests/test_physics.py -q
```

## Layout

```
app/
├── main.py            # app factory + lifespan (creates SimulationService, NeuroCoreService)
├── config.py          # pydantic-settings
├── api/               # thin route wrappers → services
├── websocket/         # /ws/telemetry broadcaster
├── simulation/        # engine.py (tick loop), physics.py, scenario_manager.py, seed.py
├── forecasting/       # service + TrendForecastEngine + history/risk/trend
├── optimization/      # service + SimulationOptimizer + planner/scoring/simulator
├── ai/                # DecisionService + RuleBasedDecisionEngine
├── execution/         # ExecutionService + manager (closed-loop remediation)
├── neurocore/         # LLM reasoning, grounding, tools, provider adapters
├── models/            # SQLAlchemy ORM
├── schemas/           # Pydantic request/response models
└── services/          # event_service (durable Event rows)
```
