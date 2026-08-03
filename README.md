# AuraPilot Monitor

Standalone, read-only Web dashboard for monitoring AuraPilot, AureZoo, IFLD,
and Partial de novo/BoltzGen workflow outputs. The monitor reads existing
project directories; it never launches, resumes, cancels, or modifies jobs.

## Supported workflows

- Six-stage IFLD execution, errors, logs, progress, and canonical outputs.
- Dedicated IFLD final-deliverables group for
  `outputs/output/library_design/*_library_design_deliverables`.
- Flat and run-scoped Partial de novo/BoltzGen projects.
- Per-node Design, Inverse folding, Folding, Analysis, and Filtering progress.
- Downstream screening shards, aggregation, and final-library state.
- Multiple read-only collectors displayed through one browser page.

The code has no runtime dependency on the AuraPilot or AureZoo Python package.
It only requires Python 3.9 or newer and filesystem access to the project root.

## Run a collector

From this repository:

```bash
python3 -m monitor_dashboard.server \
  --host 127.0.0.1 \
  --port 8765 \
  --project-root /nfs/project \
  --server-id huoshan \
  --server-label Huoshan_A800
```

The equivalent installed command is:

```bash
aurapilot-monitor --host 127.0.0.1 --port 8765 \
  --project-root /nfs/project --server-id huoshan \
  --server-label Huoshan_A800
```

Keep remote collectors bound to `127.0.0.1` and reach them through SSH tunnels.
The unified frontend expects:

- LN collector on local port `8765`.
- Huoshan collector on local port `8766`.

For the current setup, start one collector on each server at its own loopback
port `8765`, then forward those ports to `8765` and `8766` on the client.

## Development

Run the monitor regression suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Install in editable mode when desired:

```bash
python3 -m pip install -e .
```

## Progress data

When workflows do not publish a progress report, the dashboard derives counts
from logs and output files and labels them `Estimated from outputs`.

For precise reporting, a workflow may atomically publish `progress.json` at the
run or project root, or under `status/`, `postprocess/status/`, or `state/`.
Reported progress takes priority over filesystem inference. A running report
whose heartbeat is older than five minutes is shown as stalled.

## Security

- The service is read-only with respect to monitored projects.
- File preview and download paths are constrained to the configured project
  root.
- Do not commit project data, logs, credentials, private configuration, or
  biological sequence files to this repository.
- Keep this repository private when it contains internal topology or naming.
