# Production deployment

From `/home/luca/smarthub`:

```sh
docker compose config --quiet
docker compose up -d --build --no-deps heating-user-backend heating-user-ui
docker compose ps heating-user-backend heating-user-ui
docker compose logs --tail 100 heating-user-backend heating-user-ui
```

The production UI serves a compiled Vite bundle through nginx at
`http://127.0.0.1:5174`. It proxies `/api/` and `/ws` to the private backend
container. The backend uses one Gunicorn worker with 24 threads and connects to
Mosquitto on the Compose network. Both services restart unless explicitly stopped.
There is no separate published backend port. The existing development processes
on 5173/5055 are independent.

The controller YAML and entire SQLite directory (including WAL/SHM files) are
mounted read-only. UID/GID 1000 must retain read/traverse access to these host
paths. Do not use SQLite immutable mode while the controller is writing.
If broker credentials are enabled later, pass the environment variable names
referenced in the controller YAML to the backend service without committing secrets.

## HTTPS

Host nginx should proxy the selected HTTPS hostname to `127.0.0.1:5174`, retaining
Host and forwarding WebSocket Upgrade/Connection headers with a long timeout.
The existing wildcard certificate covers subdomains of luca-cazzulo.me.
Set `HEATING_APP_ORIGIN=https://YOUR-HOSTNAME` in the root untracked `.env` and
recreate the backend. Origin validation checks this exact HTTPS origin; it is
not authentication. Apply the chosen authentication/access policy to the entire
HTTPS server, covering static files, HTTP API and WebSockets together.
Validate with `sudo nginx -t` before `sudo systemctl reload nginx`.

## Verification and rollback

Check `/api/v1/health`, room state, a history query and a read-only WebSocket
subscription through the production UI. Health checks confirm HTTP availability;
the JSON health response separately reports MQTT/controller connectivity.
Never change real targets merely to validate deployment.

To stop only this application:

```sh
docker compose stop heating-user-ui heating-user-backend
```

This leaves the heating controller, Mosquitto and SQLite writer running. For a
rollback, restore the previous app source/images and recreate only these two
services. A frontend rebuild is required for source changes in production;
Vite hot reload remains available separately on development port 5173.
