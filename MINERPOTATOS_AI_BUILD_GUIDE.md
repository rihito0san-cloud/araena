# MinerPotatos GPU Fleet — Portable AI Build and Operations Guide

## Purpose and scope

Give this document to a coding assistant to build a local controller for rented NVIDIA GPUs, local transaction signing, a shared spending budget, and a separate read-only dashboard. This is a reconstruction specification, not an executable source distribution. The receiving assistant must implement the files, install dependencies, run tests, and verify the deployment before reporting that mining works.

This document contains no operator wallet addresses, private keys, SSH credentials, live access tokens, rental inventory, or private filesystem paths. Public project identifiers are included solely to identify the protocol. The technical observations below describe the implementation inspected on 2026-09-14; revalidate them before a new deployment.

## Instructions for the receiving AI

1. Inspect the user's operating system, workspace, available Python runtime, CUDA servers, and existing services. Do not overwrite unrelated projects.
2. Ask for the target chain/project, public signer address, total spending limit, and authorized server inventory. Accept private keys only through the local password-input UI. Do not ask the user to paste private keys into chat.
3. Build and test the controller and dashboard locally. Keep mining disabled until the protocol adapter and each GPU are validated.
4. Present exact chain, contract, payable mint price, selected signer, and budget before activation. Use the user's existing authorization; do not invent permission to spend or rent equipment.
5. During operation, hot-update presentation and worker enrollment. Do not restart the signer for a UI change. Obtain authorization before a restart that loses memory-only keys.
6. Distinguish found solution, simulation success, signed transaction, broadcast, successful receipt, and actual NFT mint. Never report these as interchangeable.
7. Explain remaining limitations. Neither a successful test nor high hashrate guarantees a mint, profitable resale, or zero reverted transactions.

## Architecture

Use three boundaries:

- **Local controller:** protocol reads, work allocation, local proof verification, memory-only signers, serialized submission, durable transaction ledger, receipt monitoring, authenticated controls.
- **Remote workers:** CUDA computation only. Receive public address, challenge, target and nonce allocation over pinned SSH. Return hashrate and candidate proofs. Never receive wallet private keys.
- **Read-only dashboard:** public controller state, ETH balances, NFT ownership and events. It must not import the signer module or decrypt SSH credentials.

Suggested ports are 8901 for the controller and 8903 for the dashboard. Bind both to 127.0.0.1. The optional historical balance proxy is not needed in a clean build.

Recommended modules:

| File | Responsibility |
|---|---|
| protocol.py | RPC, ABI encoding, challenge and price parsing, contract identity |
| credentials.py | OS-backed encryption of rental credentials and signed transaction records |
| preflight.py | SSH identity checks, CUDA contexts, device inventory |
| gpu-miner.cu | Keccak kernel, job protocol, telemetry |
| engine.py | Worker lifecycle, active signer selection, proofs, submission, receipts |
| launch_panel.py / panel.html | Local control API and minimal management UI |
| dashboard_server.py / dashboard.html | Independent read-only neon dashboard |
| launch.sqlite | Spending, public wallet list, transactions and event ledger |
| active-signer.json | Selected public signer address, never a private key |
| validated-fleet.json | Public server/device manifest and validation results |
| abi.json | Reviewed ABI from the project's authoritative sources |
| tests/ | Isolated tests and GPU comparison vectors |

Do not copy a previous operator's database or credential files. Generate fresh authentication tokens for the new installation.

## Setup on another computer

Use a supported 64-bit Python 3 environment and a virtual environment. Example commands, after implementing the files:

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install paramiko eth-account eth-abi pycryptodome
.venv\Scripts\python.exe -m pip freeze > requirements.lock.txt
~~~

Linux equivalents use python3 and .venv/bin/python. Pin the versions that actually pass your tests. Do not depend on another assistant's bundled runtime directories. Use project-relative paths or explicit configuration for all imports and data files.

For Windows, DPAPI can protect local encrypted files. For other systems, implement an appropriate OS credential store or user-unlocked encrypted vault; never silently replace encryption with plaintext. DPAPI ciphertext is tied to its protection context and is not a portable backup by itself. Wallet recovery must remain under the user's control.

A remote worker needs Linux, an NVIDIA driver compatible with its GPU, a working CUDA context and a matching binary. The controller computer does not need to mine or have CUDA. No GnuPG or PGP key generation is involved in this protocol.

## Protocol adapter: verify before spending

Public reference identifiers:

