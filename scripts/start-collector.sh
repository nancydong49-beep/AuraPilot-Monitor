#!/bin/sh
set -eu

exec python3 -m monitor_dashboard.server "$@"
