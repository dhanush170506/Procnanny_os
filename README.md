# ProcNanny

ProcNanny monitors process CPU usage and exposes live snapshots and JSONL records
through a local FastAPI backend and React dashboard. Monitoring starts only when
requested. An optional Linux/Windows priority policy can gradually lower the
scheduling priority of eligible processes with sustained high CPU usage and
restore it after sustained recovery. It is disabled by default, and dry-run mode
is enabled by default.

## Requirements

- Python 3.11 or newer
- Node.js 20.19+ or 22.12+ and npm

## Install

From the repository root, create and activate a virtual environment, then
install the Python dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux/macOS, activate with `source .venv/bin/activate`.

Install frontend dependencies separately:

```powershell
cd frontend
npm install
cd ..
```

## Run both applications

Start the API from the repository root. It binds to loopback by default; keep it
local because the start/stop controls are not protected by user authentication.

```powershell
python -m uvicorn procnanny.api:app --host 127.0.0.1 --port 8000
```

In another terminal, run the dashboard:

```powershell
cd frontend
npm run dev
```

Open the Vite URL, usually `http://localhost:5173`. The dashboard fetches the
backend status, process snapshot, and recent logs, then refreshes at the
configured monitor interval. Use **Start monitoring** and **Stop monitoring**
to control observation. The Priority Management, Priority Management Logs, and Settings
pages are available from the sidebar or directly at
`http://localhost:5173/#priority-management` and
`http://localhost:5173/#priority-logs`. The first uses live process snapshots,
backend-generated policy evaluations, and recorded policy actions; the second
displays only priority-decision/action history and explains when no events have
been recorded. Settings is available at `http://localhost:5173/#settings`; it
edits and persists the validated configuration returned by the backend.
API docs are available at `http://127.0.0.1:8000/docs`.

The API permits browser requests only from the local Vite origins configured
in `procnanny/api.py`. To use another local frontend origin, update that allowlist.
The API does not implement user authentication; bind it to loopback (`127.0.0.1`)
and do not expose it to an untrusted network. `GET /api/settings` reads active
configuration and `PUT /api/settings` validates, atomically saves, and applies
configuration changes to the running monitor. Enabling live mode requires an
explicit confirmation field in addition to setting both adjustment enabled and
dry-run disabled. Endpoints include `GET /api/status`, `GET /api/settings`,
`PUT /api/settings`, `GET /api/processes`,
`GET /api/logs`, `GET /api/priority/actions`,
`GET /api/priority/evaluations`, `POST /api/monitor/start`, and
`POST /api/monitor/stop`. Priority evaluations are generated from the same
process observations as the process list and the endpoint returns the latest
bounded in-memory results (`limit` 1–1000); it does not trigger a second process
scan. The Priority Management page fetches up to 1000 decisions to cover larger
current process snapshots. The status endpoint reports configured high and low CPU thresholds as
well as monitoring and priority-adjustment settings. The Settings page displays
`cpu.high_threshold_percent`, `cpu.low_threshold_percent`,
`monitoring.poll_interval_seconds`, `monitoring.history_window_seconds`,
`monitoring.excluded_processes`, `priority_adjustment.enabled`,
`priority_adjustment.dry_run`, `priority_adjustment.cooldown_seconds`,
`priority_adjustment.high_duration_seconds`,
`priority_adjustment.recovery_duration_seconds`, and
`priority_adjustment.step`, as well as Unix nice-value bounds. Windows uses process priority classes instead
of those nice values. These settings are validated before being written;
invalid updates leave the previous configuration active. Values changed in the
Settings page are persisted to `config.yaml` and applied by the monitoring loop
on its next cycle, without restarting. Editing the YAML directly still requires
a backend restart. They are not overridden by environment variables.
`PROCNANNY_LOG_PATH` changes only the JSONL log location.

`priority_adjustment.enabled` controls whether a qualified policy decision may
produce a proposal/action; `priority_adjustment.dry_run` controls whether an
enabled adjustment is proposal-only or may call the OS priority API. Monitoring,
CPU threshold evaluation, sustained-high-duration tracking, and evaluation
logging continue while adjustment is disabled. To record proposals without
changing OS priorities, enable adjustment while leaving dry-run checked in the
Settings page. To enable live changes, enable adjustment, uncheck dry-run, and
select the explicit live-adjustment confirmation before saving. The API also
rejects a transition to live mode without that confirmation. Saving settings
never applies a process change by itself; policy decisions still require
eligible processes to meet the configured threshold and duration.

Process records keep the legacy `nice_value` field and also expose
`priority_value`, `priority_scheme`, and `priority_label`; `priority_scheme`
distinguishes Windows priority classes from Unix nice values. If the operating
system denies reading a process priority, `priority_unavailable_reason` reports
the cause without attempting privilege elevation or changing the process.

`GET /api/priority/actions` reads durable priority decision/action events from
that same JSONL file, newest first. It accepts `limit` (1–200, default 20),
`offset` (0–10000), `status` (`proposed`, `dry_run`, `applied`, `failed`,
`skipped`, or `excluded`), `process_name` (case-insensitive substring), and
`pid`. Records include an event ID, timestamp, CPU measurement when available,
priority values, action, status, reason, and error details when relevant.
Normal process-monitoring samples are not returned by this endpoint.

## Data and safety

- Live measurements and records are marked as backend data.
- If the backend cannot be reached, the dashboard shows an error and does not
  replace missing measurements with demo fixtures.