| Item | Reference |
|---|---|
| Website | https://minerpotatos.xyz/ |
| Chain | Robinhood Chain, observed chain ID 4663 |
| Project RPC | https://rpc.minerpotatos.xyz |
| Contract | 0xb0db77c5d6ed578189609ecc72d25699a79f785b |
| Observed runtime bytecode Keccak-256 | ec77f042383620b420fad9321c58d0647f5891ab6fe947705b52de8e116c7553 |
| Submission | mine(uint256 nonce, uint256 anchorBlock), payable |

These are historical configuration inputs, not proof of present authenticity. Verify chain ID, deployed code and current official references. Investigate bytecode differences rather than overriding the check. The inspected ABI came from the official frontend and was checked against live calls; a complete verified-source audit was not established.

Never execute downloaded frontend bundles inside the controller. Extract ABI data and inspect it. Read miningStatus() and compare a locally generated hash to workFor(address,bytes32,bytes32,uint256).

The observed preimage is exactly 116 bytes:

~~~text
miner address: 20 bytes
prevWork:      32 bytes
anchor:        32 bytes
nonce:         32 bytes, uint256 in big-endian encoding
hash = Keccak-256(address || prevWork || anchor || nonce)
valid if integer(hash) < target
~~~

Use Ethereum Keccak-256, not SHA-256 or standardized SHA3-256. Packed bytes must not acquire ABI word padding around the address. The 116-byte input fits one Keccak rate block: domain padding starts with 0x01 at byte 116 and ends with 0x80 at byte 135. Handle little-endian internal Keccak lanes independently from big-endian contract integers.

The observed miningStatus tuple contains:

~~~text
anchorBlock, anchor, prevWork, target, difficulty, price,
supply, maxSupply, targetInterval, lastMintAt, startTime,
anchorWindow, burstLeft, burstReadyAt, chainTime, blockNumber,
epoch, floorBits, streak, activeSupply, epochEndsAt, vault,
redeemValue, epochPrice
~~~

Decode against the actual ABI, not guessed types. Retain integer precision. Obtain the anchor from the contract; do not substitute an unrelated blockhash. Read price on every submission. Never hardcode an observed epoch's price or assume the mint is free.

The observed interface exposes currentTarget() and currentStreak(). Calling currentTarget from the successful minter and a different address at the same historical block returned the same target in a checked sample. That mint did not change the target across its block. This supports using one signer; it does not prove all future behavior or replace a source audit. Do not advertise wallet rotation as a difficulty reduction feature without independent evidence.

## GPU work allocation and validation

Use one worker process per validated device. A stable worker ID includes host, SSH port and GPU index. Reject duplicate IDs. Allocate a fresh random 24-byte nonce prefix per job and search an 8-byte counter suffix. Keep the counter space disjoint and rotate the prefix before counter wrap.

Reference line protocol, all hex without 0x:

~~~text
JOB <address40hex> <prevWork64hex+anchor64hex> <target64hex> <prefix48hex>
STOP
QUIT

RATE <hashes_per_second> <total_hashes>
FOUND <nonce64hex> <digest64hex>
~~~

Reject malformed sizes and unknown commands. Flush output promptly. Store the job associated with every prefix so a returned proof retains its original address, challenge and anchor block. Discard discoveries from a job replaced while a kernel was running. Bound kernel duration near 100 ms to limit stale work.

Before enrollment, independently compare CPU and GPU hashes on every device with zero, nonzero high-word and all-one nonces. Include vectors that reveal byte-order errors and target comparison edge cases. A benchmark alone does not establish correctness.

Example builds for an implemented reference kernel:

~~~sh
nvcc -O3 -std=c++17 -arch=sm_89 -Xcompiler -pthread gpu-miner.cu -o miner-sm89
nvcc -O3 -std=c++17 -arch=sm_120 -Xcompiler -pthread gpu-miner.cu -o miner-sm120
~~~

Use a toolkit supporting the selected architecture. The inspected fleet used CUDA 12.4 for sm_89 and CUDA 12.8 for sm_120. Verify compatibility on the user's actual servers. Check official download checksums. Upload only the matching executable and public job configuration.

Keep SSH host keys pinned. A host-key mismatch after a rental changes is a reason to verify the new host, not disable validation. Relay connections should use an authorized SSH direct-tcpip tunnel; keep credentials local. Never print passwords in commands or logs.

Manifest example — placeholders only:

~~~json
[
  {
    "host": "<RENTED_HOST>",
    "port": 2200,
    "devices": [
      {"index": 0, "name": "<GPU_MODEL>", "hash_validated": true}
    ]
  }
]
~~~

Only set hash_validated after passing tests. Hot removal must stop the worker, close its connection and suppress reconnects. Hot addition must start it once. The historical implementation hot-adds devices, but full hot-removal handling must be implemented explicitly in a new build.

