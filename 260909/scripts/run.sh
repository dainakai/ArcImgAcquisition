#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec ./dual_holo --config config.yml "$@"
