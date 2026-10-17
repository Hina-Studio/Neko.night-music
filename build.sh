#!/usr/bin/env bash
# Build the C++ core extension on Linux.
set -euo pipefail
cd "$(dirname "$0")"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install nuitka ordered-set zstandard

cmake -S . -B build_linux -DCMAKE_BUILD_TYPE=Release \
    -Dpybind11_DIR="$(python -m pybind11 --cmakedir)"
cmake --build build_linux -j"$(nproc)"

so="$(ls app/ncm_core*.so | head -n 1)"
echo "OK: ${so}"
