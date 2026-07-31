# AuraPilot Monitor Dashboard

Read-only Web dashboard for inspecting projects under `/nfs/project`.

The dashboard supports the six-stage IFLD view:

1. Initialize and validate.
2. Structure prediction.
3. Structure clustering.
4. Mutation library and scoring.
5. Refolding and candidate filtering.
6. Library design.

It also recognizes flat and run-scoped Partial de novo/BoltzGen projects,
including per-node Design, Inverse folding, Folding, Analysis, Filtering,
downstream errors, and final-library state.

The monitor reads existing AuraPilot/AureZoo JSON files and artifacts. It does
not launch, resume, cancel, or modify workflow jobs.

## Run

From the AuraPilot repository root:

```bash
python3 -m apps.monitor_dashboard.server \
  --host 127.0.0.1 \
  --port 8765 \
  --project-root /nfs/project \
  --server-id ln \
  --server-label LN
```

Open `http://127.0.0.1:8765`. For a remote server, keep the service bound to
localhost and use an SSH tunnel.

The unified UI expects LN on local port `8765` and Huoshan A800 on local port
`8766`. Each collector exposes read-only CORS-enabled APIs so a single page can
switch between both data sources or show their combined project list.

The dashboard refreshes the selected run every five seconds. It distinguishes
`complete`, `running`, `failed`, `blocked`, `degraded`, `reused`, `skipped`,
and `pending` states.

## Reported progress

Without an explicit report, the dashboard derives counts from workflow outputs
and labels them `Estimated from outputs`. A workflow can publish an atomic
`progress.json` at the run/project root or under `status/`,
`postprocess/status/`, or `state/`:

```bash
python3 scripts/progressctl.py \
  --path /nfs/project/example/postprocess/status/progress.json \
  start --workflow partial_denovo --node ln01 --attempt 2 \
  --stage developability_filter

python3 scripts/progressctl.py \
  --path /nfs/project/example/postprocess/status/progress.json \
  stage --stage af3_refolding --title "AF3 refolding" \
  --completed 126 --total 353
```

The reporter uses a lock plus same-directory atomic rename, records attempt
history, emits heartbeat timestamps, and calculates an ETA after consecutive
updates. Reported progress is preferred over filesystem inference. A running
report with no heartbeat for five minutes is shown as stalled.