## Signers and switching wallets

Private keys live only in local process memory. Clear password inputs after import. Show public addresses and loaded/selected status. Do not log keys or persist them in browser storage.

Support a selected public address in active-signer.json. Every worker must filter eligible signers through this selection before issuing a new job. The sender must reject queued proofs for inactive addresses. If the selected key is absent, show Waiting for selected signer; do not fall back to another wallet.

Implement an authenticated POST /api/active-signer endpoint. Serialize the change with submission and atomically replace the selection file. Already broadcast transactions must continue through receipt reconciliation. Public wallet rows can remain visible even when they are not active signers.

Changing a signature on an existing proof does not change the address inside its hash. A proof mined for address A cannot be submitted as address B. Low-balance wallets must not continue consuming compute unnoticed: either require a funded selected signer or explicitly implement balance-aware assignment before jobs are created.

To consolidate:

1. Select the destination signer and verify all newly issued jobs use it.
2. Wait for unresolved transactions from other addresses to settle.
3. Only then have the user transfer remaining funds separately.

Do not automatically transfer ETH merely because signer selection was requested. Preserve the shared spending ledger when the selected address changes.

## Reliable transaction lifecycle

For each candidate:

1. Independently recompute its Keccak digest locally.
2. Read fresh contract state. Check proof target, prevWork, anchor window, launch state and supply. Use contract-specific rules, not assumptions from another project.
3. Check that the signer is still selected and loaded.
4. Check pending versus confirmed nonce. An unexplained external pending transaction blocks submission for that wallet.
5. Read the exact current payable price and simulate the actual mine call with that value and signer.
6. Estimate gas and apply a bounded margin. Select fees suitable for the chain. Keep payable value and gas separate.
7. Re-read state. If only price changed and the proof remains valid, rebuild and re-simulate with the new price within a small bounded retry loop. A changed challenge cannot be repaired by repricing.
8. Verify signer funds cover price plus maximum gas liability.
9. In one database transaction, reserve liability against the shared cap and persist transaction hash, nonce and encrypted signed bytes before broadcast.
10. Broadcast and reconcile by hash, even when the RPC response times out.

Use integer wei for every financial value. Enforce:

~~~text
spent + reserved + new_maximum_liability <= shared_cap
maximum_liability = payable_value + gas_limit * maximum_fee_per_gas
~~~

The spending limit must be chosen by the new user. A displayed default is not authorization. Changing the cap must not reset spent or reserved amounts. Reject a new cap below existing obligations.

The reference sender permits one globally pending transaction, three bounded preflight attempts, and up to three identical-byte rebroadcasts. These are conservative implementation choices, not protocol requirements. Parallelize only with reliable per-wallet nonce locks and a shared atomic budget.

Retry classification:

| Condition | Action |
|---|---|
| Price changed, proof still valid | Refresh value, re-simulate, recheck budget |
| Challenge or anchor expired | Drop proof and request current work |
| Insufficient ETH | Mark signer unfunded; do not substitute another signer on that proof |
| Broadcast timeout | Reconcile original hash/nonce; retain reservation |
| Already known | Continue monitoring original transaction |
| Receipt reverted | Account gas; decode cause before any new attempt |
| External pending nonce | Resolve conflict; do not blindly increment nonce |
| RPC timeout | Bounded read retry; expose freshness and error state |
| Spending cap reached | Pause new submissions; keep receipts running |

A confirmed failed transaction consumed its nonce. Rebroadcasting it cannot turn it into a success. Any new transaction requires fresh proof validity, pricing, nonce and budget checks.

On successful receipt, validate the contract's mint Transfer event, zero source address, intended recipient and token ID. A status=1 transaction without that event is not confirmed NFT ownership. Account actual gas plus payable value for success, and actual gas only for revert. Release the unused reservation atomically exactly once.

The reference waits two blocks beyond inclusion. This is not protection against every reorganization. Implement and test the finality/reorg policy appropriate to the chain. Never release an unresolved reservation merely because a timer elapsed.

## Data persistence and restart

Use SQLite transactions for settings, public wallets, events and signed transaction records. Store financial fields as decimal strings or another lossless representation. Store encrypted signed bytes for pending recovery. Do not expose signed bytes through dashboard APIs.

Pause must stop new work/submissions while receipt monitoring continues. Before a restart, inspect pending transactions and preserve their records. After restart, reconcile them before accepting new submissions. Memory-only keys must be re-entered by the user.

Starting processes in the background should use hidden windows on Windows and explicit working directories. Capture logs without secrets. Verify port ownership before terminating a controller process. Do not indiscriminately kill Python or SSH processes.

