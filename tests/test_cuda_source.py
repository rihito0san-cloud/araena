"""
Compile-check the CUDA branch of worker/gpu-miner.cu without needing nvcc.

Why this exists: the CUDA code path is never compiled by the host-mode build,
so a typo in the kernel or in the device-memory plumbing ships silently and
only surfaces on the rented GPU. This test type-checks the real shipped source
against a stub CUDA API using g++, which catches those errors before deploy.

What it does and does not prove:
  proves   -- the CUDA branch parses and type-checks; the kernel signature,
              atomicCAS/__threadfence usage, dim3 fields and cudaMalloc
              template forms are all consistent
  does not -- run anything on a GPU, check -arch compatibility, or validate
              kernel launch geometry. build.sh's self-test covers the binary.

The only edit made to the source is rewriting the <<<>>> launch syntax, which
g++ cannot parse. The kernel body is byte-identical to the shipped file, and
the test asserts that is the case so the check cannot quietly drift into
testing a copy.
"""
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(os.path.dirname(HERE), "worker")

STUB = r"""
/* Minimal CUDA API stub: enough for g++ to type-check the shipped kernel.
   __host__/__device__ are passed as compiler keywords on the command line,
   because in real nvcc they are keywords, not header macros -- and keccak.h
   is included before cuda_runtime.h. */
#ifndef CUDA_STUB_H
#define CUDA_STUB_H
#include <cstdint>
#include <cstddef>
#define __global__
struct dim3 {
  unsigned x, y, z;
  dim3(unsigned a = 1, unsigned b = 1, unsigned c = 1) : x(a), y(b), z(c) {}
};
extern dim3 blockIdx, threadIdx, gridDim, blockDim;
typedef int cudaError_t;
#define cudaSuccess 0
struct cudaDeviceProp { char name[256]; int major, minor, multiProcessorCount; };
enum cudaMemcpyKind { cudaMemcpyHostToDevice, cudaMemcpyDeviceToHost };
cudaError_t cudaSetDevice(int);
cudaError_t cudaGetDeviceProperties(cudaDeviceProp*, int);
template <class T> cudaError_t cudaMalloc(T** p, size_t n) {
  (void)p; (void)n; return 0;
}
cudaError_t cudaMemcpy(void*, const void*, size_t, cudaMemcpyKind);
cudaError_t cudaDeviceSynchronize();
const char* cudaGetErrorString(cudaError_t);
__device__ inline unsigned long long atomicCAS(unsigned long long* a,
                                               unsigned long long cmp,
                                               unsigned long long val) {
  unsigned long long old = *a;
  if (old == cmp) *a = val;
  return old;
}
__device__ inline void __threadfence() {}
#endif
"""


@pytest.fixture()
def gxx():
    exe = shutil.which("g++")
    if not exe:
        pytest.skip("g++ not available; cannot type-check the CUDA branch")
    return exe


def test_cuda_branch_type_checks(gxx, tmp_path):
    src = os.path.join(WORKER, "gpu-miner.cu")
    assert os.path.exists(src), "worker/gpu-miner.cu missing"
    original = open(src, encoding="utf-8").read()

    # Rewrite only the launch syntax. Anything else differing means this test
    # has started checking a modified copy rather than the shipped kernel.
    rewritten = original.replace(
        "mp_search<<<blocks, threads_per_block>>>(", "mp_search(")
    assert rewritten != original, (
        "launch syntax not found -- the kernel call site changed shape and "
        "this test needs updating")
    # Count the launch operator, not the symbol: "mp_search(" also appears in
    # the kernel's own definition, so counting it double-counts.
    assert original.count("<<<") == 1, (
        f"expected exactly one kernel launch, found {original.count('<<<')}")
    assert "<<<" not in rewritten, "a launch site survived the rewrite"

    target = tmp_path / "kernel_check.cu"
    target.write_text(rewritten, encoding="utf-8")
    (tmp_path / "cuda_runtime.h").write_text(STUB, encoding="utf-8")
    for hdr in ("keccak.h", "mp_proto.h", "vectors.inc"):
        shutil.copy(os.path.join(WORKER, hdr), tmp_path / hdr)

    proc = subprocess.run(
        [gxx, "-O2", "-std=c++17", "-fsyntax-only", "-D__CUDACC__",
         "-D__host__=", "-D__device__=", f"-I{tmp_path}", "-x", "c++",
         str(target)],
        capture_output=True, text=True)

    # Filter the benign "#pragma unroll" noise g++ emits for CUDA pragmas.
    noise = ("pragma unroll", "In file included", "^ ", "^\\s+\\|")
    errors = [ln for ln in proc.stderr.splitlines()
              if ln.strip() and not any(re.search(p, ln) for p in noise)]
    assert proc.returncode == 0, (
        "CUDA branch failed to type-check:\n" + "\n".join(errors[:40]))


def test_kernel_publishes_counter_and_digest_atomically():
    """Regression guard for a real race that was shipped once.

    The kernel used atomicMin on the digest's leading word and then stored
    best_counter and best_digest separately, so a delayed loser could pair one
    candidate's counter with another's digest. The controller re-verifies and
    would reject it, so the symptom was a silently lost proof.
    """
    src = open(os.path.join(WORKER, "gpu-miner.cu"), encoding="utf-8").read()
    kernel = src[src.index("__global__ void mp_search"):src.index(
        "struct mp_device")]
    # Strip comments before asserting absence: the kernel carries a comment
    # explaining the old atomicMin race, and scanning raw text would match it.
    code = re.sub(r"/\*.*?\*/", "", kernel, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)

    assert "atomicCAS(claim, 0ULL, 1ULL)" in code, (
        "single-publisher claim missing from the kernel")
    assert "__threadfence()" in code, (
        "the counter/digest pair must be fenced before the flag is read")
    assert "atomicMin" not in code, (
        "ranked atomicMin reintroduces the torn counter/digest race")
    # The published pair must be written by the same thread that won the claim.
    claim_at = code.index("atomicCAS(claim")
    assert code.index("*best_counter = counter") > claim_at
    assert code.index("best_digest[i] = d[i]") > claim_at

