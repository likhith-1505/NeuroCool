# NeuroCool — Frontend

React 19 + TypeScript + Vite single-page app for the NeuroCool platform.
See the [root README](../README.md) for the project overview and screenshots.

> **Run commands from the repository root, not from this folder.** The Vite project
> lives at the root (`index.html`, `vite.config.ts`, `package.json` are all there);
> this `Frontend/` directory only holds the app source.

## Quick start

```bash
# from the repo root
cp .env.example .env      # optional — defaults target http://localhost:8000
npm install
npm run dev               # Vite dev server on http://localhost:5173
```

With the [backend](../backend/README.md) running, open the app and press **Start** in the
simulation dock to bring the digital twin online.

## Scripts

| Command | Description |
| --- | --- |
| `npm run dev` | Start the Vite dev server (HMR) |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Serve the production build locally |

## Configuration

Only `VITE_`-prefixed variables reach client code (read in exactly one place —
`Frontend/lib/env.ts`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | `http://localhost:8000` | Backend REST base URL |
| `VITE_WS_URL` | derived from `VITE_API_URL` (`http`→`ws`, `https`→`wss`) | Backend WebSocket base URL |

## Workspaces

| Route | Workspace | What it shows |
| --- | --- | --- |
| `/mission-control` | Mission Control | Cluster overview, metrics ribbon, current recommendation, event timeline |
| `/digital-twin` | Digital Twin | Interactive pan/zoom rack graph with forecast overlays |
| `/analytics` | Analytics | Forward-looking forecast curves per rack (this backend keeps no trailing history — every chart plots the real forecast horizon anchored at the current reading) |
| `/ai-copilot` | AI Copilot | Streaming chat grounded in live state; proposed actions require explicit confirm |
| `/settings` | Settings | Theme, accent, motion, prediction interval, AI provider |

**Command palette:** <kbd>Ctrl</kbd>/<kbd>Cmd</kbd> + <kbd>K</kbd> — navigation, scenario
switching, replay, reset, search.

## How it talks to the backend

| Concern | Module | Notes |
| --- | --- | --- |
| REST | `lib/apiClient.ts` | One typed `request()` — no component calls `fetch()` directly |
| Live telemetry | `state/TelemetryContext.tsx` → `lib/wsClient.ts` | Subscribes to `/ws/telemetry`; every `TelemetrySnapshot` fans out through context |
| Streaming chat | `lib/sseClient.ts` | Consumes `POST /api/ai/chat/stream` (SSE) |
| Base URLs | `lib/env.ts` | The only reader of `import.meta.env` |

## Layout

```
Frontend/
├── main.tsx              # entry — Router + Settings/Telemetry/ScenarioEngine providers
├── App.tsx               # routes, command palette, workspace nav
├── AppLayout.tsx
├── MissionControlPage.tsx
├── pages/                # DigitalTwinWorkspace, AnalyticsWorkspace, AICopilotWorkspace, SettingsWorkspace
├── components/           # CommandPalette, SimulationControl/Dock, ConnectionBadge, AnimatedValue, ...
├── scenario/             # ScenarioEngine (client-side scenario state)
├── settings/             # SettingsContext
├── state/                # TelemetryContext (WebSocket)
└── lib/                  # apiClient, wsClient, sseClient, env, types
```

## Stack

React 19 · TypeScript · Vite 8 · Tailwind CSS 3 · Framer Motion · React Router 7
