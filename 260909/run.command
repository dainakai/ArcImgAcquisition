#!/bin/zsh
set -eu
cd "${0:A:h}"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
exec ./build/DualHolo.app/Contents/MacOS/DualHolo --config config.yml "$@"