## Dashboard and local browser access

Build a separate dark plum dashboard with restrained pink/purple neon accents, readable typography and responsive cards. Include:

- Large measured GH/s and a rolling real-sample graph.
- Fresh hashing worker count and per-device states.
- Current mint price in ETH, effective difficulty and collection supply.
- All configured public wallet addresses with copy buttons and confirmation feedback.
- ETH balances, NFT counts and available token links per wallet.
- Selected signer badge, shared cap, spent, reserved and remaining allowance.
- Timestamped solution, transaction and error events.

Count only fresh telemetry from hashing workers. The reference freshness cutoff is five seconds. Do not count disconnected devices at their last reported rate.

Calculate effective bits as log2(2^256 / target); expected hashes per solution are approximately 2^256 / target. Expected seconds are expected hashes divided by fresh aggregate hashes/second. These are statistical estimates, not guaranteed mint rates; challenge turnover and competition reduce realized success.

Poll controller state about every two seconds. Poll ETH balances and NFT ownership about every fifteen seconds, using a common block for coherent snapshots. Use eth_getBalance, balanceOf(address), and ownerOf(tokenId). Distinguish current holdings from mint history: a transferred NFT remains a historical mint but is no longer owned by its original recipient.

Local transaction history does not enumerate every externally acquired NFT. For complete token ID discovery, index Transfer logs or use a verified indexer and reconcile against balanceOf. Preserve cached values with a visible stale marker on read failure; never turn an RPC error into a zero balance.

Use a browser fetch timeout. Report disconnected/stale state rather than showing Mining live indefinitely. Sound should be optional, require a user gesture, and deduplicate confirmed transaction events so page reload does not replay all historical alerts. Test sound independently; the reference dashboard does not establish a verified audio implementation.

Use authenticated loopback APIs with strict Host/Origin validation, request size limits, no-store responses and a restrictive content policy. The read-only service must reject mutation requests. Prefer a separate read-only access credential in a new build; the historical dashboard shared the controller's runtime token.

Access links place the token after # and send it in an Authorization header. Tokens must not appear in shared documentation. If the controller rotates its token on restart, regenerate every launcher and refresh every open dashboard. Navigating only to a different fragment does not reload a page whose script captured the old token; perform a real page reload. Never fix this by disabling authentication.

Provide local launchers for the controller and dashboard that read the current local URL files and open Chrome. Do not publish fixed links containing live tokens. UI-only changes must not restart mining.

## Acceptance tests before claiming success

Use isolated mocks for transaction tests. Do not spend funds just to exercise error branches.

- CPU and each GPU agree on independent byte-order and nonce vectors.
- Bad digest, stale prevWork, expired anchor, wrong chain and contract are rejected.
- Price refresh rebuilds the simulated transaction and respects the cap.
- Lost broadcast response retains reservation and reconciles the original hash.
- Restart does not duplicate a pending transaction or reset spending.
- Receipt processing is idempotent and validates mint events.
- Selecting one signer directs all jobs to it; an absent selected key never falls back.
- Old queued proofs cannot be submitted after signer deselection.
- Hot addition creates one worker per device; removal suppresses reconnects.
- UI restart/authentication behavior works in ordinary Chrome, not only an embedded browser.
- Copy-address controls copy the complete public address.
- Wallet RPC failure displays unknown/stale, not zero.
- No horizontal overflow on narrow screens; long events remain readable.
- Dashboard outage does not interrupt mining or receipt monitoring.

Finally report measured fresh worker count, hashrate, selected signer, chain-state age, budget, pending transaction count and actual receipt evidence. If keys are not yet loaded, say the system is waiting for the user instead of claiming that it is mining.

## Operational limitations to preserve in the handoff

The reference is not a full contract audit. It uses a project RPC without implemented failover, does not implement automatic transaction fee replacement/cancellation, lacks a cross-project signer lock, and does not establish deep-reorg recovery. These are explicit engineering tasks if required, not features to claim by default.

Do not run another sender on the same wallet concurrently without coordination. An increase in GPU throughput cannot eliminate challenge expiry, RPC delays, competition or transaction inclusion latency. Maintain accurate events and bounded retries rather than promising instant or guaranteed mints.

## Delivery expected from the receiving AI

Deliver a clean source directory, locked dependencies, sample configuration with placeholders, local launchers, tests, setup instructions and a verified status report. Keep all credentials, runtime access links, private wallet material and operator databases outside the shareable package. The final result should let the new user provide their own server access, signer and spending authorization locally, then start a validated fleet.