- To intentionally preview the mock UI, set `VITE_DEMO_MODE=true` in
  `frontend/.env.local`. Demo mode is opt-in and clearly labeled.
- Priority adjustment execution is supported on Linux and Windows and disabled
  by default. The default `priority_adjustment` configuration sets
  `enabled: false` and `dry_run: true`.
- Dry-run revalidates the sampled process identity and eligibility, records
  proposals, and never calls the OS priority setter. To allow actual adjustment,
  explicitly enable adjustment, disable dry-run, and acknowledge the live-mode
  confirmation on the Settings page (or make the corresponding explicit local
  YAML change and restart the backend). No root/admin privilege is requested;
  OS permission failures and priority read-back mismatches are reported as
  failed actions.
- The evaluator uses the existing rolling average. At or above
  `cpu.high_threshold_percent`, it waits for `high_duration_seconds` before
  proposing one demotion step. Below `cpu.low_threshold_percent`, it waits for
  `recovery_duration_seconds` before proposing one restoration step; values
  between the thresholds reset both timers. `cooldown_seconds` prevents a new
  proposal/action on every poll. Linux proposals are bounded by the configured
  nice minimum/maximum; Windows uses ordered process priority classes rather
  than interpreting those class values as Unix nice values. Names in
  `monitoring.excluded_processes` and critical system processes are never
  adjusted. Process creation time is rechecked before an OS change to guard
  against PID reuse.

Example configuration (safe defaults):

```yaml
priority_adjustment:
  enabled: false
  dry_run: true
  cooldown_seconds: 30
  high_duration_seconds: 15
  recovery_duration_seconds: 15
  step: 1
```

## Bounded Windows live-priority demonstration

Use this demonstration only with a local ProcNanny backend and a disposable,
ordinary-user process. The workload below runs for 40 seconds total: 18 seconds
of CPU work at about a 50% duty cycle, then 22 seconds idle so the recovery
policy can restore its original priority. It never spawns or terminates another
process.

1. Start the backend and frontend as described above. In **Settings**, set:
   high CPU threshold `10`, recovery threshold `2`, monitoring interval `1`,
   CPU history window `5`, sustained high duration `5`, recovery duration `5`,
   and cooldown `2`. Save with automatic adjustment enabled and **Dry-run only**
   still checked first if you want to review proposal-only behavior.
2. When ready for the live demonstration, go back to Settings, enable automatic
   adjustment, uncheck **Dry-run only**, check the explicit live-authorization
   confirmation, and save. Do not make live changes by editing YAML or calling
   the settings API directly.
3. Start monitoring, then launch the dedicated test workload from the project
   root in a separate PowerShell window:

   ```powershell
   & .\.venv\Scripts\python.exe .\tests\priority_demo_workload.py
   ```

   Copy the printed PID and original priority. Check the actual Windows class
   before, during, and after the run:

   ```powershell
   Get-Process -Id <PID> | Select-Object Id,ProcessName,PriorityClass
   ```

4. In **Priority management**, find that exact PID and observe its evaluation.
   In **Priority logs**, find the same PID and inspect the requested/resulting
   class and timestamp. The workload exits by itself; its idle phase is intended
   to trigger recovery before exit. Verify the final class matches the printed
   original priority. If Windows denies a change, the log must show a failed
   action; do not elevate privileges or retry against another process.
5. Stop monitoring and restore the normal safe configuration in Settings:
   adjustment disabled and dry-run checked. Verify the backend reports those
   settings before ending the session.

The timed workload is bounded independently of ProcNanny policy. If its PID
exits before recovery, no recovery action is possible; the log should retain the
observed action results, and ProcNanny must not attempt to act on a reused PID.

Safe Linux/WSL2 dry-run demonstration:

1. Keep `dry_run: true`. Temporarily set only
   `priority_adjustment.enabled: true` in `config.yaml`; do not set dry-run to
   false. The default 30-second rolling window, 70% high threshold, and
   15-second high duration mean the CPU workload should run for at least
   45 seconds to allow for process discovery and sustained-average detection.
2. Start the backend and dashboard using the commands above, start monitoring,
   then launch this bounded CPU workload in a third Linux/WSL2 terminal:

   ```sh
   python3 -c 'import time; end=time.monotonic()+45; exec("while time.monotonic() < end:\n    pass"); time.sleep(60)' &
   test_pid=$!
   ps -o pid,ni,comm -p "$test_pid"
   ```

   Leave monitoring running through the 45-second CPU phase and recovery period.
   Since the low threshold is applied to the 30-second rolling average and
   recovery must then remain sustained for 15 seconds, allow roughly 45 seconds
   after the busy phase for a restoration proposal. Watch the dashboard's Recent
   activity and query `http://127.0.0.1:8000/api/priority/actions`. Inspect the
   JSONL file selected by `PROCNANNY_LOG_PATH` (default `procnanny.log`) for
   `priority_demote` and `priority_restore` records.
3. Confirm the logged records have `dry_run: true` and `success: null`; check
   `ps -o pid,ni,comm -p "$test_pid"` again to verify its nice value did not
   change. Stop monitoring, terminate the workload if it has not exited with
   `kill "$test_pid"`, and restore `enabled: false` in `config.yaml`. The shipped
   default remains `enabled: false`, `dry_run: true`.

## Tests and build

From the repository root:

```powershell
python -m unittest discover -s tests -v
```

Ruff is available in the project virtual environment when installed:

```powershell
ruff check procnanny tests
```

No Mypy configuration or dependency is currently included.

From `frontend`:

```powershell
npm test
npm run build
```
