# AGENTS.md — MinerPotatos GPU fleet

Instructions for AI coding agents working in this repository. Read this before
changing anything. It encodes facts that were verified against the live chain
and bugs that were already paid for once; re-deriving them wastes a round trip,
and getting them wrong silently produces a miner that runs fast and mints
nothing.

The human-facing setup guide is `README.md` (Russian). This file is the
technical contract.

---

## 1. What this is

A local controller plus CUDA workers that mine proof-of-work NFT mints on
[minerpotatos.xyz/mine](https://minerpotatos.xyz/mine), designed for rented
Vast.ai GPUs. Built from the specification in
`MINERPOTATOS_AI_BUILD_GUIDE.md` — that document is a **spec, not a source
distribution**. Everything here was written from it and verified independently.

Three trust boundaries, and they are load-bearing:

| Layer | May do | Must never do |
|---|---|---|
| Worker (`worker/`) | hash, propose nonces | hold a private key |
| Controller (`controller/`) | verify proofs, sign, spend | trust a worker's digest |
| Dashboard (`controller/dashboard.py`) | read public state | import `signer.py`, mutate |

---

## 2. Ground truth — verified, do not re-guess

Re-verified against the live chain on **2026-09-14**. Re-check before relying
on these if you are reading this much later; the contract can be redeployed.

| Fact | Value | How it was established |
|---|---|---|
| Chain ID | `4663` (`0x1237`) | `eth_chainId` |
| Contract | `0xb0db77c5d6ed578189609ecc72d25699a79f785b` | extracted from the frontend bundle |
| Runtime bytecode | keccak256 = `ec77f042…116c7553` | `eth_getCode`, matches the guide |
| Submit | `mine(uint256 nonce, uint256 anchorBlock)` payable | ABI (158 entries) |
| Verify view | `workFor(address,bytes32,bytes32,uint256)` pure | ABI |
| Status view | `miningStatus()` → 24-field tuple | ABI, decoded positionally |
| Selectors | `miningStatus()` `be38c5c8`, `workFor(…)` `41c7e5bd`, `mine(…)` `071e9503`, `mintPrice()` `6817c76c` | keccak of signature |
| Anchor window | 250 blocks | `miningStatus().anchorWindow`, confirmed empirically |
| Deploy block | 62985004 | frontend `VITE_DEPLOY_BLOCK` |

### The preimage — 116 bytes, exactly

```
miner address   20 bytes   packed, NOT ABI-padded to a 32-byte word
prevWork        32 bytes
anchor          32 bytes   == blockhash(anchorBlock)
nonce           32 bytes   big-endian uint256
```

```
digest = keccak256(miner || prevWork || anchor || nonce)
valid  = uint256(digest) <= target      # INCLUSIVE — equality counts
```

Confirmed on **6 vectors** against the contract's own pure `workFor()` view.

The nonce the browser/worker submits is `preimage[84:116]`, i.e.
`prefix(24) || counter(8, big-endian)`.

### The trap that will waste your time

**`prevWork` and `anchorBlock` change every single block.**

If you read `miningStatus()` and then call `workFor()` at `"latest"`, the two
calls land in different blocks and your vectors will not match. You will
conclude the preimage layout in this file is wrong. It is not. Pin every
comparison to one block tag:

```python
st = contract.mining_status()          # picks block N-1 internally
block = st["_block"]
remote = contract.work_for(miner, st["prevWork"], st["anchor"], nonce_int, block)
```

Observed real-mint anchor lag: **19–126 blocks** against the 250-block window.
That is the practical submission budget, and it is why `submit.py` hard-fails on
`anchor_expired` rather than trying to reprice.

### Keccak specifics that differ from what you might assume

- Ethereum Keccak-256, **not** `hashlib.sha3_256` (padding byte `0x01` vs `0x06`)
  and not SHA-256.
- 116 bytes fits one rate block. Rate is **136 bytes = 17 lanes exactly**.
  `0x01` at byte 116, `0x80` at byte 135.
- Lanes are little-endian internally; contract integers are big-endian. These
  are independent and both appear in this codebase.
- The pi step writes to index `y + 5*((2x+3y) mod 5)`. Swapping the two index
  arguments compiles cleanly and yields a stable, fast, completely wrong hash.

---

## 3. Invariants — break these and you ship a broken miner

1. **A worker only proposes.** `fleet.py::Worker._on_found` recomputes the
   digest locally and drops anything that disagrees. Do not "optimize" this
   away. A broken GPU must cost hashrate, never a bad transaction.
2. **One pinned block per read.** Never mix chain reads from two blocks into a
   single proof or a single transaction decision.
3. **Price is read on every submission.** Never hardcode it. It moves per epoch.
4. **The cap is not defaulted.** `budget.py` refuses to reserve when `cap == 0`.
   A displayed default is not authorization.
5. **A proof belongs to the address inside its hash.** A proof mined for A
   cannot be submitted as B. `submit.py` raises `signer_mismatch`; do not relax
   this.
6. **No signer fallback.** If the selected key is absent, wait. Never substitute
   another loaded wallet.
7. **`status=1` is not ownership.** A receipt without a `Transfer` from the zero
   address to the expected wallet is booked as a **failure** (`no_mint_event`).
8. **Reserve before broadcast**, in one DB transaction, and release exactly
   once. Receipt processing must stay idempotent.
9. **Fresh prefix per job.** Reusing a prefix re-searches space already covered
   and inflates displayed hashrate without doing new work.
10. **Count only fresh telemetry.** A disconnected worker must not keep
    contributing its last known rate (`FRESH_SECONDS = 5`).

---

## 4. Bugs already found and fixed — do not reintroduce

Each of these was real, and none was visible by reading the code.

| Bug | Symptom | Guard |
|---|---|---|
| `MP_STATE_LANES` was 16 instead of 17 | dropped the `0x80` padding lane; every hash wrong | `tests/test_keccak_vectors.py` canonical vectors |
| pi-step index arguments swapped | stable, fast, wrong Keccak | same, plus `mp_reference.py` |
| `mp_emit` emitted no `\n` | all output on one line; any line reader blocked to EOF | `tests/test_worker.py`, harness |
| `mp_readline` used `fgets` on a pipe | `QUIT` buffered with `JOB` was consumed before hashing; worker exited without searching | poll-based `mp_next_line` |
| `eth_account` 0.14 needs checksummed `to` | **every** real submission failed to sign | `tests/test_submit.py` happy path |
| CUDA kernel used ranked `atomicMin` + separate stores | a delayed loser overwrote the pair: one candidate's counter with another's digest → silently lost proof | `tests/test_cuda_source.py` |
| anchor-window check nested under "anchor changed" | expired anchors slipped through when the anchor was unchanged | `tests/test_submit.py::test_expired_anchor_is_dropped` |
| `forget()` cleared the signer selection | "selected but key not entered" became indistinguishable from "no signer" | `tests/test_submit.py` |

If you touch the kernel, note that the publish path must stay
single-publisher via `atomicCAS` with a `__threadfence()` before the flag is
read. "First solve wins" is intentional: any nonce under target mints the same
NFT, so ranking buys nothing and costs a race.

---

## 5. Build, test, run

```bash
python3 -m pip install -r requirements.txt

python3 -m pytest tests/ -q                  # 76 tests, includes live RPC checks
MP_SKIP_LIVE=1 python3 -m pytest tests/ -q   # offline: 66 pass, 10 skip

# Worker. Reads compute capability from nvidia-smi; refuses to ship if the
# compiled-in self-test fails.
bash worker/build.sh miner                   # CUDA build
g++ -O2 -std=c++17 -x c++ worker/gpu-miner.cu -o miner-host   # CPU reference

# Protocol harness against the live contract
python3 tests/harness.py --binary miner-host --easy --live

# Controller (loopback only) and read-only dashboard
python3 controller/panel.py --binary ./worker/miner --device 0
MP_PANEL_TOKEN=<panel token> python3 controller/dashboard.py
```

### The harness caveat

`tests/harness.py` keeps stdin open and sends `QUIT` only after `FOUND`. This is
**not** a valid test and never will be:

```bash
printf 'JOB ...\nQUIT\n' | miner   # worker eats QUIT before hashing, exits
```

That is correct worker behaviour — it drains buffered commands before starting.
Do not "fix" the worker to hash before draining.

### What the tests do and do not prove

`tests/test_cuda_source.py` type-checks the CUDA branch through `g++` with a
stub CUDA API. It rewrites **only** the `<<<>>>` launch syntax and asserts
nothing else changed, so it cannot quietly drift into testing a copy.

It proves the branch parses and type-checks. It does **not** run anything on a
GPU, check `-arch` compatibility, or validate launch geometry. No GPU was
available where this was written. `build.sh`'s on-device self-test is the real
gate.

---

## 6. Architecture notes for changes

**`controller/protocol.py`** — the only module that talks to the contract.
`STATUS_FIELDS` is positional; a tuple-order change must fail loudly, not
silently reassign fields.

**`controller/budget.py`** — SQLite, integer wei as decimal strings. The
invariant is `spent + reserved + new_liability <= cap`, where
`liability = value + gas_limit * max_fee_per_gas`. Lowering the cap below
existing obligations is refused.

**`controller/submit.py`** — retry classification matters. Price churn is
repairable (re-read, re-simulate, bounded loop). A moved `prevWork` or expired
anchor is terminal: drop the proof, request fresh work. Repricing cannot fix a
changed challenge.

**`controller/signer.py`** — memory only. Imported by the panel and nothing
else. If you add a module that needs keys, stop and reconsider the boundary.

**`worker/gpu-miner.cu`** — one file, two builds. `__CUDACC__` selects the CUDA
path; `g++ -x c++` compiles the host path. Both share `keccak.h` and
`mp_proto.h`, which is the point: the GPU path and the reference path cannot
drift apart.

---

## 7. Known limitations — do not describe these as features

- No per-request RPC failover (the RPC list retries across rounds only).
- No automatic transaction fee replacement or cancellation.
- Finality policy is 2 blocks past inclusion; no deep-reorg recovery.
- One globally pending transaction, deliberately. No per-wallet nonce
  parallelism.
- No hardware-backed key storage or unattended key recovery.
- High hashrate does not guarantee a mint. Anchor expiry, RPC latency,
  competition and inclusion delay all reduce realized success.

---

## 8. File map

```
worker/
  keccak.h        Keccak-256, single rate block, shared by CUDA and host
  mp_proto.h      line protocol: JOB/STOP/QUIT <-> READY/RATE/FOUND/ERROR/BYE
  gpu-miner.cu    CUDA kernel + host fallback + self-test gate
  vectors.inc     generated; do not hand-edit (see test_keccak_vectors.py)
  build.sh        arch detection from nvidia-smi, refuses on self-test failure
controller/
  protocol.py     RPC, ABI, preimage, miningStatus decode
  budget.py       cap enforcement, atomic reserve/release, ledger
  signer.py       memory-only keys, no fallback
  fleet.py        worker lifecycle, local proof verification
  submit.py       transaction lifecycle, receipt reconciliation
  engine.py       orchestration loop
  panel.py/html   authenticated control UI, 127.0.0.1:8901
  dashboard.py/html  read-only, separate token, 127.0.0.1:8903
  abi.json        ABI extracted from the official frontend
tests/
  mp_reference.py from-spec Keccak + hand-rolled RPC, deliberately independent
  harness.py      end-to-end worker protocol driver
  test_*.py       76 tests
deploy/install-on-vast.sh
```

`tests/mp_reference.py` is intentionally **not** imported by the controller. Its
value is that it shares no code with what it checks. Three independent
authorities agree on every proof: this module, pycryptodome, and the contract's
own `workFor()`.

---

## 9. If you are asked to "just make it work"

The things most likely to be suggested and why they are wrong here:

- **Hardcode the price or target.** Both move. Price per epoch, target per
  retarget window and streak.
- **Skip local proof verification to save CPU.** It is the only thing making an
  untrusted rented GPU safe.
- **Relax the checksummed-address requirement.** It is not a requirement to
  relax; it is what `eth_account` 0.14 enforces.
- **Run the panel on `0.0.0.0`.** The token is the only authentication. Use an
  SSH tunnel.
- **Add a default spending cap.** Zero means "cannot spend" on purpose.
- **Report a broadcast as a mint.** Six distinct stages exist for a reason.
