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

## Share on a trusted LAN

Run the gateway on the computer that owns both SSH tunnels:

```bash
python3 -m monitor_dashboard.gateway \
  --host 0.0.0.0 \
  --port 8780 \
  --ln-url http://127.0.0.1:8765 \
  --huoshan-url http://127.0.0.1:8766
```

Colleagues on the same private network can then open:

```text
http://<gateway-computer-LAN-IP>:8780
```

The browser talks only to port `8780`. The gateway forwards same-origin
`/collector/ln/api/...` and `/collector/huoshan/api/...` requests through the
existing local SSH tunnels. Remote collectors remain bound to loopback and are
not exposed to the LAN.

By default the gateway accepts only loopback, RFC1918 IPv4, and private IPv6
clients. It does not enable a password automatically. To add HTTP Basic Auth,
put the password in an environment variable rather than on the command line:

```bash
export AURAPILOT_MONITOR_PASSWORD='replace-with-a-strong-password'
python3 -m monitor_dashboard.gateway \
  --auth-user monitor \
  --auth-password-env AURAPILOT_MONITOR_PASSWORD
```

Keep the gateway computer awake and keep both SSH tunnels running. Restrict
port `8780` with the host firewall when the surrounding network is not fully
trusted.

## Direct public-port deployment

When a host firewall or cloud security group already exposes a selected port,
public clients must be allowed explicitly and HTTP Basic Auth should be enabled:

```bash
export AURAPILOT_MONITOR_PASSWORD='replace-with-a-strong-password'
python3 -m monitor_dashboard.gateway \
  --host 0.0.0.0 \
  --port 8888 \
  --ln-url http://127.0.0.1:8766 \
  --huoshan-url http://127.0.0.1:8765 \
  --allow-cidr 0.0.0.0/0 \
  --allow-cidr ::/0 \
  --auth-user monitor
```

This direct form serves plain HTTP, so Basic Auth credentials and dashboard
traffic are not encrypted in transit. For long-term or multi-user deployment,
put the gateway behind an HTTPS reverse proxy with a domain and trusted TLS
certificate. Keep the two collector services bound to loopback.

A macOS `launchd` template is available at
`deploy/macos/com.aurapilot.monitor-gateway.plist.example`. Replace its three
placeholders with the Python executable, repository, and log-directory paths
before installing it under `~/Library/LaunchAgents`. Because macOS may block
background services from reading `Documents`, deploy the launchd runtime copy
under a non-protected path such as `~/.local/share/AuraPilot-Monitor`.

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
