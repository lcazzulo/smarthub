# Heating UI

React + TypeScript + Vite. Four room tabs show external temperature, reported
target, requested opening, operating status and collapsible diagnostics.
Target controls use HTTP; current state arrives over a native WebSocket, without
polling. HTTP acceptance is not confirmation: the UI waits for a subsequent,
non-retained controller target report. MQTT does not correlate request IDs, so
this confirms the reported value, not which client caused it.

## Development

With Node 24 and the Flask backend listening on localhost:5055:

```sh
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies `/api` and `/ws` to Flask while preserving
the original Host and Origin. Set `HEATING_BACKEND_URL` to override the backend.

If Node is unavailable on the host, run from this directory:

```sh
docker run --rm --network host --user "$(id -u):$(id -g)" \
  -e npm_config_cache=/tmp/npm-cache -v "$PWD:/app" -w /app \
  node:24-bookworm sh -c 'npm ci && npm run dev'
```

The development server binds to loopback. For remote access, use an SSH tunnel
(`ssh -L 5173:127.0.0.1:5173 HOST`) and open the local address. Production nginx
and Docker deployment are separate work; no service is deployed by this scaffold.

## Checks

```sh
npm test
npm run build
```

Tests use simulated transport and never change live valve targets. `dist/`
contains the production bundle. Configure nginx to serve it with an SPA fallback
and proxy `/api` and `/ws` (including WebSocket upgrade) to Flask.

## Temperature history

The selected room shows external temperature and a stepped target line from the
recorded control samples. Choose 1h, 6h, 24h or 7d; the duration is preserved when
switching rooms. All timestamps use Europe/Rome, with UTC offsets in sample
inspection to distinguish repeated daylight-saving hours.

History is fetched on room/range changes, Refresh, resuming Follow live and
reconnection. All pages use fixed query boundaries. No polling is performed.
Follow live appends fresh room telemetry, using its backend receipt timestamp;
it does not claim that a live point has already been recorded in SQLite. Target
changes appear on the chart with the next control telemetry sample, while the
current target control can update sooner from a target report. Refresh reconciles
the chart with recordings. Turn off Follow live to hold the displayed data still.

Controller restarts, recording gaps, stale/null temperature readings and live
connection interruptions break the lines. Live data starts a separate segment
because telemetry does not identify its recording run. Long ranges are reduced
for rendering while preserving extrema and gap boundaries; sample inspection
uses the original points. At most 250,000 samples are loaded per request range;
a clear error asks for a shorter range if this limit is exceeded.

Hover/tap the chart or use the keyboard-accessible sample slider for exact values.
Humidity and the other planned charts remain future work.

## Valve demand history

The valve demand chart shares the temperature chart's range, Follow live control,
refresh and sample inspection cursor. It plots requested opening as a stepped
0–100% line from recorded control samples and live telemetry. It reuses the same
history request, without extra polling. This is requested demand, not confirmed
physical position, water flow or delivered heat.

A status strip and background shading show active control, outside supply hours,
outside heating schedule and other states. Exact status, recorded supply
availability and dry-run mode appear in sample inspection. Gaps remain unshaded.
Live supply availability is unknown unless the reported status explicitly says
outside supply; the frontend does not infer boiler availability. Reduction keeps
opening extrema, missing values and status transitions.

## Expandable PI diagnostics

Expand PI diagnostics below valve demand to see P and I contributions on a shared
signed scale with a zero baseline. Values are percentage-point contributions;
negative values and values above 100 are not clipped. The panel is collapsed by
default and resets when switching rooms. It shares history requests, range,
Follow live and inspection cursor with the other charts, using recorded and live
proportional/integral fields. Missing contributions remain gaps, not zeroes.
Downsampling preserves PI extrema and missing-data boundaries. Requested opening
can differ from P + I because of output limits and control schedules.
