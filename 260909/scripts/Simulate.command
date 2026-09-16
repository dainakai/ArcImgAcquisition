#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec ./DualHolo.app/Contents/MacOS/DualHolo --config config.yml --simulate "$@"
