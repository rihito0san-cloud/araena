#!/usr/bin/env bash
# Build the MinerPotatos worker for the GPU actually present on this machine.
#
# The compute capability is read from the driver rather than guessed: compiling
# for the wrong -arch produces a binary that loads and then fails at kernel
# launch, which looks like a mining problem rather than a build problem.
set -euo pipefail

cd "$(dirname "$0")"

OUT="${1:-miner}"

if ! command -v nvcc >/dev/null 2>&1; then
  echo "nvcc not found on PATH." >&2
  echo "Vast.ai images with CUDA installed put it in /usr/local/cuda/bin." >&2
  if [ -x /usr/local/cuda/bin/nvcc ]; then
    echo "Try:  export PATH=/usr/local/cuda/bin:\$PATH" >&2
  fi
  exit 1
fi

echo "nvcc: $(nvcc --version | tail -1)"

# Compute capability of the first visible device, e.g. "8.9" -> sm_89.
if command -v nvidia-smi >/dev/null 2>&1; then
  CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d '\r' || true)"
else
  CAP=""
fi

if [ -n "${CAP}" ]; then
  ARCH="sm_$(echo "$CAP" | tr -d '.')"
  echo "GPU compute capability: ${CAP} -> -arch=${ARCH}"
else
  echo "WARNING: could not read compute capability from nvidia-smi." >&2
  echo "Falling back to a portable build (sm_70 + PTX). This runs anywhere" >&2
  echo "from Volta up but is slower than a native build." >&2
  ARCH=""
fi

CXXFLAGS="-O3 -std=c++17 -Xcompiler -pthread"

if [ -n "$ARCH" ]; then
  # shellcheck disable=SC2086
  nvcc $CXXFLAGS -arch="$ARCH" gpu-miner.cu -o "$OUT"
else
  # shellcheck disable=SC2086
  nvcc $CXXFLAGS -gencode arch=compute_70,code=[sm_70,compute_70] \
    gpu-miner.cu -o "$OUT"
fi

echo "built ./$OUT"

# The binary refuses to start if its compiled-in Keccak vectors are wrong, so
# this doubles as a correctness gate on the build itself.
if ./"$OUT" --selftest-only; then
  echo "self-test passed"
else
  echo "SELF-TEST FAILED -- do not deploy this binary" >&2
  exit 1
fi

# Also build the host-mode binary: the test suite compares it against the
# Python reference, and it is the fallback if the GPU is unavailable.
if g++ -O2 -std=c++17 -x c++ gpu-miner.cu -o miner-host 2>/dev/null; then
  echo "built ./miner-host (CPU reference build)"
else
  echo "note: host-mode build skipped (no g++ available)"
fi
