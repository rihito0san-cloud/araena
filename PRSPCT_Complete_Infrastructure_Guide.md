# PRSPCT CPU and GPU Mining Infrastructure

**Complete installation, source, migration, and operations guide**  
Prepared: September 13, 2026  
Controller platform: Windows x64  
Worker platform: Linux x86-64 with optional NVIDIA CUDA GPUs

This document contains the operating guide and the complete readable source bundle. Save this file as `PRSPCT_Complete_Infrastructure_Guide.md`. The extraction procedure reconstructs a separate project directory from the source appendix. Python packages, compilers, rented servers, and operator credentials must be supplied during installation.

The system searches for PRSPCT proofs on CPU and GPU workers, validates every returned proof locally, and submits zero-value claims through one local wallet signer. Remote machines perform hashing; the local computer owns the signing process, transaction queue, and cumulative gas accounting.

The embedded source is a portable handover edition. It removes the original workstation dependency path, reads the public wallet from configuration, provides an English interface and an interactive operations utility, defaults local CPU mining to off, and requires explicit server selection for deployment. It does not change the proof algorithm, contract destination, claim value, or transaction fee ceilings. Source preparation does not update an already running installation.

## Contents

1. System architecture and trust boundaries
2. Network, contract, and proof specification
3. Requirements and directory layout
4. Extract the project from this Markdown
5. Install on a new computer
6. Configure the wallet and spending allowance
7. Connect and provision servers
8. Configure direct and relay connections
9. Validate and enable multiple GPUs
10. Start mining and automatic claims
11. Transaction lifecycle and retries
12. Control the local CPU and remote fleet
13. Move an existing installation to another computer
14. Monitoring, estimates, and rental economics
15. Troubleshooting and recovery
16. Update, stop, and decommission
17. API and configuration reference
18. Verification record and limitations
19. Source manifest and complete source appendix

## 1. System architecture and trust boundaries

```text
Local browser, restricted to 127.0.0.1
    |
    | authenticated local HTTP
    v
Windows Python controller
    +-- public chain polling and target selection
    +-- local C# CPU worker, optional
    +-- dynamic SSH fleet supervisor
    |      +-- Linux C++ CPU worker per endpoint
    |      +-- CUDA worker per validated GPU
    |      +-- optional authorized SSH relay
    +-- independent Ethereum Keccak proof verification
    +-- one in-memory wallet signer
    +-- persistent spending ledger and pending reservations
    |
    | signed transactions and public RPC reads
    v
Robinhood Chain -> PRSPCT contract -> NFT mint receipt
```

The controller computer must remain awake, connected, and running. Workers communicate through long-lived SSH stdin/stdout channels. They are not detached mining services: closing their input ends the worker. A connection failure triggers a new SSH session and a fresh nonce prefix. A network partition can delay the remote process noticing EOF, so verify process exit when a precise stop matters.

Only the seed, public wallet address, target, and random nonce prefix are distributed. SSH passwords authenticate rented machines and are stored locally with Windows DPAPI. The wallet private key is entered manually in the local browser, retained in process memory, and never installed on a worker. Signed transaction bytes are stored in the local ledger for recovery. Treat that ledger as private operational state even though signed transactions are eventually public.

The panel binds to `127.0.0.1:8866`. A new random access token is generated at startup and placed in the fragment of the local launch URL. API requests carry that token in `X-Panel-Token`. Do not expose this listener through a public tunnel or reverse proxy.

The original reference design accepts previously unknown SSH host keys on first connection. That is trust on first use, not independent verification of server identity. For a new rental, compare its host key fingerprint with a trusted provider source before relying on it. Known-key changes require investigation. This package does not implement hardware-backed wallet signing or unattended key recovery.

## 2. Network, contract, and proof specification

| Parameter | Value |
| --- | --- |
| Network | Robinhood Chain mainnet |
| Chain ID | `4663`, hexadecimal `0x1237` |
| Contract | `0xd078008c3D887A52CE722A3cA0539cA1F4971dD1` |
| Primary RPC | `https://rpc.mainnet.chain.robinhood.com/` |
| Fallback RPC | `https://robinhood.drpc.org` |
| Transaction function | `claim(uint256 nonce)` |
| Function selector | `0x379607f5` |
| Transaction value | `0` wei |
| Maximum collection depth handled by this version | `8888` |
| Hash function | Ethereum Keccak-256 |
| Accepted solution | Unsigned 256-bit hash strictly below the current target |

These contract settings describe the audited deployment, not a generic configuration for another project. Before enabling signing, run the read-only preflight and verify the contract against the project's official deployment information. Contract-code presence and a matching ABI shape alone do not establish that an arbitrary replacement contract is safe.

The proof preimage is exactly:

```text
seed[32 bytes] || mining_wallet[20 bytes] || nonce[32 bytes]
```

The total length is 84 bytes. The 20-byte address is packed directly; it is not padded to a 32-byte ABI word. Hashing hexadecimal text instead of raw bytes produces an invalid proof.

```text
nonce = random_prefix[24 bytes] || counter[8 bytes, big endian]
digest = keccak256(seed || wallet || nonce)
valid = integer_big_endian(digest) < target
```

Ethereum Keccak-256 differs from standardized SHA3-256 and from SHA-256. In the single 136-byte rate block, this implementation places Keccak padding `0x01` at byte 84 and `0x80` at byte 135. Do not replace it with `hashlib.sha3_256` or a Bitcoin hashing implementation. Bitcoin SHA-256 ASIC capacity cannot execute this CUDA/C++ Keccak worker.

Each connection receives a fresh 192-bit random prefix. CPU threads use disjoint counter strides; GPU threads divide contiguous batches. Counters remain monotonic when a job's target changes, preventing the same prefix and counter ranges from being searched repeatedly. A displayed hash rate does not prove useful unique work unless this behavior is correct.

The controller decodes a 14-word, 448-byte `state()` response: depth at word 0, seed at word 1, displayed price at word 3, and current target at word 4. It queries `targetOf(uint256,uint256)` with `(min(depth + 32, 8887), 0)` to mine against a stricter target ahead of current depth. Returned proofs are checked again against fresh contract state before signing. The price field is observed; claim transaction value remains zero.

Network architecture and ETH gas use are described in the [official Robinhood Chain documentation](https://docs.robinhood.com/chain/). The deployment's project references are the [PRSPCT site](https://prspct.xyz/) and [project paper](https://prspct.xyz/paper). The paper could not be retrieved again during this packaging pass; algorithm details here are grounded in the inspected source and earlier deployment checks. Recheck deployment identity and availability before a new installation commits gas.

## 3. Requirements and directory layout

### Controller computer

- Windows x64 and an available local TCP port 8866.
- Python 3.12 x64; the reference interpreter was 3.12.14.
- Python packages: PyCryptodome 3.23.0, eth-account 0.14.0, Paramiko 5.0.0, and their dependencies.
- .NET Framework x64 C# compiler if building or testing the optional local CPU worker.
- Stable outbound HTTPS and SSH access; sufficient disk space for packages, sources, and logs.
- A browser on the same computer for private-key entry.

The current controller is Windows-specific because it uses DPAPI, `msvcrt` locks, and a .NET executable. A Linux or macOS controller requires a deliberate port of credential storage, locking, process launch, and local worker selection. Linux rental workers are supported independently of the controller OS restriction.

### Rented worker

- Linux x86-64 with authorized root SSH access.
- Python 3 and `g++`, or a compatible validated native binary.
- For GPU mining, a functioning NVIDIA driver with CUDA device access.
- NVIDIA CUDA compiler/runtime files for compilation, or a compatible precompiled binary validated on that exact device.
- A provider plan permitting the intended workload.

Large VRAM is not the primary requirement for this fixed-size hash kernel. Actual verified hash rate, rental cost, driver health, and connection reliability matter more than advertised model names alone. Consumer GPUs from the reference deployment included RTX 3060 Ti, 3070, 4080 SUPER, 4090, 5060, 5080, and 5090.

### Project layout

```text
prspct-miner/
  controller.py           Chain polling, verification, signing, local API
  fleet.py                Dynamic CPU/GPU workers over SSH
  probe_servers.py        Direct SSH and configured relays
  setup_credentials.py    DPAPI encryption helpers
  ops.py                  Interactive setup and operational commands
  panel.html              English local dashboard
  CpuMiner.cs             Optional Windows CPU worker source
  cpu-linux.cpp           Linux CPU worker source
  gpu-miner.cu            CUDA worker source
  deploy_cpu_selected.py  Explicit CPU deployment entry point
  deploy_cpu_servers.py   CPU deployment implementation
  deploy_new_cpu_fleet.py CPU validation and host-key merge helpers
  prepare_cuda_tools.py   Official CUDA redistributable installation
  deploy_gpu.py           GPU compilation, validation, updates, audit
  merge_gpu_metadata.py   Serialized deployment metadata updates
  start.py                Optional hidden background launch
  requirements.txt        Top-level Python version pins
  local-settings.json     Local CPU preference, initially false
  disabled-ports.json     Explicit blocked ports, initially empty
  test_*.py               Offline regression suite
```

Files created later include `deployment.json`, `ssh-secrets.dpapi`, `ssh-known-hosts`, `gpu-ready.json`, `launch.json`, `service.log`, and `spend-ledger.json`. The manifest lists source files only. No live wallet key, SSH password, tokenized launch URL, or original spending ledger is embedded.

## 4. Extract the project from this Markdown

Install Python first. Save the following block as `extract_prspct.py` next to this Markdown, then run the command below from Windows Command Prompt. This temporary extraction script is created locally from the document; no separate download package is required.

```python
from pathlib import Path
import hashlib
import re
import sys

document = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
text = document.read_text(encoding="utf-8")
pattern = re.compile(
    r"<!-- FILE: ([A-Za-z0-9_.-]+) SHA256: ([0-9a-f]{64}) -->\n"
    r"````[^\n]*\n(.*?)\n````\n<!-- END FILE -->",
    re.S,
)
entries = pattern.findall(text)
if not entries:
    raise SystemExit("No embedded files found; use the complete Markdown file.")
names = [name for name, _, _ in entries]
if len(names) != len(set(names)):
    raise SystemExit("Duplicate file entries.")
verified = []
for name, expected, body in entries:
    if name in (".", ".."):
        raise SystemExit("Invalid filename.")
    data = (body + "\n").encode("utf-8")
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("Checksum mismatch: " + name)
    verified.append((name, data))
if destination.exists():
    raise SystemExit("Destination already exists; choose a new empty path.")
destination.mkdir(parents=True)
for name, data in verified:
    target = destination / name
    if target.resolve().parent != destination:
        raise SystemExit("Invalid destination.")
    target.write_bytes(data)
print("Extracted", len(verified), "verified files to", destination)
```

```bat
py -3.12 extract_prspct.py PRSPCT_Complete_Infrastructure_Guide.md C:\PRSPCT\prspct-miner
cd /d C:\PRSPCT\prspct-miner
```

Checksums detect changes to the embedded code while copying the document. They do not independently authenticate its author. Use a new directory; the extractor deliberately refuses to overwrite an existing installation or spending ledger.

## 5. Install on a new computer

Run these commands inside the extracted directory:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /optimize+ /unsafe /platform:x64 /out:CpuMiner.exe CpuMiner.cs
.venv\Scripts\python.exe -m unittest test_miner test_fleet_audit test_local_cpu -v
```

If the compiler is missing, install the official .NET Framework developer tools or use an appropriate x64 C# compiler. The local CPU executable is optional during production when local CPU mining is disabled, but the native CPU regression tests require it. The tests briefly run the local CPU worker and mocked local HTTP servers; they do not submit transactions or connect to rental servers.

`requirements.txt` pins the three direct libraries observed in the reference environment. It is not a full transitive lock or an offline wheel archive. After a successful install, record `pip freeze` and preserve your package environment for reproducibility. The embedded controller imports the active virtual environment and no longer depends on another workspace directory.

For first operation, keep the controller in a visible terminal so shutdown is straightforward:

```bat
.venv\Scripts\python.exe ops.py init
.venv\Scripts\python.exe ops.py preflight
```

Do not start the controller until wallet configuration and any existing-wallet migration are complete. The new project's local CPU preference is false, and no rental credentials or ready GPUs are configured initially.

## 6. Configure the wallet and spending allowance

`ops.py init` asks for the public mining wallet address. Enter an address you control, not a private key. Configuration is stored as:

```json
{
  "address": "YOUR_PUBLIC_EVM_WALLET_ADDRESS",
  "relay_ports": []
}
```

The placeholder above is explanatory and must be replaced through initialization before launch. The public address participates in every proof; changing it invalidates work for the old address. Use one dedicated project directory and ledger for each wallet. This package must not share a ledger across unrelated wallets.

| Limit | Value | Meaning |
| --- | --- | --- |
| Cumulative gas cap | 0.03 ETH | Spent fees plus pending reservations must fit within this cap |
| Per-nonce reservation cap | 0.01 ETH | Maximum gas reservation for one transaction nonce, including fee replacements |
| NFT transaction value | 0 ETH | No separate NFT purchase payment is sent by this claim path |

These are ceilings. The miner does not deliberately spend the full allowance per claim. The wallet must hold enough ETH on the configured chain for the reserved gas; a cap does not fund the wallet.

The private key is entered only after launch in the local panel. It must match the configured public address. Never place it in `deployment.json`, a command line, a source file, a worker, this Markdown, or a support log. Restarting the controller loses the in-memory account reference. Locking drops the reference, but Python does not guarantee physical memory zeroization.

For an existing wallet, follow the migration procedure before enabling automatic claims. A fresh ledger would forget earlier gas spending and unresolved transactions. The original implementation also accepts a legacy cumulative cap of 0.003 ETH and migrates it to this edition's 0.03 ETH while preserving spend and pending reservations; unexpected other ledger caps are rejected. Using this edition therefore requires accepting the displayed 0.03 ETH cap.

## 7. Connect and provision servers

### Add one endpoint

```bat
.venv\Scripts\python.exe ops.py add-server
```

Enter the rental hostname and forwarded SSH port, then enter the SSH password at the hidden prompt. Repeat for every authorized server. The importer writes encrypted credentials under the current Windows account. It does not accept or store a wallet private key.

Use unique forwarded port numbers within one installation. The current blocklist and several command selectors identify servers by port globally; two distinct hosts using the same port are ambiguous for these commands.

Add credentials while the controller is stopped for initial setup. On a running controller, adding a credential immediately makes the CPU worker eligible for launch, even before its binary exists. Missing binaries will produce reconnect attempts until provisioning succeeds. CPU readiness reports are informational; GPU activation has an explicit validation gate.

### Install native prerequisites

Verify that the provider reports the rental as deployed and that SSH authentication succeeds. On the authorized remote server, install the ordinary compiler tools if needed:

```sh
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y g++ python3
```

Do not reinstall a working NVIDIA host driver from inside a rental container. An advertised CUDA version in `nvidia-smi` describes driver compatibility, not proof that `nvcc` is installed.

### Deploy CPU workers

The examples use ports 1234 and 5678. Replace them with your actual enabled rental ports.

```bat
.venv\Scripts\python.exe deploy_cpu_selected.py 1234 5678
```

This entry point waits for an available compiler, compiles `cpu-linux.cpp`, checks three independent Keccak vectors on the target, records CPU information, and merges observed SSH host keys under a lock. Install compiler prerequisites first: its readiness wait is not a substitute for a working package manager.

Native build command executed on a target:

```sh
g++ -O3 -march=native -std=c++11 -pthread /root/prspct-cpu/cpu-linux.cpp -o /root/prspct-cpu/cpu-miner
```

The supervisor launches one CPU process per endpoint using the remote `nproc` value, bounded to 1-512 threads. There is no per-server CPU thread cap setting in this edition. If full CPU use starves GPU launch or other services, stop that deployment and make a deliberate, tested thread-policy change.

### Install CUDA tools and deploy GPUs

```bat
.venv\Scripts\python.exe prepare_cuda_tools.py 1234 5678
.venv\Scripts\python.exe deploy_gpu.py --ports 1234,5678
```

The CUDA installer downloads `cuda_nvcc`, `cuda_cudart`, and `cuda_cccl` from NVIDIA's [CUDA 12.4.1 redistributable manifest](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.4.1.json), verifies each archive against the manifest's SHA-256 value, and merges the tools into `/root/prspct-cuda`. A `lib64 -> lib` symlink handles the redistributable layout. This installs compilation dependencies; it does not repair a broken host driver.

The GPU deployment command compiles and validates each selected device. It only publishes a GPU to `gpu-ready.json` after validation passes. On a running controller, publication activates the device automatically.

Exact GPU compilation configuration:

```sh
/root/prspct-cuda/bin/nvcc \
  -O3 -std=c++14 -Xcompiler -pthread \
  -L/root/prspct-cuda/lib -L/root/prspct-cuda/lib64 \
  -gencode arch=compute_86,code=sm_86 \
  -gencode arch=compute_89,code=sm_89 \
  -gencode arch=compute_86,code=compute_86 \
  /root/prspct-cpu/gpu-miner.cu \
  -o /root/prspct-cpu/gpu-miner
```

The executable includes native Ampere/Ada code and forward-compatible PTX. The reference Blackwell devices used the PTX path. Compatibility must still be verified on each device and driver. This source does not include an AMD GPU or OpenCL backend.

## 8. Configure direct and relay connections

The default route is direct SSH. If that fails, `probe_servers.py` tries the authorized endpoints listed in `deployment.json` under `relay_ports`, in order. A relay transports an inner SSH connection to the target through `direct-tcpip` forwarding. The inner target password is handled by that SSH session, and the wallet signer stays local.

Example public configuration:

```json
{
  "address": "YOUR_PUBLIC_EVM_WALLET_ADDRESS",
  "relay_ports": [9000]
}
```

Port 9000 must refer to an existing authorized credential entry. The relay needs permission and connectivity to forward TCP to the target. A selected but missing relay cannot create connectivity on its own. Change relay configuration before starting the controller; this configuration is loaded at process import and does not hot-reload like fleet readiness.

For a deployment-only forced route:

```bat
.venv\Scripts\python.exe deploy_cpu_selected.py 1234 --relay 9000
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --relay 9000
```

`--relay` changes that deployment process only. Configure `relay_ports` as well when the production supervisor also needs the route. The CUDA installer uses the configured direct/fallback connection helper.

Avoid circular relay dependencies. If a relay is canceled, remove its entry from `relay_ports` and from credentials. A controller restart applies a changed relay list and requires signer key re-entry.

## 9. Validate and enable multiple GPUs

One GPU worker process is assigned to each validated CUDA ordinal. A two-card server usually uses ordinals 0 and 1, but faults can change this mapping. NVML indices reported by `nvidia-smi` are not guaranteed to match CUDA ordinals.

Before activating a multi-GPU machine, check device count, PCI bus IDs, UUIDs, and actual CUDA access:

```sh
nvidia-smi -L
nvidia-smi --query-gpu=index,name,uuid,pci.bus_id,memory.total --format=csv,noheader
```

A card listed by NVML may be unavailable to CUDA. The deployment helper attempts actual CUDA operations and stops on an invalid device. In a partially faulty machine, validate the working CUDA ordinals explicitly after confirming their physical mapping:

```bat
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --only-devices 0,1
```

The regular GPU validation path performs four random full-width hash comparisons, a short benchmark, three independently verified easy-target solutions, and a target-update regression proving the nonce counter continues forward. It waits for actual device output instead of assuming initialization completes within a fixed short delay.

Readiness has this shape:

```json
{
  "YOUR_RENTAL_HOST:1234": {
    "devices": [0, 1],
    "source_sha256": "SHA256_OF_THE_EXACT_GPU_SOURCE"
  }
}
```

Do not create readiness entries merely from advertised GPU counts. When device enumeration changes after a reset or redeployment, stop the affected workers, reconcile CUDA-to-physical mapping, and validate again. Otherwise, two logical entries can accidentally target one physical card.

### Worker protocol

All hexadecimal fields below are unprefixed and exact width:

```text
Input:
JOB <seed:64 hex> <wallet:40 hex> <target:64 hex> <prefix:48 hex>
STOP
QUIT

Output:
RATE <hashes_per_second> <total_hashes>
FOUND <nonce:64 hex> <digest:64 hex>
```

Example process commands:

```sh
/root/prspct-cpu/cpu-miner 16
/root/prspct-cpu/gpu-miner 0
/root/prspct-cpu/gpu-miner benchmark 0 3
```

`STOP` pauses the worker, `QUIT` exits, and input EOF exits. The diagnostic `--trace-jobs` switch emits counter starts for regression tests; production does not use it. Standalone benchmark or test processes should finish before the supervisor starts a production process on the same GPU.

## 10. Start mining and automatic claims

1. Complete dependency and offline tests.
2. Configure the intended public wallet and, if applicable, transfer the original ledger.
3. Recreate endpoint credentials on this computer.
4. Provision and validate the selected workers.
5. Run the preflight and confirm chain/contract/state results.
6. Start one controller.
7. Open its local panel and check address, rates, data age, budget, and pending transactions.
8. Enter the matching wallet key locally and enable automatic claims.

```bat
.venv\Scripts\python.exe ops.py preflight
.venv\Scripts\python.exe controller.py
```

Leave that terminal running. From a second terminal in the same directory:

```bat
.venv\Scripts\python.exe ops.py open-panel
.venv\Scripts\python.exe ops.py status
```

For later background operation, stop the foreground instance first, then use:

```bat
.venv\Scripts\python.exe start.py
.venv\Scripts\python.exe ops.py open-panel
```

`start.py` uses the interpreter that launches it, writes output to `service.log`, and starts the controller without a visible helper window. It is not a Windows service manager or a crash watchdog. The project does not install scheduled startup or monitoring tasks.

Mining starts without a signing key. Automatic claims remain off until manual local key entry. A loaded key and global mining state are separate controls. A successful transaction requires an accepted proof, sufficient balance and gas budget, a functioning network, and successful inclusion; an illuminated ON label alone does not prove a mint.

## 11. Transaction lifecycle and retries

The controller independently hashes every candidate, fetches fresh state, and checks the full 256-bit target. It simulates the zero-value claim, estimates gas, reads the wallet nonce, prepares a signed transaction, checks the chain and balance, simulates again, and reserves the maximum transaction cost in the ledger before broadcasting.

Initial gas limit is `ceil(estimate * 1.25)`, accepted only within 21,000-800,000. Initial legacy `gasPrice` is twice the RPC quote. Reservation is `gasLimit * gasPrice`. This is a conservative fee policy; it is not a guarantee of faster sequencing or successful execution.

```text
available_budget = total_cap - spent_fees - pending_reservations
new_reservation <= per_nonce_cap
new_reservation <= available_budget
wallet_balance >= new_reservation
```

Only one wallet nonce is pending at a time. Signed bytes, nonce, transaction fields, hash variants, and reservation are written to `spend-ledger.json` before network submission. An uncertain broadcast retains the reservation because the transaction may already have reached the network.

| Condition | Implemented behavior |
| --- | --- |
| RPC transport failure | Try configured fallback; retain a prepared candidate where the error is transient |
| Signer temporarily locked | Keep a valid candidate in memory and retry later |
| Another transaction pending | Keep the candidate and wait; do not select an unsafe competing nonce |
| Ambiguous broadcast | Keep reservation and inspect receipts |
| Pending transaction without receipt | Re-broadcast the same signed bytes after at least eight seconds when signing is enabled |
| Underpriced transaction after 30 seconds | Prepare a bounded fee replacement with the same nonce, destination, value, and calldata |
| Unknown transaction consumes nonce | Retain reservation; pause signer after the reconciliation delay |
| Pending proof rejected against both pending and latest state | Pause signer; retain reservation; do not automatically send a cancellation |
| Confirmed transaction | Account receipt fee once and record mint token IDs from logs |
| Reverted transaction | Account consumed gas; mark reverted; do not automatically replay the proof |

Fee replacement uses at least a 13% increase over the previous price, or twice the current quote if higher, while preserving both spending caps. All replacement hashes are tracked. Only one transaction with that nonce can execute on the canonical chain. The confirmed variant is charged once.

Receipt accounting waits until two additional blocks have been observed after inclusion. This is the application's confirmation threshold; it does not establish final settlement against every possible chain reorganization. The current ledger does not implement a complete deep-reorganization rollback system.

Actual charged gas is `gasUsed * effectiveGasPrice`. In the audited Nitro receipt model, parent-chain data gas is already included; the implementation does not add `gasUsedForL1` a second time. Token IDs come from contract `Transfer` events whose sender is zero and receiver is the configured wallet.

### Limits of automatic recovery

The unbroadcast candidate queue holds at most 16 entries plus one held candidate and is memory-only. Restarting loses those proofs. A candidate is discarded after an insufficient-balance error, an out-of-range gas estimate, an invalid hash, a stale proof, or a definitive contract rejection. Queue overflow also drops candidates. A mined revert is not automatically replayed. Stale pending transactions require reconciliation; no automatic self-send cancellation is implemented.

Temporary preparation failures use backoff capped at four seconds. Repeated signing attempts do not mean repeated successful mints. A replacement improves fee validity within limits; it cannot make an expired proof valid again. Keep adequate gas balance and avoid other software transacting from the same wallet during automatic claims.

## 12. Control the local CPU and remote fleet

```bat
.venv\Scripts\python.exe ops.py local-off
.venv\Scripts\python.exe ops.py local-on
.venv\Scripts\python.exe ops.py lock
.venv\Scripts\python.exe ops.py stop
.venv\Scripts\python.exe ops.py start
```

| Action | Effect |
| --- | --- |
| `local-off` | Disable this computer's CPU worker; remote hashing and signer continue |
| `local-on` | Enable local CPU when global mining is active |
| `lock` | Disable automatic signing and drop the account reference; hashing can continue |
| `stop` | Stop global hashing and automatic claims; account reference may remain in memory |
| `start` | Resume hashing; does not itself unlock or re-enable automatic signing |

To remove the account reference and stop work, use both `lock` and `stop`. Already broadcast transactions can still be mined after these commands. The controller can continue polling their receipts while it remains running.

The supplied `local-settings.json` disables local CPU at startup. Disabling an already active local CPU sends `STOP`; it does not kill the process or signer. Future work updates respect the preference. If the file is absent, the underlying controller defaults to enabling local CPU, so preserve the supplied setting.

`fleet.py` reloads credentials, the disabled-port list, and GPU readiness every three seconds. Adding a validated GPU, updating credentials, or removing an endpoint does not require restarting the signer. Source-code changes, wallet configuration changes, and relay-list changes do require restart.

### Remove a server

```bat
.venv\Scripts\python.exe ops.py remove-server YOUR_RENTAL_HOST 1234
```

The removal utility blocks the port, removes that exact host/port credential and readiness entry, and lets the supervisor retire its CPU/GPU workers. It does not contact the rental provider's billing API. Cancel the matching rental in the provider account separately and verify the order is closed. Stopping a mining process does not stop rental billing.

For the original deployment, endpoint 2801 was removed from the supervisor and credentials during this handover. Its provider-side cancellation could not be confirmed after the browser connection failed. Check the provider's order status before assuming its billing has stopped. No other fleet worker or signer was stopped by that removal.

## 13. Move an existing installation to another computer

### New operator with a new wallet

Extract the package into a new directory, initialize the operator's own public address, add their own rental credentials, validate workers, and enter their own key locally. The package has no preselected funded wallet. A new wallet requires new proofs because the address is part of the hash.

### Same wallet, same ongoing gas allowance

The nonce lock and spending budget are local to one controller. Port 8866 prevents duplicate instances on one computer only. It does not prevent a second computer from signing concurrently. Never operate two unlocked controllers for the same wallet.

1. Prepare the destination's dependencies and sources without starting a signer.
2. On the source computer, lock signing and stop mining.
3. Inspect pending transactions. Prefer waiting for receipts to settle. If a nonce remains unresolved, carry its complete pending record and reservation; do not delete it.
4. Shut down the source controller so it no longer changes the ledger or runs the fleet.
5. Privately copy the final `spend-ledger.json` intact to the destination project. Preserve all spent amounts, pending records, raw bytes, variants, nonce information, and reservations.
6. Copy the public wallet configuration, local CPU preference, disabled ports, verified SSH host keys, and readiness metadata through an appropriate private transfer. Review every surviving rental's identity and device mapping.
7. Re-enter SSH credentials on the destination using the hidden prompt. Recreate the DPAPI file there; do not assume the source machine's encrypted store is portable.
8. Do not copy `launch.json` as active runtime state. Start the destination and use its newly generated local launch URL.
9. Check that address, budget, total spent, and pending reservation match the source's final snapshot.
10. Confirm the old controller remains stopped, then manually enter the wallet key in the new local panel and enable automatic claims.

This public source document is sufficient to reconstruct the software. Restoring an existing wallet's exact operational state also requires its private ledger and authorized credentials. Omitting those inputs is not a budget reset mechanism. The wallet key cannot be recovered from this document or transferred out of the running controller by a supported command.

When upgrading the original earlier controller, note that a false local CPU setting on disk was prepared before the old process had loaded the new toggle implementation. A running process without the `local_cpu_enabled` status field has not applied that upgrade. Restart once at a planned handover point and re-enter the key; later local CPU toggles need no restart.

## 14. Monitoring, estimates, and rental economics

Use the panel and `ops.py status` to inspect:

- `mining`, `auto`, and `key_loaded` independently.
- Total and per-worker rate; local CPU rate separately.
- Chain state age, depth, candidate count, and event messages.
- Pending transaction count, total spent, and reserved gas.
- Confirmed token IDs and transaction receipts.

Remote rates older than ten seconds are displayed as zero. Work pauses when chain data becomes older than ten seconds or collection depth reaches 8888. RPC state is normally refreshed every two seconds. Reconnect waits grow through approximately 2, 4, 8, and 16 seconds; successful connection resets the backoff, so a process that repeatedly connects and immediately fails may retry more frequently.

### Hash rate and probability

For a uniform 256-bit hash and target `T`, the per-hash success probability is `T / 2^256`. At effective unique-work rate `H`, the approximate mean discovery time is:

```text
mean_seconds = 2^256 / (T * H)
expected_solutions_over_t = H * T * t / 2^256
probability_of_at_least_one_solution = 1 - exp(-expected_solutions_over_t)
```

These formulas assume a constant target, continuous unique hashing, and independent trials. They estimate discovery, not guaranteed inclusion or sale proceeds. Other miners can advance depth, reducing the target before a proof reaches the chain. The panel's mean uses the current contract target, whereas workers mine against the stricter 32-position-ahead target; the displayed mean is therefore optimistic for the work actually requested. Use the selected work target for a closer calculation.

Reference measurements for this particular implementation, under varying rental conditions, were roughly 0.8-1.1 GH/s on smaller supported cards, 2-3 GH/s on RTX 4080 SUPER/5080, 4-5 GH/s on RTX 4090, and 4.5-5.8 GH/s on RTX 5090. These are observations, not guaranteed model specifications. Benchmark the actual rented device and compare sustained rates after CPU and GPU workers are both active.

### Budget runway

```text
hourly_rental_cost = daily_rental_cost / 24
remaining_hours = rental_account_balance / hourly_rental_cost
cost_per_GH_hour = hourly_rental_cost / sustained_GH_per_second
```

Keep rental credit and wallet gas balance separate. Fleet hourly fees continue during outages until the provider stops the orders. A low gas cost per mint does not make an expensive rental profitable. Economic profit requires realized sale proceeds or otherwise realizable value to exceed rental cost, actual chain fees, and trading fees. No resale or NFT price is assumed by this software.

## 15. Troubleshooting and recovery

| Symptom | Likely issue | Next step |
| --- | --- | --- |
| SSH timeout while rental is deploying | Forwarding or container not ready | Confirm deployed state and exact forwarded port; retry with bounded waits |
| Authentication failed | Wrong credential or reassigned rental | Recheck current provider credentials; do not guess passwords |
| Direct connection fails | Routing restriction | Use an authorized reachable relay and configure production fallback |
| Host key mismatch | Endpoint identity changed | Verify current rental fingerprint and update only that known-host entry |
| CPU worker immediately reconnects | Missing binary, incompatible CPU instructions, or runtime library | Inspect stderr and validate a locally built or compatible target binary |
| `nvcc` missing | Toolkit absent | Install official redistributables or use validated compatible binary |
| Linker cannot find CUDA static libraries | Redistributable library layout | Verify explicit library paths and `lib64 -> lib` |
| Unsupported PTX version | Driver/toolkit mismatch | Use a compatible toolchain/native target, then repeat validation |
| GPU requires reset or unknown device error | Driver/device unavailable at host level | Stop affected worker; ask provider for a scoped recovery |
| Four NVML cards but three CUDA devices | Failed device or visibility mismatch | Reconcile physical UUID/PCI identity with CUDA ordinals |
| `Text file busy` during deployment | Old executable inode held open | Write a new candidate and atomically rename it; do not overwrite a running inode |
| SFTP upload stalls | Remote SFTP implementation or network | Use validated binary cloning's compressed SSH-stdin transfer |
| Broken package repository | Image package-manager configuration | Repair deliberately or use a compatible validated donor binary |
| Healthy hash rate but no mint | Statistical variance, stricter target, difficulty change, locked signer | Check state, target, signer flags, balance, events, and receipts |
| `Wrong chain` or unexpected state ABI | RPC/configuration/deployment mismatch | Keep signer locked until the exact network and contract are verified |
| Budget mismatch | Incompatible or damaged ledger | Preserve file; reconcile it instead of creating a blank replacement |
| Unknown consumed nonce | Another transaction or lagging receipt | Inspect all variants and wallet history; preserve reservation |
| Panel unauthorized after restart | Old launch token or fragment-only navigation | Open the newly generated URL and fully reload the panel |

A container can run as root while lacking the capabilities required for a GPU reset. Do not confuse device reset with rebooting the entire server, reloading the driver, or resetting a PCI bus. Those actions may stop healthy GPUs and other workloads. A provider-managed device fault cannot be fixed by changing this hash kernel.

### Binary recovery and audit

```bat
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --existing
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --clone-from 5678
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --audit
```

Cloning checks that the donor's CUDA source hash matches this source revision, transfers its executable, and still validates the receiving GPU. A source hash match alone does not establish binary integrity or compatibility; use a trusted donor and compare binary hashes when transferring privately.

An executable built with `-march=native` may fail on another CPU. A donor CPU build using `-march=x86-64-v3` requires a compatible AVX2-class target and compatible runtime libraries. For broad portability, rebuild on the target when possible and repeat independent hash vectors.

## 16. Update, stop, and decommission

### GPU-only update

```bat
.venv\Scripts\python.exe deploy_gpu.py --ports 1234 --replace
```

This compiles a candidate, validates it, atomically installs it, and restarts the selected server's production GPU worker processes. It does not restart the local signer or CPU fleet. The replacement path tests one random hash vector per selected device plus real proof and counter-continuity checks. Run a full initial validation for new devices.

Replacement affects every production GPU worker on the selected host. This handover edition rejects partial-device selection with `--replace` and rejects invalid device indices before installation. Use `--only-devices` for initial validation of known working CUDA ordinals; recover or deliberately isolate a faulty device before performing a shared-binary replacement.

### Controller update

Check pending receipts, lock signing, stop mining, and exit the controller. Back up its private ledger. Replace sources in a separate reviewed release directory, preserve the required operational state, run offline tests, and start the new version. Re-enter the key manually. Do not overwrite executable or state files blindly while a signer is active.

For a foreground controller, use Ctrl+C in its own terminal after locking/stopping. For a background instance, inspect `launch.json` to identify its PID and verify that PID belongs to this project's Python controller before ending it. Never terminate every Python process on the computer.

### Complete shutdown

1. Lock the signer and stop mining.
2. Reconcile pending transactions; broadcasts can still confirm after shutdown.
3. Save the final spending ledger and necessary private operational records.
4. Stop the controller and verify worker sessions/processes have ended.
5. Cancel each unwanted provider order and verify closed billing status.
6. Remove canceled endpoints from active credentials/readiness and preserve the blocklist.

The controller has no built-in provider-order cancellation, balance top-up, NFT listing, NFT transfer, token approval, or trading automation. Those operations require separate, explicit operator action.

## 17. API and configuration reference

All API calls require the current `X-Panel-Token`, exact host `127.0.0.1:8866`, and an absent or matching local origin. The operations utility reads the launch token locally without printing it.

| Method | Route | Body | Behavior |
| --- | --- | --- | --- |
| GET | `/api/status` | None | Operational fields; excludes private key and raw signed transaction bytes |
| POST | `/api/start` | `{}` | Enable hashing |
| POST | `/api/stop` | `{}` | Disable hashing and automatic claims |
| POST | `/api/lock` | `{}` | Disable signer and drop account reference |
| POST | `/api/unlock` | Local panel key entry | Verify wallet match and enable automatic claims |
| POST | `/api/local-cpu` | `{"enabled": false}` | Disable local CPU independently |

Do not script the private-key API call into a shell history or shareable file. The provided operations CLI intentionally has no unlock command.

| File | Contents | Reload behavior |
| --- | --- | --- |
| `deployment.json` | Public wallet address and ordered relay ports | Controller restart |
| `local-settings.json` | Strict boolean `local_cpu_enabled` | Startup; use API for live changes |
| `ssh-secrets.dpapi` | Encrypted authorized endpoint credentials | Approximately every three seconds |
| `disabled-ports.json` | Blocked integer port numbers | Approximately every three seconds |
| `gpu-ready.json` | Validated CUDA ordinals per host/port | Approximately every three seconds |
| `ssh-known-hosts` | Trusted/observed SSH host identities | Read when establishing connections |
| `spend-ledger.json` | Fees, pending reservations, signed recovery records | Loaded at startup; controlled writes while running |
| `launch.json` | Current PID and tokenized panel URL | Generated at startup |
| `service.log` | Background stdout/stderr | Appended during background operation |

GPU readiness and host-key merges use file locks and atomic replacement. Credential and configuration edits should be performed by one operator at a time. The current ledger has no cross-computer lock or automatic wallet-address binding, so migration discipline is required.

## 18. Verification record and limitations

The reference deployment verified GPU proofs on actual devices, nonce continuity across target updates, CPU hash vectors, dynamic fleet addition/removal, and successful contract mint receipts. The prepared source's offline suite covers 32 tests across `test_miner`, `test_fleet_audit`, and `test_local_cpu`.

The handover edition additionally includes source checksums, extraction validation, Python syntax checks, and verification of the English panel and operational wrapper. These checks do not constitute a new successful mint from the recipient's wallet or a hardware test of newly rented machines. Run target-device validation and read-only contract checks on the recipient's installation before key entry.

This is an operating implementation with explicit limits: one local signer, one pending nonce, memory-only unbroadcast candidates, Windows-only controller storage/locking, password-based SSH, no deep-reorganization ledger rollback, and no provider billing API. Automatic claims can fail or become stale. Readiness, queue counts, and projected means do not guarantee an NFT or a financial return.

The test suite uses public, deterministic dummy keys to exercise signing code without network submission. Never fund those test identities. No operator key or original wallet credential is included.

The complete source appendix below is the implementation authority for this handover edition. Provisioning utilities can change remote binaries and package dependencies; inspect selected ports and deployment mode before invoking them. A read-only audit and an operational replacement are different commands.

## 19. Source manifest and complete source appendix

Every embedded file is UTF-8 with LF line endings and a final newline. The extractor verifies the SHA-256 value before writing. The source appendix follows the generated manifest.

| File | SHA-256 |
| --- | --- |
| `requirements.txt` | `0d7bb2889bbb0c946f705523cedbd6bc780d86e9557067c7a99067cd2ecc95c5` |
| `local-settings.json` | `81602c64b95a29c999e45f9becf0f81331c60c16336448719dd349fde6808469` |
| `disabled-ports.json` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| `controller.py` | `77e2f058aa3b0aae2f48010b6567b8205b9f1b4f6ca7a844e7a5a43cc48a6212` |
| `fleet.py` | `57a5691f2e9fe71c583e27e252beb98861bba4dc40f89248e3f11c871dc52731` |
| `probe_servers.py` | `4e8ea1413ac17d4e0ab2f691d8fbba26fed16003da6c0dec5062ae91a7006a5a` |
| `setup_credentials.py` | `9cd1a20a4e8319053b31efcfe93b01c1dd2cd5f3ad6c0bb44876e63df244c1f7` |
| `ops.py` | `50c933b831b15a2badec60cdc44daff601785433334f6d5806ca4cdf5e185d34` |
| `panel.html` | `4d862928b2413440c4af3de4e47098afaa21a9d5f9fa8c6803a1db36171fc87c` |
| `CpuMiner.cs` | `0c4044dcdea2598f250db9997579134901e2e5ae737a7bb56334b6d6f3e94fd3` |
| `cpu-linux.cpp` | `b70735b5ca55faff921bb7b04bc443ce15aa7fe237589d7e501460544a5a187c` |
| `gpu-miner.cu` | `954e969bb7d4954b6c009f53c60686d4bd50a6f3a27aa08385370e0c26212a40` |
| `deploy_cpu_selected.py` | `c8957ef972543ada0ae5af06ef8fc8ce72a742bfc69cb554bd63262ea0f2ea26` |
| `deploy_cpu_servers.py` | `ee8a6c4cba778d7cc3c105cf989050513f86cb0f38c89a6b61b8153f98bde4d5` |
| `deploy_new_cpu_fleet.py` | `b522763b13d69c45491e3d91c5c4ae174c7752c2c0eb932fdb0dbbf111144590` |
| `prepare_cuda_tools.py` | `f67834363bcff594f77cc3e5509063ac892f38bc8eecb5ff79b6b07d3592e139` |
| `merge_gpu_metadata.py` | `aa232ac12142cd4da18732f637f4c0163a4929383b6d16e59933039a75eddd4b` |
| `deploy_gpu.py` | `61b26e1b35ab66795b233843ceb13a2710053e1ff583a5c72a7522638b46eb5d` |
| `start.py` | `7cc6ab6d67c714b694fa22f8c09e86cd4602ceb99f0672ae16e4fdf333c0e239` |
| `test_miner.py` | `6b0f237b0a5e47102c0c46c84c5397c77ceb174de85d50f977cffdad1f2d5d60` |
| `test_fleet_audit.py` | `2100f7b9fbaf321e003d64fd5ac02314988b9cbf968046c594a7f63e948c3791` |
| `test_local_cpu.py` | `74bd792f7e6c384a98b4d42dcb272e0d9db23595d4dee9f46516ce6ade8e5072` |

### requirements.txt

<!-- FILE: requirements.txt SHA256: 0d7bb2889bbb0c946f705523cedbd6bc780d86e9557067c7a99067cd2ecc95c5 -->
````text
pycryptodome==3.23.0
eth-account==0.14.0
paramiko==5.0.0
````
<!-- END FILE -->

### local-settings.json

<!-- FILE: local-settings.json SHA256: 81602c64b95a29c999e45f9becf0f81331c60c16336448719dd349fde6808469 -->
````json
{
  "local_cpu_enabled": false
}
````
<!-- END FILE -->

### disabled-ports.json

<!-- FILE: disabled-ports.json SHA256: 37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570 -->
````json
[]
````
<!-- END FILE -->

### controller.py

<!-- FILE: controller.py SHA256: 77e2f058aa3b0aae2f48010b6567b8205b9f1b4f6ca7a844e7a5a43cc48a6212 -->
````python
"""Local PRSPCT CPU mining and zero-value claims. Key exists only in RAM."""
import sys, os, json, time, secrets, threading, queue, subprocess, urllib.request, urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from decimal import Decimal

ROOT=Path(__file__).resolve().parent
from Crypto.Hash import keccak
from eth_account import Account

CONTRACT='0xd078008c3D887A52CE722A3cA0539cA1F4971dD1'
CONFIG=json.loads((ROOT/'deployment.json').read_text(encoding='utf-8')) if (ROOT/'deployment.json').exists() else {}
ADDRESS=CONFIG.get('address','0x0000000000000000000000000000000000000000')
CHAIN=4663
RPCS=['https://rpc.mainnet.chain.robinhood.com/','https://robinhood.drpc.org']
BUDGET=30_000_000_000_000_000  # User-authorized total 0.03 ETH; spent amounts survive restarts.
PER_TX=10_000_000_000_000_000  # User-authorized maximum 0.01 ETH per nonce.
PORT=8866
LEDGER=ROOT/'spend-ledger.json'
LOCAL_SETTINGS=ROOT/'local-settings.json'
def kh(b):return keccak.new(digest_bits=256,data=b).digest()
def data(sig,*words):return '0x'+kh(sig.encode())[:4].hex()+''.join(int(w).to_bytes(32,'big').hex() for w in words)
class RpcRejected(RuntimeError):pass
class CandidateDeferred(RuntimeError):pass
def rpc(method,params):
    last=None
    for url in RPCS:
        try:
            req=urllib.request.Request(url,data=json.dumps(dict(jsonrpc='2.0',id=1,method=method,params=params)).encode(),headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req,timeout=4) as r:out=json.load(r)
            if 'error' in out:
                msg=str(out['error'].get('message','error'))[:160]
                if 'revert' in msg.lower():raise RpcRejected('Contract rejected '+method+': '+msg)
                last=RuntimeError(msg);continue
            return out['result']
        except (urllib.error.URLError,TimeoutError,OSError) as e:last=e
    raise RuntimeError('RPC unavailable: '+type(last).__name__)
def call(sig,*args):return rpc('eth_call',[{'to':CONTRACT,'data':data(sig,*args)},'latest'])
def read_state():
    raw=bytes.fromhex(call('state()')[2:])
    if len(raw)!=448:raise RuntimeError('Unexpected state ABI')
    w=[int.from_bytes(raw[i:i+32],'big') for i in range(0,len(raw),32)]
    if not 0<=w[0]<=8888 or not w[1]:raise RuntimeError('Mine not open or unexpected state')
    return dict(depth=w[0],seed=w[1].to_bytes(32,'big').hex(),target=w[4],price=w[3],read_at=time.time())
def receipt_cost(r):
    # Arbitrum Nitro: gasUsed already includes gasUsedForL1. Do not double-count it.
    return int(r['gasUsed'],16)*int(r['effectiveGasPrice'],16)
def write_json(path,obj):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2),encoding='utf-8');os.replace(tmp,path)
def load_local_cpu_enabled():
    if not LOCAL_SETTINGS.exists():return True
    settings=json.loads(LOCAL_SETTINGS.read_text(encoding='utf-8'))
    if not isinstance(settings,dict) or type(settings.get('local_cpu_enabled')) is not bool:
        raise RuntimeError('Invalid local CPU setting')
    return settings['local_cpu_enabled']
def load_ledger():
    if not LEDGER.exists():return {'budget':BUDGET,'spent':0,'transactions':[]}
    x=json.loads(LEDGER.read_text(encoding='utf-8'))
    if not isinstance(x['spent'],int) or x['spent']<0:raise RuntimeError('Ledger invalid')
    if x['budget']==3_000_000_000_000_000 and BUDGET==30_000_000_000_000_000:
        x['budget']=BUDGET;write_json(LEDGER,x) # Explicitly authorized increase; preserve all spending.
    if x['budget']!=BUDGET:raise RuntimeError('Ledger budget mismatch')
    return x
def reserve_ok(ledger,reserved):
    used=ledger['spent']+sum(t['reserved'] for t in ledger['transactions'] if t['status']=='pending')
    return 0<reserved<=PER_TX and used+reserved<=BUDGET
def validate_nonce(st,nonce,digest=None):
    n=bytes.fromhex(nonce)
    if len(n)!=32:raise ValueError('Invalid nonce length')
    h=kh(bytes.fromhex(st['seed'])+bytes.fromhex(ADDRESS[2:])+n)
    if digest is not None and h.hex()!=digest:raise ValueError('Worker hash mismatch')
    if int.from_bytes(h,'big')>=st['target']:raise ValueError('Solution no longer meets target')
    return h

class Controller:
    def __init__(self):
        self.lock=threading.RLock();self.token=secrets.token_urlsafe(32);self.account=None;self.auto=False
        self.ledger=load_ledger();self.state=None;self.events=[];self.proc=None;self.mining=False
        self.rate=0;self.total=0;self.found=0;self.solutions=queue.Queue(maxsize=16);self.stop_event=threading.Event()
        self.threads=max(1,min(8,(os.cpu_count() or 2)-1));self.prefix=secrets.token_hex(24);self.last_work=None
        self.local_cpu_enabled=load_local_cpu_enabled()
        self.remote={};self.address_hex=ADDRESS[2:]
    def log(self,msg):
        with self.lock:self.events=(self.events+[{'time':time.strftime('%H:%M:%S'),'message':msg}])[-80:]
    def status(self):
        with self.lock:
            st=self.state or {};pending=[t for t in self.ledger['transactions'] if t['status']=='pending']
            fleet={k:dict(v,rate=v['rate'] if time.time()-v['updated']<10 else 0) for k,v in self.remote.items()}
            local_enabled=getattr(self,'local_cpu_enabled',True)
            local_rate=self.rate if local_enabled and self.mining else 0
            local_mining=bool(local_enabled and self.mining and self.proc and self.proc.poll() is None)
            total_rate=local_rate+sum(v['rate'] for v in fleet.values())
            return dict(address=ADDRESS,contract=CONTRACT,chain=CHAIN,mining=self.mining,auto=self.auto,key_loaded=self.account is not None,
                threads=self.threads if local_enabled else 0,local_threads_configured=self.threads,
                local_cpu_enabled=local_enabled,local_mining=local_mining,
                rate=total_rate,local_rate=local_rate,workers=fleet,total=self.total,found=self.found,depth=st.get('depth'),
                state_age=round(time.time()-st['read_at'],1) if st else None,
                expected_seconds=((2**256)/st['target']/total_rate) if st.get('target') and total_rate else None,
                budget_eth=str(Decimal(BUDGET)/10**18),per_tx_eth=str(Decimal(PER_TX)/10**18),spent_eth=str(Decimal(self.ledger['spent'])/10**18),
                reserved_eth=str(Decimal(sum(t['reserved'] for t in pending))/10**18),
                pending=len(pending),transactions=[{k:v for k,v in t.items() if k!='raw'} for t in self.ledger['transactions'][-30:]],events=self.events[-30:])
    def command(self,line):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(line+'\n');self.proc.stdin.flush()
        else:raise RuntimeError('CPU worker is not running')
    def ensure_local_worker(self):
        # Called under self.lock. The local process is optional; fleet/signing
        # stay active if it is disabled or cannot start.
        if not getattr(self,'local_cpu_enabled',True) or not self.mining:return
        if self.proc is None or self.proc.poll() is not None:
            self.proc=subprocess.Popen([str(ROOT/'CpuMiner.exe'),str(self.threads)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.prefix=secrets.token_hex(24);self.last_work=None
            threading.Thread(target=self.worker_output,args=(self.proc,),daemon=True).start()
    def stop_local_work(self):
        self.rate=0;self.last_work=None
        if self.proc and self.proc.poll() is None:
            try:self.command('STOP')
            except (OSError,RuntimeError) as exc:self.log('Local CPU stop: '+type(exc).__name__)
    def set_local_cpu_enabled(self,enabled):
        if type(enabled) is not bool:raise ValueError('enabled must be a boolean')
        with self.lock:
            write_json(LOCAL_SETTINGS,{'local_cpu_enabled':enabled})
            self.local_cpu_enabled=enabled
            if enabled:
                self.last_work=None
                try:self.ensure_local_worker()
                except Exception as exc:self.log('Local CPU start: '+type(exc).__name__)
                self.log('Local CPU enabled' if self.mining else 'Local CPU enabled for the next mining start')
            else:
                self.stop_local_work()
                self.log('Local CPU disabled; remote workers and signer continue')
    def start(self):
        with self.lock:
            if self.mining:return
            self.last_work=None;self.mining=True
            try:self.ensure_local_worker()
            except Exception as exc:self.log('Local CPU start: '+type(exc).__name__)
            self.log('Mining enabled; mint value is fixed at 0 ETH')
    def stop(self):
        with self.lock:
            self.mining=False;self.auto=False;self.stop_local_work()
            self.log('Mining and auto-claim stopped')
    def unlock(self,key):
        try:account=Account.from_key(key)
        except Exception:raise ValueError('Invalid private key') from None
        if account.address.lower()!=ADDRESS.lower():raise ValueError('Key does not match the displayed mining address')
        with self.lock:
            if self.ledger['spent']>=BUDGET:raise ValueError('Gas budget exhausted')
            self.account=account;self.auto=True;self.log('Local signer enabled; key is held only in RAM')
    def disable(self):
        with self.lock:self.auto=False;self.account=None;self.log('Signer locked; CPU mining can continue')
    def worker_output(self,proc):
        for line in proc.stdout:
            f=line.strip().split()
            if len(f)==3 and f[0]=='RATE':
                with self.lock:
                    if self.proc is proc:
                        self.rate=float(f[1]) if self.mining and getattr(self,'local_cpu_enabled',True) else 0;self.total=int(f[2])
            elif len(f)==3 and f[0]=='FOUND':
                with self.lock:self.found+=1
                try:self.solutions.put_nowait((f[1],f[2]))
                except queue.Full:self.log('Candidate queue full; continuing search')
        with self.lock:
            if self.proc is proc:
                self.rate=0;self.last_work=None
                self.log('Local CPU worker exited; remote workers and signer continue')
    def update_work(self):
        while not self.stop_event.is_set():
            try:
                if int(rpc('eth_chainId',[]),16)!=CHAIN:raise RuntimeError('Wrong chain')
                st=read_state()
                # Aim 32 feet ahead to allow submission time while other miners advance.
                target=int(call('targetOf(uint256,uint256)',min(8887,st['depth']+32),0),16) if st['depth']<8888 else 0
                st['work_target']=target
                with self.lock:
                    if self.state and st['depth']<self.state['depth']:raise RuntimeError('RPC returned older depth')
                    self.state=st
                    if st['depth']>=8888:
                        if self.mining:self.stop();self.log('All 8888 NFTs issued')
                    elif self.mining and getattr(self,'local_cpu_enabled',True):
                        self.ensure_local_worker()
                        work=(st['seed'],target)
                        if work!=self.last_work:
                            self.command('JOB '+st['seed']+' '+ADDRESS[2:]+' '+target.to_bytes(32,'big').hex()+' '+self.prefix)
                            self.last_work=work
            except Exception as e:
                self.log(str(e)[:180])
                with self.lock:
                    if self.mining and (not self.state or time.time()-self.state['read_at']>10):
                        self.stop_local_work()
            self.stop_event.wait(2)
    def check_receipts(self):
        with self.lock:pending=[t.copy() for t in self.ledger['transactions'] if t['status']=='pending']
        for t in pending:
            r=None
            for variant in t.get('variants',[{'hash':t['hash']}]):
                r=rpc('eth_getTransactionReceipt',[variant['hash']])
                if r:break
            if not r:
                self.retry_pending(t);continue
            # Leave reservation until two blocks are observed after inclusion.
            if int(rpc('eth_blockNumber',[]),16)<int(r['blockNumber'],16)+2:continue
            cost=receipt_cost(r)
            nft_ids=[]
            for log in r.get('logs',[]):
                topics=log.get('topics',[])
                if log.get('address','').lower()==CONTRACT.lower() and len(topics)==4 and topics[0]=='0x'+kh(b'Transfer(address,address,uint256)').hex() and int(topics[1],16)==0 and int(topics[2],16)==int(ADDRESS,16):nft_ids.append(int(topics[3],16))
            with self.lock:
                item=next(x for x in self.ledger['transactions'] if x['hash']==t['hash'])
                if item['status']!='pending':continue
                item.update(status='confirmed' if int(r['status'],16)==1 else 'reverted',cost=cost,nft_ids=nft_ids,mined_hash=r.get('transactionHash',t['hash']))
                self.ledger['spent']+=cost;write_json(LEDGER,self.ledger)
                self.log(item['status']+' '+t['hash']+' NFTs '+str(nft_ids))
                if cost>item['reserved'] or self.ledger['spent']>=BUDGET:
                    self.auto=False;self.log('Spending guard stopped auto-claim')
    def retry_pending(self,t):
        now=time.time()
        with self.lock:
            if not self.auto or now-t.get('last_retry',t['created'])<8:return
        # If wallet nonce moved, a receipt may be propagating or another app replaced it.
        # Keep the reservation; never guess a new nonce in this case.
        if int(rpc('eth_getTransactionCount',[ADDRESS,'latest']),16)>t['nonce']:
            with self.lock:
                item=next(x for x in self.ledger['transactions'] if x['hash']==t['hash'])
                first=item.setdefault('nonce_moved_at',now);write_json(LEDGER,self.ledger)
                if now-first>60:
                    self.auto=False;self.log('Nonce consumed but receipt unavailable; signer paused for reconciliation')
            return
        raw=t['raw'];tx=t.get('tx')
        # Underpriced pending claim: replacement has SAME nonce, destination, calldata and value.
        if tx and now-t['created']>30:
            price=max(int(rpc('eth_gasPrice',[]),16)*2,(tx['gasPrice']*113+99)//100)
            new_reserved=tx['gas']*price
            with self.lock:
                account=self.account
                other=self.ledger['spent']+sum(x['reserved'] for x in self.ledger['transactions'] if x['status']=='pending' and x['hash']!=t['hash'])
            if account and new_reserved<=PER_TX and other+new_reserved<=BUDGET:
                try:
                    rpc('eth_call',[{'from':ADDRESS,'to':CONTRACT,'value':'0x0','data':tx['data']},'pending'])
                    changed=dict(tx,gasPrice=price);signed=account.sign_transaction(changed);raw='0x'+bytes(signed.raw_transaction).hex()
                    h='0x'+kh(bytes(signed.raw_transaction)).hex()
                    with self.lock:
                        if not self.auto:return
                        item=next(x for x in self.ledger['transactions'] if x['hash']==t['hash'])
                        item.setdefault('variants',[{'hash':t['hash']}]).append({'hash':h})
                        item.update(raw=raw,tx=changed,reserved=new_reserved,created=now)
                        write_json(LEDGER,self.ledger)
                    self.log('Fee replacement prepared with the same nonce: '+h)
                except RpcRejected:
                    # Pending state can already contain our own claim. Confirm the
                    # rejection against latest before pausing for reconciliation.
                    try:rpc('eth_call',[{'from':ADDRESS,'to':CONTRACT,'value':'0x0','data':tx['data']},'latest'])
                    except RpcRejected:
                        with self.lock:
                            self.auto=False
                            self.log('Pending claim now rejected by contract; signer paused. Reservation retained; reconcile nonce '+str(t['nonce'])+' before resuming. No cancellation sent.')
                        return
        with self.lock:
            if not self.auto:return
            item=next(x for x in self.ledger['transactions'] if x['hash']==t['hash'])
            item['last_retry']=now;item['retries']=item.get('retries',0)+1;write_json(LEDGER,self.ledger)
        try:rpc('eth_sendRawTransaction',[raw]);self.log('Pending claim rebroadcast with the same nonce')
        except Exception:pass # Known/temporarily unavailable: receipt remains the source of truth.
    def submit(self,nonce,digest):
        with self.lock:
            if not self.auto or not self.account:
                raise CandidateDeferred('Signer locked; candidate retained. Enter key locally to enable claim.')
            if any(t['status']=='pending' for t in self.ledger['transactions']):
                raise CandidateDeferred('Waiting for pending claim; candidate retained')
        st=read_state();validate_nonce(st,nonce,digest)
        payload={'from':ADDRESS,'to':CONTRACT,'value':'0x0','data':data('claim(uint256)',int(nonce,16))}
        rpc('eth_call',[payload,'pending'])
        estimated=int(rpc('eth_estimateGas',[payload]),16)
        gas=(estimated*125+99)//100
        gasprice=int(rpc('eth_gasPrice',[]),16)*2
        if gas<21000 or gas>800000 or gasprice<=0:raise ValueError('Unexpected gas estimate')
        seq=int(rpc('eth_getTransactionCount',[ADDRESS,'pending']),16)
        if seq!=int(rpc('eth_getTransactionCount',[ADDRESS,'latest']),16):raise CandidateDeferred('Wallet has another pending transaction; candidate retained')
        tx={'chainId':CHAIN,'nonce':seq,'to':CONTRACT,'value':0,'data':payload['data'],'gas':gas,'gasPrice':gasprice}
        with self.lock:
            if not self.auto or not self.account:raise CandidateDeferred('Signer locked; candidate retained')
            signed=self.account.sign_transaction(tx)
        raw=bytes(signed.raw_transaction)
        # Nitro eth_estimateGas includes both execution and parent-chain data costs.
        reserved=gas*gasprice
        if int(rpc('eth_chainId',[]),16)!=CHAIN:raise ValueError('Wrong signing chain')
        balance=int(rpc('eth_getBalance',[ADDRESS,'pending']),16)
        # Repeat simulation after all preparation; do not submit stale work.
        rpc('eth_call',[payload,'pending'])
        txhash='0x'+kh(raw).hex()
        with self.lock:
            if not self.auto or not self.account:raise CandidateDeferred('Signer locked; candidate retained')
            if not reserve_ok(self.ledger,reserved):
                self.auto=False;self.log('Gas cap or total budget reached; auto-claim stopped');return
            if balance<reserved:raise ValueError('Not enough ETH for reserved gas')
            item={'hash':txhash,'nonce':seq,'reserved':reserved,'status':'pending','created':time.time(),'raw':'0x'+raw.hex(),'tx':tx,'variants':[{'hash':txhash}]}
            self.ledger['transactions'].append(item)
            # Persist before sending. Uncertain network outcomes never release a reservation.
            write_json(LEDGER,self.ledger)
        try:
            got=rpc('eth_sendRawTransaction',['0x'+raw.hex()])
            if got.lower()!=txhash.lower():raise ValueError('Unexpected transaction hash')
            self.log('Claim broadcast: '+txhash)
        except Exception:
            self.log('Broadcast outcome uncertain; reservation kept. Waiting for '+txhash)
    def signer_loop(self):
        # One held candidate plus the bounded queue; temporary contention and RPC
        # outages must not destroy an otherwise valid proof. Receipts are checked
        # between every preparation attempt, with at most four seconds backoff.
        candidate=None;attempt=0;deferred_reason=None
        while not self.stop_event.is_set():
            try:self.check_receipts()
            except Exception as e:self.log('Receipt check: '+str(e)[:140])
            if candidate is None:
                try:candidate=self.solutions.get(timeout=2)
                except queue.Empty:continue
                attempt=0;deferred_reason=None
            nonce,digest=candidate
            try:
                with self.lock:st=self.state
                if st and time.time()-st['read_at']<10:
                    if st['depth']>=8888:raise ValueError('All NFTs issued')
                    validate_nonce(st,nonce,digest)
                self.submit(nonce,digest)
                candidate=None
            except CandidateDeferred as e:
                reason=str(e)[:160]
                if reason!=deferred_reason:self.log(reason);deferred_reason=reason
                self.stop_event.wait(2)
            except (ValueError,RpcRejected) as e:
                self.log('Candidate skipped: '+str(e)[:160]);candidate=None
            except Exception as e:
                attempt+=1
                self.log('Preparation retry '+str(attempt)+'; candidate retained: '+str(e)[:140])
                self.stop_event.wait(min(4,.4*2**min(attempt-1,4)))

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass # Never log request bodies, query strings or keys.
    def respond(self,code,obj,ctype='application/json'):
        b=(json.dumps(obj).encode() if ctype=='application/json' else obj.encode())
        self.send_response(code);self.send_header('Content-Type',ctype+'; charset=utf-8');self.send_header('Content-Length',str(len(b)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers();self.wfile.write(b)
    def allowed(self):
        return self.headers.get('Host')=='127.0.0.1:'+str(PORT)
    def authorized(self):
        origin=self.headers.get('Origin')
        return self.allowed() and origin in (None,'http://127.0.0.1:'+str(PORT)) and secrets.compare_digest(self.headers.get('X-Panel-Token',''),self.server.control.token)
    def do_GET(self):
        if not self.allowed():return self.respond(403,{'error':'Host denied'})
        if self.path=='/':return self.respond(200,(ROOT/'panel.html').read_text(encoding='utf-8'),'text/html')
        if self.path=='/api/status' and self.authorized():return self.respond(200,self.server.control.status())
        self.respond(403,{'error':'Open the launch URL'})
    def do_POST(self):
        if not self.authorized():return self.respond(403,{'error':'Denied'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=2048:raise ValueError('Invalid request size')
            body=json.loads(self.rfile.read(n));c=self.server.control
            if self.path=='/api/unlock':c.unlock(body.get('key',''));body.clear()
            elif self.path=='/api/lock':c.disable()
            elif self.path=='/api/start':c.start()
            elif self.path=='/api/stop':c.stop()
            elif self.path=='/api/local-cpu':c.set_local_cpu_enabled(body.get('enabled'))
            else:raise ValueError('Unknown action')
            self.respond(200,{'ok':True})
        except ValueError as e:self.respond(400,{'error':str(e)})
        except Exception:self.respond(500,{'error':'Operation failed; check panel events'})

def main():
    if ADDRESS=='0x0000000000000000000000000000000000000000':
        raise SystemExit('Run python ops.py init before starting the controller.')
    # Bind first: a second instance must not launch competing miners/signers.
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    c=Controller();server.control=c
    write_json(ROOT/'launch.json',{'url':'http://127.0.0.1:'+str(PORT)+'/#'+c.token,'pid':os.getpid()})
    c.log('Ready. Auto-claim OFF until key is entered locally. Budget 0.03 ETH.')
    threading.Thread(target=c.update_work,daemon=True).start();threading.Thread(target=c.signer_loop,daemon=True).start()
    c.start()
    from fleet import start_fleet
    start_fleet(c)
    try:server.serve_forever()
    finally:
        c.stop_event.set();c.disable()
        if c.proc and c.proc.poll() is None:c.command('QUIT')
        server.server_close()
if __name__=='__main__':main()
````
<!-- END FILE -->

### fleet.py

<!-- FILE: fleet.py SHA256: 57a5691f2e9fe71c583e27e252beb98861bba4dc40f89248e3f11c871dc52731 -->
````python
"""Dynamic SSH CPU/CUDA fleet. Only public jobs leave the local signer."""
import json,time,threading,secrets
from pathlib import Path
from setup_credentials import unprotect
from probe_servers import connect

def build_specs(rows,blocked,ready):
 specs={}
 for row in rows:
  if row['port'] in blocked:continue
  endpoint=row['host']+':'+str(row['port'])
  specs[endpoint+' / CPU']=(row,'CPU',None)
  for device in ready.get(endpoint,{}).get('devices',[]):
   if type(device) is int and 0<=device<64:
    specs[endpoint+' / GPU '+str(device)]=(row,'GPU',device)
 return specs

def start_fleet(control):
 root=Path(__file__).resolve().parent
 running={}
 def worker(wid,row,kind,device,cancel):
  retry=0
  stopped=lambda:cancel.is_set() or control.stop_event.is_set()
  while not stopped():
   client=None
   with control.lock:control.remote[wid]=dict(status='connecting',kind=kind,device=device,rate=0,threads=0,updated=time.time())
   try:
    client=connect(row);client.get_transport().set_keepalive(10)
    threads=0
    if kind=='CPU':
     _,out,_=client.exec_command('nproc',timeout=10);threads=int(out.read())
     if not 1<=threads<=512:raise ValueError('Invalid remote CPU count')
     command='/root/prspct-cpu/cpu-miner '+str(threads)
    else:command='/root/prspct-cpu/gpu-miner '+str(device)
    stdin,stdout,stderr=client.exec_command(command,timeout=12)
    channel=stdout.channel;channel.settimeout(2)
    prefix,last,buffer=secrets.token_hex(24),None,''
    retry=0
    with control.lock:control.remote[wid].update(status='starting',threads=threads)
    control.log('Connected: '+wid)
    while not stopped():
     with control.lock:
      st=control.state
      active=bool(control.mining and st and time.time()-st['read_at']<10 and st['depth']<8888)
      command=('JOB '+st['seed']+' '+control.address_hex+' '+st['work_target'].to_bytes(32,'big').hex()+' '+prefix) if active else 'STOP'
     if command!=last:stdin.write(command+'\n');stdin.flush();last=command
     if channel.recv_stderr_ready():channel.recv_stderr(16384)
     if channel.recv_ready():
      buffer+=channel.recv(65536).decode('ascii',errors='replace')
      if len(buffer)>262144:raise ValueError('Oversized worker output')
      while '\n' in buffer:
       line,buffer=buffer.split('\n',1);fields=line.split()
       if len(fields)==3 and fields[0]=='RATE':
        rate=float(fields[1])
        if not 0<=rate<=1e15:raise ValueError('Invalid worker rate')
        with control.lock:control.remote[wid].update(status='mining' if active else 'waiting',rate=rate if active else 0,total=int(fields[2]),updated=time.time())
       elif len(fields)==3 and fields[0]=='FOUND':
        if any(len(x)!=64 for x in fields[1:]):raise ValueError('Malformed candidate')
        int(fields[1],16);int(fields[2],16)
        with control.lock:control.found+=1
        control.log('Solution received from '+wid)
        try:control.solutions.put_nowait((fields[1],fields[2]))
        except Exception:control.log('Candidate queue full')
     elif channel.exit_status_ready() or channel.closed:raise ConnectionError('SSH worker exited')
     cancel.wait(0.15)
    try:stdin.write('QUIT\n');stdin.flush()
    except Exception:pass
   except Exception as exc:
    if not stopped():
     with control.lock:control.remote[wid].update(status='reconnecting',rate=0,updated=time.time())
     control.log('Remote '+wid+': '+type(exc).__name__+'; reconnecting')
   finally:
    if client:client.close()
   retry+=1
   if not stopped():cancel.wait(min(20,2**min(retry,4)))
  with control.lock:control.remote.pop(wid,None)
 def supervisor():
  while not control.stop_event.is_set():
   try:
    file=root/'ssh-secrets.dpapi';rows=json.loads(unprotect(file.read_bytes())) if file.exists() else []
    file=root/'disabled-ports.json';blocked=json.loads(file.read_text()) if file.exists() else []
    file=root/'gpu-ready.json';ready=json.loads(file.read_text()) if file.exists() else {}
    specs=build_specs(rows,blocked,ready)
    for wid,(thread,cancel,old_spec) in list(running.items()):
     if specs.get(wid)!=old_spec:cancel.set()
     if not thread.is_alive():running.pop(wid)
    for wid,spec in specs.items():
     if wid not in running:
      cancel=threading.Event()
      thread=threading.Thread(target=worker,args=(wid,*spec,cancel),daemon=True)
      running[wid]=(thread,cancel,spec);thread.start()
   except Exception as exc:control.log('Fleet configuration retry: '+type(exc).__name__)
   control.stop_event.wait(3)
  for thread,cancel,spec in running.values():cancel.set()
 thread=threading.Thread(target=supervisor,daemon=True);thread.start()
 return thread
````
<!-- END FILE -->

### probe_servers.py

<!-- FILE: probe_servers.py SHA256: 4e8ea1413ac17d4e0ab2f691d8fbba26fed16003da6c0dec5062ae91a7006a5a -->
````python
import controller as m,json,concurrent.futures
import paramiko
from setup_credentials import unprotect
def connect_once(row,sock=None):
 c=paramiko.SSHClient();known=m.ROOT/'ssh-known-hosts'
 if known.exists():c.load_host_keys(str(known))
 c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
 try:c.connect(row['host'],port=row['port'],username='root',password=row['password'],timeout=10,banner_timeout=10,auth_timeout=10,allow_agent=False,look_for_keys=False,sock=sock)
 except Exception:c.close();raise
 return c
def connect(row):
 try:return connect_once(row)
 except Exception as initial:
  rows=json.loads(unprotect((m.ROOT/'ssh-secrets.dpapi').read_bytes()))
  for port in m.CONFIG.get('relay_ports',[]):
   if row['port']==port:continue
   relay=None
   try:
    relay=connect_once(next(x for x in rows if x['port']==port))
    channel=relay.get_transport().open_channel('direct-tcpip',(row['host'],row['port']),('127.0.0.1',0),timeout=8)
    c=connect_once(row,channel);close=c.close
    def close_all():
     close();relay.close()
    c.close=close_all
    return c
   except Exception:
    if relay:relay.close()
  raise initial
def probe(row):
 try:
  c=connect(row);_,out,err=c.exec_command("uname -s; nproc; lscpu | head -18; command -v gcc; command -v g++; command -v python3",timeout=15)
  text=out.read().decode();c.close();return {'port':row['port'],'status':'OK','hardware':text}
 except Exception as e:return {'port':row['port'],'status':type(e).__name__,'error':str(e)[:180]}
if __name__=='__main__':
 rows=json.loads(unprotect((m.ROOT/'ssh-secrets.dpapi').read_bytes()))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for r in pool.map(probe,rows):print(json.dumps(r),flush=True)
````
<!-- END FILE -->

### setup_credentials.py

<!-- FILE: setup_credentials.py SHA256: 9cd1a20a4e8319053b31efcfe93b01c1dd2cd5f3ad6c0bb44876e63df244c1f7 -->
````python
"""Interactive local SSH credential storage protected by Windows DPAPI."""
import ctypes,json,sys,getpass
from ctypes import wintypes
from pathlib import Path
class Blob(ctypes.Structure):_fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_char))]
def protect(raw):
 buf=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_char)));target=Blob()
 if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source),None,None,None,None,0,ctypes.byref(target)):raise ctypes.WinError()
 try:return ctypes.string_at(target.pbData,target.cbData)
 finally:ctypes.windll.kernel32.LocalFree(target.pbData)
def unprotect(raw):
 buf=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_char)));target=Blob()
 if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source),None,None,None,None,0,ctypes.byref(target)):raise ctypes.WinError()
 try:return ctypes.string_at(target.pbData,target.cbData)
 finally:ctypes.windll.kernel32.LocalFree(target.pbData)
````
<!-- END FILE -->

### ops.py

<!-- FILE: ops.py SHA256: 50c933b831b15a2badec60cdc44daff601785433334f6d5806ca4cdf5e185d34 -->
````python
"""Public-wallet setup and local PRSPCT operations. No wallet private keys in CLI.

Examples:
  python ops.py init
  python ops.py add-server
  python ops.py preflight
  python ops.py status
  python ops.py open-panel
  python ops.py local-off
  python ops.py remove-server worker.example.com 1234

Run controller.py separately before using panel/API commands. Enter a wallet
private key only in the local panel; this CLI cannot unlock or sign anything.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings
import webbrowser

ROOT = Path(__file__).resolve().parent
EXPECTED_CHAIN = 4663
EXPECTED_CONTRACT = "0xd078008c3d887a52ce722a3ca0539ca1f4971dd1"
SELECTORS = {
    "state()": ("0xc19d93fb", 0),
    "targetOf(uint256,uint256)": ("0x9319d7d3", 2),
    "claim(uint256)": ("0x379607f5", 1),
}


class OpsError(Exception):
    """An error message that is safe to display without secrets."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo arbitrary arguments: a user might accidentally paste a key.
        self.print_usage(sys.stderr)
        self.exit(2, "Error: invalid command or arguments. Use --help. Passwords belong only in the hidden prompt.\n")


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise OpsError("A local configuration file could not be read. Repair it before continuing.") from None


def atomic_bytes(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".ops-", suffix=".tmp", dir=str(ROOT))
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, content):
    atomic_bytes(path, (json.dumps(content, indent=2) + "\n").encode("utf-8"))


@contextlib.contextmanager
def file_lock(path):
    # gpu-ready.lock uses the same first-byte lock as deploy_gpu.py.
    with path.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def public_address(value):
    value = value.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or int(value[2:], 16) == 0:
        raise OpsError("Enter a nonzero public EVM wallet address: 0x followed by 40 hexadecimal characters.")
    return value


def host_name(value):
    value = value.strip().lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if len(value) > 253 or not value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise OpsError("Enter a hostname or IP address, without ssh, a URL, a username, or a port.")
    return value


def port_number(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise OpsError("The SSH port must be an integer from 1 to 65535.") from None
    if not 1 <= result <= 65535:
        raise OpsError("The SSH port must be an integer from 1 to 65535.")
    return result


def dpapi_helpers():
    if os.name != "nt":
        raise OpsError("SSH credential storage uses Windows DPAPI. Run this command on the Windows controller account.")
    from setup_credentials import protect, unprotect
    return protect, unprotect


def credential_rows(unprotect):
    path = ROOT / "ssh-secrets.dpapi"
    if not path.exists():
        return []
    try:
        rows = json.loads(unprotect(path.read_bytes()))
    except Exception:
        raise OpsError("Cannot decrypt SSH credentials with this Windows account. The existing file was not changed.") from None
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("host"), str)
        or type(row.get("port")) is not int or not isinstance(row.get("password"), str)
        for row in rows
    ):
        raise OpsError("The encrypted SSH credential schema is invalid. The existing file was not changed.")
    return rows


def blocked_ports():
    ports = read_json(ROOT / "disabled-ports.json", [])
    if not isinstance(ports, list) or any(type(port) is not int for port in ports):
        raise OpsError("disabled-ports.json must contain a list of integer ports.")
    return ports


def init_wallet():
    # Deliberately stdlib-only: controller imports may require this configuration.
    configuration = read_json(ROOT / "deployment.json", {})
    if not isinstance(configuration, dict):
        raise OpsError("deployment.json must contain a JSON object.")
    previous = configuration.get("address", "")
    prompt = "Public EVM wallet address (never a private key)"
    value = input(prompt + (" [Enter keeps the current address]: " if previous else ": ")).strip()
    address = public_address(value or previous)
    with file_lock(ROOT / "ops-config.lock"):
        current = read_json(ROOT / "deployment.json", {})
        if not isinstance(current, dict):
            raise OpsError("deployment.json must contain a JSON object.")
        if current.get("address") and current["address"].lower() != address.lower():
            raise OpsError("This directory is initialized for another wallet. Use a separate deployment directory to keep its ledger and signer state separate.")
        if (ROOT / "spend-ledger.json").exists() and not current.get("address"):
            raise OpsError("An existing ledger has no matching wallet configuration. Restore its original deployment.json before continuing.")
        current["address"] = address
        current.setdefault("relay_ports", [])
        write_json(ROOT / "deployment.json", current)
    print("Public wallet configured: " + address)


def add_server():
    # getpass must never fall back to an echoing or redirected input stream.
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise OpsError("add-server requires an interactive terminal with hidden password input. Piped input is not accepted.")
    protect, unprotect = dpapi_helpers()
    host = host_name(input("SSH host (login is root): "))
    port = port_number(input("SSH port: "))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("SSH password (hidden): ")
    except getpass.GetPassWarning:
        raise OpsError("Hidden password input is unavailable. Use an interactive Windows terminal; no password was requested through an echoing fallback.") from None
    if not password:
        raise OpsError("An empty SSH password was not saved.")
    with file_lock(ROOT / "ops-config.lock"):
        rows = credential_rows(unprotect)
        if any(row["port"] == port and row["host"].lower() != host for row in rows):
            raise OpsError("Another host already uses this port. Fleet exclusions and relay selection use port numbers, so choose endpoints with distinct SSH ports.")
        matching = [i for i, row in enumerate(rows) if row["host"].lower() == host and row["port"] == port]
        if len(matching) > 1:
            raise OpsError("Duplicate credentials exist for this endpoint. Resolve the duplicate before updating it.")
        if matching:
            rows[matching[0]] = {**rows[matching[0]], "password": password}
        else:
            rows.append({"host": host, "port": port, "password": password})
        encrypted = protect(json.dumps(rows).encode("utf-8"))
        disabled = blocked_ports()
        atomic_bytes(ROOT / "ssh-secrets.dpapi", encrypted)
        write_json(ROOT / "disabled-ports.json", [value for value in disabled if value != port])
    print("Saved SSH credentials with Windows DPAPI for " + host + ":" + str(port))
    print("Install and validate the remote workers using the deployment steps. This command does not purchase or provision a rental.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def panel_location():
    launch = read_json(ROOT / "launch.json", {})
    if not isinstance(launch, dict) or not isinstance(launch.get("url"), str):
        raise OpsError("No local panel is registered. Start controller.py first.")
    try:
        parsed = urllib.parse.urlsplit(launch["url"])
        valid = (
            parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and parsed.path in ("", "/") and not parsed.query
            and parsed.username is None and parsed.password is None
            and re.fullmatch(r"[A-Za-z0-9_-]{16,256}", parsed.fragment)
        )
    except ValueError:
        valid = False
    if not valid:
        raise OpsError("launch.json does not contain a valid loopback-only panel URL.")
    return "http://127.0.0.1:" + str(parsed.port), parsed.fragment, launch["url"]


def api(path, payload=None):
    base, token, _ = panel_location()
    encoded = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=encoded, headers={
        "X-Panel-Token": token, "Content-Type": "application/json",
    }, method="GET" if payload is None else "POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise OpsError("The local API returned HTTP " + str(error.code) + ". Check the local panel; authentication tokens are not printed.") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise OpsError("Cannot read the local controller API. Start controller.py or check its terminal.") from None
    if not isinstance(result, dict):
        raise OpsError("The local controller returned an unexpected response.")
    return result


def show_status():
    state = api("/api/status")
    fields = ("address", "contract", "chain", "mining", "auto", "key_loaded", "threads",
              "local_cpu_enabled", "local_mining", "rate", "local_rate", "found", "depth",
              "state_age", "budget_eth", "per_tx_eth", "spent_eth", "reserved_eth", "pending")
    public = {field: state[field] for field in fields if field in state}
    worker_fields = ("status", "kind", "device", "threads", "rate", "total", "updated")
    workers = state.get("workers", {})
    if isinstance(workers, dict):
        public["workers"] = {
            name: {field: worker[field] for field in worker_fields if field in worker}
            for name, worker in workers.items() if isinstance(worker, dict)
        }
    # Intentionally omit events, transaction payloads, launch URL and panel token.
    print(json.dumps(public, indent=2))


def open_panel():
    _, _, url = panel_location()
    if not webbrowser.open(url, new=2):
        raise OpsError("The default browser did not open. Use the local panel shortcut.")
    print("Opened the local panel. Its authentication URL was not printed.")


def control_action(name):
    actions = {
        "lock": ("/api/lock", {}, "Signer locked; automatic claiming is disabled."),
        "stop": ("/api/stop", {}, "Global mining stop requested."),
        "start": ("/api/start", {}, "Global mining start requested. Unlock signing only through the local panel."),
        "local-off": ("/api/local-cpu", {"enabled": False}, "Local CPU mining disabled; remote workers are unchanged."),
        "local-on": ("/api/local-cpu", {"enabled": True}, "Local CPU mining enabled, subject to the global mining state."),
    }
    path, payload, message = actions[name]
    if api(path, payload).get("ok") is not True:
        raise OpsError("The local controller did not confirm the action. Check its panel.")
    print(message)


def remove_server(host, port):
    host, port = host_name(host), port_number(port)
    protect, unprotect = dpapi_helpers()
    endpoint = host + ":" + str(port)
    with file_lock(ROOT / "ops-config.lock"):
        rows = credential_rows(unprotect)
        if any(row["port"] == port and row["host"].lower() != host for row in rows):
            raise OpsError("Another host uses this port. The global port exclusion would stop that host too; resolve the port collision before removal.")
        retained = [row for row in rows if not (row["host"].lower() == host and row["port"] == port)]
        disabled = blocked_ports()
        ready_path = ROOT / "gpu-ready.json"
        with file_lock(ready_path.with_suffix(".lock")):
            ready = read_json(ready_path, {})
            if not isinstance(ready, dict):
                raise OpsError("gpu-ready.json must contain a JSON object.")
            encrypted = protect(json.dumps(retained).encode("utf-8"))
            # Stop reconnect attempts first. The running fleet supervisor sends
            # QUIT and closes only this endpoint's CPU/GPU SSH workers.
            write_json(ROOT / "disabled-ports.json", sorted(set(disabled + [port])))
            atomic_bytes(ROOT / "ssh-secrets.dpapi", encrypted)
            ready.pop(endpoint, None)
            write_json(ready_path, ready)
    print("Removed mining configuration for " + endpoint + ". The running fleet supervisor will stop its workers.")
    print("Rental billing is separate: cancel the paid order on your provider's website.")


def preflight():
    configuration = read_json(ROOT / "deployment.json", {})
    if not isinstance(configuration, dict):
        raise OpsError("Run init before preflight.")
    wallet = public_address(configuration.get("address", ""))
    module = importlib.import_module("controller")
    if str(module.ADDRESS).lower() != wallet.lower():
        raise OpsError("controller.ADDRESS does not match deployment.json. Use the matching portable controller source.")
    if str(module.CONTRACT).lower() != EXPECTED_CONTRACT or int(module.CHAIN) != EXPECTED_CHAIN:
        raise OpsError("The controller chain or contract differs from this PRSPCT deployment.")
    for signature, (expected, arguments) in SELECTORS.items():
        encoded = module.data(signature, *([0] * arguments))
        if encoded[:10] != expected or len(encoded) != 10 + 64 * arguments:
            raise OpsError("An ABI selector or argument encoding check failed.")
    chain = int(module.rpc("eth_chainId", []), 16)
    if chain != EXPECTED_CHAIN:
        raise OpsError("RPC chain ID is not Robinhood Chain 4663.")
    # Pin all contract reads to one block; no mining, gas estimation or signing.
    block = module.rpc("eth_blockNumber", [])
    code = module.rpc("eth_getCode", [EXPECTED_CONTRACT, block])
    if not isinstance(code, str) or not code.startswith("0x") or not bytes.fromhex(code[2:]):
        raise OpsError("The expected contract has no deployed bytecode.")
    raw = module.rpc("eth_call", [{"to": EXPECTED_CONTRACT, "data": module.data("state()")}, block])
    decoded = bytes.fromhex(raw[2:])
    if len(decoded) != 14 * 32:
        raise OpsError("state() did not return the expected 14 ABI words.")
    words = [int.from_bytes(decoded[offset:offset + 32], "big") for offset in range(0, len(decoded), 32)]
    depth, seed, target = words[0], words[1], words[4]
    if not 0 <= depth < 8888 or seed == 0 or target == 0:
        raise OpsError("The mine is not open for a free proof-of-work claim, or all certificates have been issued.")
    target_raw = module.rpc("eth_call", [{"to": EXPECTED_CONTRACT, "data": module.data("targetOf(uint256,uint256)", depth, 0)}, block])
    target_bytes = bytes.fromhex(target_raw[2:])
    if len(target_bytes) != 32 or int.from_bytes(target_bytes, "big") != target:
        raise OpsError("targetOf(depth, 0) does not match the free-mining target from state().")
    print(json.dumps({"preflight": "passed", "chain": chain, "contract": EXPECTED_CONTRACT,
                      "public_wallet": wallet, "block": int(block, 16), "state_words": 14,
                      "depth": depth, "free_mining_target": "0x" + target.to_bytes(32, "big").hex(),
                      "selectors": {name: entry[0] for name, entry in SELECTORS.items()},
                      "transactions_sent": 0, "benchmark_started": False}, indent=2))


def main():
    parser = SafeParser(description="PRSPCT public-wallet setup and authenticated local operations. Wallet private keys are accepted only by the local panel.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Configure the public wallet without importing the controller."),
        ("add-server", "Prompt for SSH host/port and a hidden password; save with Windows DPAPI."),
        ("status", "Show public controller and worker status."),
        ("open-panel", "Open the local panel without printing its authentication URL."),
        ("lock", "Clear the in-memory signer through the local API."),
        ("stop", "Stop global mining through the local API."),
        ("start", "Start global mining through the local API."),
        ("local-off", "Disable only the controller computer's CPU miner."),
        ("local-on", "Enable the controller computer's CPU miner."),
        ("preflight", "Read-only chain, contract and ABI checks; no transactions or benchmark."),
    ):
        commands.add_parser(name, help=help_text)
    remove = commands.add_parser("remove-server", help="Remove one endpoint from mining; rental cancellation is separate.")
    remove.add_argument("host")
    remove.add_argument("port")
    args = parser.parse_args()
    try:
        if args.command == "init": init_wallet()
        elif args.command == "add-server": add_server()
        elif args.command == "status": show_status()
        elif args.command == "open-panel": open_panel()
        elif args.command == "preflight": preflight()
        elif args.command == "remove-server": remove_server(args.host, args.port)
        else: control_action(args.command)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 130
    except OpsError as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    except Exception as error:
        # Never dump RPC headers, CLI input, DPAPI payloads or arbitrary errors.
        print("Operation failed (" + type(error).__name__ + "). Check the local controller or setup logs.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
````
<!-- END FILE -->

### panel.html

<!-- FILE: panel.html SHA256: 4d862928b2413440c4af3de4e47098afaa21a9d5f9fa8c6803a1db36171fc87c -->
````html
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PRSPCT CPU and GPU Miner</title>
<style>
body{margin:0;background:#101319;color:#eef2f7;font:16px system-ui}main{max-width:1000px;margin:auto;padding:32px 20px}h1{margin-bottom:8px}p{line-height:1.5;color:#b5bfce}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}section,.card{background:#1a202a;padding:20px;border:1px solid #303a48;border-radius:12px;margin-top:16px}.label{font-size:13px;color:#b5bfce}.value{font-size:25px;margin-top:8px}button{padding:12px 16px;margin:4px;border:0;border-radius:7px;background:#d5f76d;color:#101319;font-weight:650;cursor:pointer}.secondary{background:#354152;color:white}input{box-sizing:border-box;width:100%;padding:13px;background:#101319;color:white;border:1px solid #58677a;border-radius:6px;margin:12px 0}#events{white-space:pre-wrap;font:13px ui-monospace,monospace;max-height:340px;overflow:auto;line-height:1.7}#wallet,#events,#workers,#transactions{overflow-wrap:anywhere}#message,a{color:#d5f76d}.hint{font-size:14px}
</style>
<main>
<h1>PRSPCT CPU and GPU Miner</h1>
<p>Robinhood Chain · ALL SWEAT · NFT mint value: 0 ETH</p>
<p id="wallet"></p>
<div class="cards">
<div class="card"><div class="label">Total hash rate</div><div class="value" id="rate">Waiting</div></div>
<div class="card"><div class="label">Mining / automatic claims</div><div class="value" id="mode">Waiting</div></div>
<div class="card"><div class="label">Gas spent / budget (ETH)</div><div class="value" id="spend">Waiting</div></div>
<div class="card"><div class="label">Collection depth</div><div class="value" id="depth">Waiting</div></div>
</div>
<p id="details"></p>
<button id="start">Start mining</button>
<button id="stop" class="secondary">Stop mining and claims</button>
<button id="local-cpu-toggle" class="secondary" hidden>Disable local CPU</button>
<section>
<h2>Local signer</h2>
<p>Total gas budget: <b>0.03 ETH</b>, including reverted transactions. Maximum reservation per wallet nonce: <b>0.01 ETH</b>. The NFT payment is always zero.</p>
<p class="hint">Enter the private key only in this local form. It is sent to the controller on 127.0.0.1 and kept in process memory. The form is cleared immediately. Every controller restart requires manual key entry. The key must match the displayed wallet.</p>
<input id="key" type="password" autocomplete="new-password" spellcheck="false" placeholder="Private key: enter only here" aria-label="Private key">
<button id="unlock">Enable automatic claims</button>
<button id="lock" class="secondary">Lock signer</button>
<p id="message" role="status"></p>
</section>
<section><h2>Workers</h2><div id="workers"></div><p class="hint">Workers receive public jobs and the wallet address. Signing stays on this computer. SSH workers reconnect after connection loss.</p></section>
<section><h2>Transactions</h2><div id="transactions"></div></section>
<section><h2>Events</h2><div id="events"></div></section>
</main>
<script>
const token=location.hash.slice(1),$=id=>document.getElementById(id);
let localCpuEnabled=false;
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Panel-Token':token},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok)throw Error(j.error);return j;}
async function action(name,body={}){try{await api(name,body);$('message').textContent='Done';await refresh();}catch(e){$('message').textContent=e.message;}}
$('unlock').onclick=()=>{const key=$('key').value.trim();$('key').value='';action('unlock',{key});};
for(const name of ['start','stop','lock'])$(name).onclick=()=>action(name);
$('local-cpu-toggle').onclick=()=>action('local-cpu',{enabled:!localCpuEnabled});
function rate(n){return n>=1e9?(n/1e9).toFixed(2)+' GH/s':(n/1e6).toFixed(2)+' MH/s';}
async function refresh(){try{
const s=await api('status');$('wallet').textContent='Wallet: '+s.address+' · Chain: '+s.chain;
localCpuEnabled=s.local_cpu_enabled!==false;$('local-cpu-toggle').hidden=!('local_cpu_enabled' in s);$('local-cpu-toggle').textContent=localCpuEnabled?'Disable local CPU':'Enable local CPU';
$('rate').textContent=rate(s.rate);$('mode').textContent=(s.mining?'ON':'OFF')+' / '+(s.auto?'ON':'OFF');$('spend').textContent=Number(s.spent_eth).toFixed(6)+' / '+s.budget_eth;$('depth').textContent=(s.depth??'?')+' / 8888';
$('details').textContent='Found candidates: '+s.found+' · Pending transactions: '+s.pending+' · Reserved gas: '+s.reserved_eth+' ETH · Chain data age: '+s.state_age+' seconds.'+(s.expected_seconds?' Approximate mean at the current contract target: '+(s.expected_seconds/3600).toFixed(2)+' hours. Mining uses a stricter target; changing difficulty affects this estimate.':'');
$('events').textContent=s.events.slice().reverse().map(e=>e.time+' '+e.message).join('\n');
$('workers').replaceChildren();const local=document.createElement('p');local.textContent='Local CPU: '+(localCpuEnabled?(s.local_mining?'ON':'WAITING'):'OFF')+' · '+s.threads+' threads · '+rate(s.local_rate??0);$('workers').append(local);
for(const [id,w] of Object.entries(s.workers||{})){const p=document.createElement('p');p.textContent=id+' · '+(w.kind==='GPU'?'':w.threads+' threads · ')+rate(w.rate)+' · '+w.status;$('workers').append(p);}
$('transactions').replaceChildren();for(const t of s.transactions.slice().reverse()){const p=document.createElement('p'),a=document.createElement('a');a.href='https://robinhoodchain.blockscout.com/tx/'+(t.mined_hash||t.hash);a.target='_blank';a.rel='noopener noreferrer';a.textContent=(t.mined_hash||t.hash).slice(0,16)+'...';p.append(a,document.createTextNode(' · '+t.status+' · NFT '+(t.nft_ids||[]).join(', ')));$('transactions').append(p);}
}catch(e){$('message').textContent=e.message;}}
refresh();setInterval(refresh,2000);
</script>
</html>
````
<!-- END FILE -->

### CpuMiner.cs

<!-- FILE: CpuMiner.cs SHA256: 0c4044dcdea2598f250db9997579134901e2e5ae737a7bb56334b6d6f3e94fd3 -->
````csharp
using System;
using System.Threading;
using System.Diagnostics;
using System.Globalization;

// Keccak-256, Ethereum padding (0x01), one 84-byte message per nonce.
// This process receives public work only. It never receives a signing key.
class CpuMiner {
 static readonly ulong[] RC={0x0000000000000001UL,0x0000000000008082UL,0x800000000000808aUL,0x8000000080008000UL,0x000000000000808bUL,0x0000000080000001UL,0x8000000080008081UL,0x8000000000008009UL,0x000000000000008aUL,0x0000000000000088UL,0x0000000080008009UL,0x000000008000000aUL,0x000000008000808bUL,0x800000000000008bUL,0x8000000000008089UL,0x8000000000008003UL,0x8000000000008002UL,0x8000000000000080UL,0x000000000000800aUL,0x800000008000000aUL,0x8000000080008081UL,0x8000000000008080UL,0x0000000080000001UL,0x8000000080008008UL};
 static readonly int[] Rot={1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44};
 static readonly int[] Pi={10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1};
 static volatile Job Current; static volatile bool Quit; static long Total; static object Output=new object();
 class Job { public ulong[] Template,Target; public string Prefix; }
 static ulong Rol(ulong x,int n){return (x<<n)|(x>>(64-n));}
 static ulong Swap(ulong x){x=((x&0x00ff00ff00ff00ffUL)<<8)|((x>>8)&0x00ff00ff00ff00ffUL);x=((x&0x0000ffff0000ffffUL)<<16)|((x>>16)&0x0000ffff0000ffffUL);return(x<<32)|(x>>32);}
 static unsafe void Permute(ulong* a){
  for(int r=0;r<24;r++){
   ulong c0=a[0]^a[5]^a[10]^a[15]^a[20];
   ulong c1=a[1]^a[6]^a[11]^a[16]^a[21];
   ulong c2=a[2]^a[7]^a[12]^a[17]^a[22];
   ulong c3=a[3]^a[8]^a[13]^a[18]^a[23];
   ulong c4=a[4]^a[9]^a[14]^a[19]^a[24];
   ulong d0=c4^Rol(c1,1);
   a[0]^=d0;
   a[5]^=d0;
   a[10]^=d0;
   a[15]^=d0;
   a[20]^=d0;
   ulong d1=c0^Rol(c2,1);
   a[1]^=d1;
   a[6]^=d1;
   a[11]^=d1;
   a[16]^=d1;
   a[21]^=d1;
   ulong d2=c1^Rol(c3,1);
   a[2]^=d2;
   a[7]^=d2;
   a[12]^=d2;
   a[17]^=d2;
   a[22]^=d2;
   ulong d3=c2^Rol(c4,1);
   a[3]^=d3;
   a[8]^=d3;
   a[13]^=d3;
   a[18]^=d3;
   a[23]^=d3;
   ulong d4=c3^Rol(c0,1);
   a[4]^=d4;
   a[9]^=d4;
   a[14]^=d4;
   a[19]^=d4;
   a[24]^=d4;
   ulong t=a[1],u;
   u=a[10];a[10]=Rol(t,1);t=u;
   u=a[7];a[7]=Rol(t,3);t=u;
   u=a[11];a[11]=Rol(t,6);t=u;
   u=a[17];a[17]=Rol(t,10);t=u;
   u=a[18];a[18]=Rol(t,15);t=u;
   u=a[3];a[3]=Rol(t,21);t=u;
   u=a[5];a[5]=Rol(t,28);t=u;
   u=a[16];a[16]=Rol(t,36);t=u;
   u=a[8];a[8]=Rol(t,45);t=u;
   u=a[21];a[21]=Rol(t,55);t=u;
   u=a[24];a[24]=Rol(t,2);t=u;
   u=a[4];a[4]=Rol(t,14);t=u;
   u=a[15];a[15]=Rol(t,27);t=u;
   u=a[23];a[23]=Rol(t,41);t=u;
   u=a[19];a[19]=Rol(t,56);t=u;
   u=a[13];a[13]=Rol(t,8);t=u;
   u=a[12];a[12]=Rol(t,25);t=u;
   u=a[2];a[2]=Rol(t,43);t=u;
   u=a[20];a[20]=Rol(t,62);t=u;
   u=a[14];a[14]=Rol(t,18);t=u;
   u=a[22];a[22]=Rol(t,39);t=u;
   u=a[9];a[9]=Rol(t,61);t=u;
   u=a[6];a[6]=Rol(t,20);t=u;
   u=a[1];a[1]=Rol(t,44);t=u;
   c0=a[0];
   c1=a[1];
   c2=a[2];
   c3=a[3];
   c4=a[4];
   a[0]=c0^((~c1)&c2);
   a[1]=c1^((~c2)&c3);
   a[2]=c2^((~c3)&c4);
   a[3]=c3^((~c4)&c0);
   a[4]=c4^((~c0)&c1);
   c0=a[5];
   c1=a[6];
   c2=a[7];
   c3=a[8];
   c4=a[9];
   a[5]=c0^((~c1)&c2);
   a[6]=c1^((~c2)&c3);
   a[7]=c2^((~c3)&c4);
   a[8]=c3^((~c4)&c0);
   a[9]=c4^((~c0)&c1);
   c0=a[10];
   c1=a[11];
   c2=a[12];
   c3=a[13];
   c4=a[14];
   a[10]=c0^((~c1)&c2);
   a[11]=c1^((~c2)&c3);
   a[12]=c2^((~c3)&c4);
   a[13]=c3^((~c4)&c0);
   a[14]=c4^((~c0)&c1);
   c0=a[15];
   c1=a[16];
   c2=a[17];
   c3=a[18];
   c4=a[19];
   a[15]=c0^((~c1)&c2);
   a[16]=c1^((~c2)&c3);
   a[17]=c2^((~c3)&c4);
   a[18]=c3^((~c4)&c0);
   a[19]=c4^((~c0)&c1);
   c0=a[20];
   c1=a[21];
   c2=a[22];
   c3=a[23];
   c4=a[24];
   a[20]=c0^((~c1)&c2);
   a[21]=c1^((~c2)&c3);
   a[22]=c2^((~c3)&c4);
   a[23]=c3^((~c4)&c0);
   a[24]=c4^((~c0)&c1);
   a[0]^=RC[r];
  }
 }
 static byte[] Unhex(string s){if(s.StartsWith("0x"))s=s.Substring(2);if(s.Length%2!=0)throw new Exception("hex length");byte[] b=new byte[s.Length/2];for(int i=0;i<b.Length;i++)b[i]=byte.Parse(s.Substring(i*2,2),NumberStyles.HexNumber);return b;}
 static string Hex(byte[] b){return BitConverter.ToString(b).Replace("-","").ToLowerInvariant();}
 static ulong[] Template(string seed,string address,string prefix){
  byte[] s=Unhex(seed),ad=Unhex(address),p=Unhex(prefix),m=new byte[136];
  if(s.Length!=32||ad.Length!=20||p.Length!=24)throw new Exception("work length");
  Array.Copy(s,0,m,0,32);Array.Copy(ad,0,m,32,20);Array.Copy(p,0,m,52,24);m[84]=1;m[135]=128;
  ulong[] a=new ulong[25];for(int i=0;i<17;i++)a[i]=BitConverter.ToUInt64(m,8*i);return a;
 }
 static unsafe void Hash(ulong[] template,ulong n,ulong[] a,ulong[] c){fixed(ulong* ap=a,tp=template){for(int i=0;i<25;i++)ap[i]=tp[i];ulong b=Swap(n);ap[9]=(ap[9]&0xffffffffUL)|(b<<32);ap[10]=(ap[10]&0xffffffff00000000UL)|(b>>32);Permute(ap);}}
 static bool Under(ulong[] a,ulong[] target){for(int i=0;i<4;i++){ulong w=Swap(a[i]);if(w<target[i])return true;if(w>target[i])return false;}return false;}
 static string Digest(ulong[] a){byte[] b=new byte[32];for(int i=0;i<4;i++)Array.Copy(BitConverter.GetBytes(a[i]),0,b,i*8,8);return Hex(b);}
 static void Emit(string s){lock(Output){Console.WriteLine(s);Console.Out.Flush();}}
 static void Work(int id,int threads){
  ulong n=(ulong)id;ulong[] a=new ulong[25],c=new ulong[5];
  while(!Quit){Job j=Current;if(j==null){Thread.Sleep(30);continue;}
   for(int k=0;k<4096;k++){
    Hash(j.Template,n,a,c);
    if(Under(a,j.Target))Emit("FOUND "+j.Prefix+n.ToString("x16")+" "+Digest(a));
    n+=(ulong)threads;
   }
   Interlocked.Add(ref Total,4096);
  }
 }
 public static int Main(string[] args){
  try{
   if(args.Length==4 && args[0]=="hash"){
    byte[] n=Unhex(args[3]);if(n.Length!=32)throw new Exception("nonce length");
    var a=new ulong[25];Hash(Template(args[1],args[2],args[3].Replace("0x","").Substring(0,48)),Swap(BitConverter.ToUInt64(n,24)),a,new ulong[5]);Console.WriteLine(Digest(a));return 0;
   }
   int threads=args.Length>0?int.Parse(args[0]):Math.Max(1,Environment.ProcessorCount-1);
   if(threads<1||threads>Environment.ProcessorCount)throw new Exception("thread count");
   for(int i=0;i<threads;i++){int id=i;new Thread(()=>Work(id,threads)){IsBackground=true}.Start();}
   var watch=Stopwatch.StartNew();new Thread(()=>{long last=0;double t=0;while(!Quit){Thread.Sleep(1000);long count=Interlocked.Read(ref Total);double now=watch.Elapsed.TotalSeconds;Emit("RATE "+((count-last)/(now-t)).ToString("F0",CultureInfo.InvariantCulture)+" "+count);last=count;t=now;}}){IsBackground=true}.Start();
   string line;while((line=Console.ReadLine())!=null){
    if(line=="STOP"){Current=null;continue;}if(line=="QUIT")break;
    string[] f=line.Split(' ');if(f.Length!=5||f[0]!="JOB")throw new Exception("work protocol");
    byte[] target=Unhex(f[3]);if(target.Length!=32)throw new Exception("target length");
    ulong[] tw=new ulong[4];for(int i=0;i<4;i++)tw[i]=Swap(BitConverter.ToUInt64(target,8*i));
    Current=new Job{Template=Template(f[1],f[2],f[4]),Target=tw,Prefix=f[4]};
   }
   Quit=true;return 0;
  }catch(Exception){Console.Error.WriteLine("Invalid miner input");return 1;}
 }
}
````
<!-- END FILE -->

### cpu-linux.cpp

<!-- FILE: cpu-linux.cpp SHA256: b70735b5ca55faff921bb7b04bc443ce15aa7fe237589d7e501460544a5a187c -->
````cpp
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>
#include <array>
#include <memory>
#include <atomic>
#include <thread>
#include <mutex>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <cstdint>
using namespace std;
static uint64_t Rol(uint64_t x,int n){return (x<<n)|(x>>(64-n));}
static uint64_t Swap(uint64_t x){return __builtin_bswap64(x);}
 static const uint64_t RC[]={0x0000000000000001ULL,0x0000000000008082ULL,0x800000000000808aULL,0x8000000080008000ULL,0x000000000000808bULL,0x0000000080000001ULL,0x8000000080008081ULL,0x8000000000008009ULL,0x000000000000008aULL,0x0000000000000088ULL,0x0000000080008009ULL,0x000000008000000aULL,0x000000008000808bULL,0x800000000000008bULL,0x8000000000008089ULL,0x8000000000008003ULL,0x8000000000008002ULL,0x8000000000000080ULL,0x000000000000800aULL,0x800000008000000aULL,0x8000000080008081ULL,0x8000000000008080ULL,0x0000000080000001ULL,0x8000000080008008ULL};
 static void Permute(uint64_t* a){
  for(int r=0;r<24;r++){
   uint64_t c0=a[0]^a[5]^a[10]^a[15]^a[20];
   uint64_t c1=a[1]^a[6]^a[11]^a[16]^a[21];
   uint64_t c2=a[2]^a[7]^a[12]^a[17]^a[22];
   uint64_t c3=a[3]^a[8]^a[13]^a[18]^a[23];
   uint64_t c4=a[4]^a[9]^a[14]^a[19]^a[24];
   uint64_t d0=c4^Rol(c1,1);
   a[0]^=d0;
   a[5]^=d0;
   a[10]^=d0;
   a[15]^=d0;
   a[20]^=d0;
   uint64_t d1=c0^Rol(c2,1);
   a[1]^=d1;
   a[6]^=d1;
   a[11]^=d1;
   a[16]^=d1;
   a[21]^=d1;
   uint64_t d2=c1^Rol(c3,1);
   a[2]^=d2;
   a[7]^=d2;
   a[12]^=d2;
   a[17]^=d2;
   a[22]^=d2;
   uint64_t d3=c2^Rol(c4,1);
   a[3]^=d3;
   a[8]^=d3;
   a[13]^=d3;
   a[18]^=d3;
   a[23]^=d3;
   uint64_t d4=c3^Rol(c0,1);
   a[4]^=d4;
   a[9]^=d4;
   a[14]^=d4;
   a[19]^=d4;
   a[24]^=d4;
   uint64_t t=a[1],u;
   u=a[10];a[10]=Rol(t,1);t=u;
   u=a[7];a[7]=Rol(t,3);t=u;
   u=a[11];a[11]=Rol(t,6);t=u;
   u=a[17];a[17]=Rol(t,10);t=u;
   u=a[18];a[18]=Rol(t,15);t=u;
   u=a[3];a[3]=Rol(t,21);t=u;
   u=a[5];a[5]=Rol(t,28);t=u;
   u=a[16];a[16]=Rol(t,36);t=u;
   u=a[8];a[8]=Rol(t,45);t=u;
   u=a[21];a[21]=Rol(t,55);t=u;
   u=a[24];a[24]=Rol(t,2);t=u;
   u=a[4];a[4]=Rol(t,14);t=u;
   u=a[15];a[15]=Rol(t,27);t=u;
   u=a[23];a[23]=Rol(t,41);t=u;
   u=a[19];a[19]=Rol(t,56);t=u;
   u=a[13];a[13]=Rol(t,8);t=u;
   u=a[12];a[12]=Rol(t,25);t=u;
   u=a[2];a[2]=Rol(t,43);t=u;
   u=a[20];a[20]=Rol(t,62);t=u;
   u=a[14];a[14]=Rol(t,18);t=u;
   u=a[22];a[22]=Rol(t,39);t=u;
   u=a[9];a[9]=Rol(t,61);t=u;
   u=a[6];a[6]=Rol(t,20);t=u;
   u=a[1];a[1]=Rol(t,44);t=u;
   c0=a[0];
   c1=a[1];
   c2=a[2];
   c3=a[3];
   c4=a[4];
   a[0]=c0^((~c1)&c2);
   a[1]=c1^((~c2)&c3);
   a[2]=c2^((~c3)&c4);
   a[3]=c3^((~c4)&c0);
   a[4]=c4^((~c0)&c1);
   c0=a[5];
   c1=a[6];
   c2=a[7];
   c3=a[8];
   c4=a[9];
   a[5]=c0^((~c1)&c2);
   a[6]=c1^((~c2)&c3);
   a[7]=c2^((~c3)&c4);
   a[8]=c3^((~c4)&c0);
   a[9]=c4^((~c0)&c1);
   c0=a[10];
   c1=a[11];
   c2=a[12];
   c3=a[13];
   c4=a[14];
   a[10]=c0^((~c1)&c2);
   a[11]=c1^((~c2)&c3);
   a[12]=c2^((~c3)&c4);
   a[13]=c3^((~c4)&c0);
   a[14]=c4^((~c0)&c1);
   c0=a[15];
   c1=a[16];
   c2=a[17];
   c3=a[18];
   c4=a[19];
   a[15]=c0^((~c1)&c2);
   a[16]=c1^((~c2)&c3);
   a[17]=c2^((~c3)&c4);
   a[18]=c3^((~c4)&c0);
   a[19]=c4^((~c0)&c1);
   c0=a[20];
   c1=a[21];
   c2=a[22];
   c3=a[23];
   c4=a[24];
   a[20]=c0^((~c1)&c2);
   a[21]=c1^((~c2)&c3);
   a[22]=c2^((~c3)&c4);
   a[23]=c3^((~c4)&c0);
   a[24]=c4^((~c0)&c1);
   a[0]^=RC[r];
  }
 }

struct Job{array<uint64_t,25> base{};array<uint64_t,4> target{};string prefix;};
shared_ptr<Job> current;atomic<bool> quit(false);atomic<uint64_t> total(0);mutex output;
vector<uint8_t> unhex(string s){if(s.substr(0,2)=="0x")s=s.substr(2);if(s.size()%2)throw runtime_error("hex");vector<uint8_t>b(s.size()/2);for(size_t i=0;i<b.size();i++)b[i]=stoul(s.substr(i*2,2),nullptr,16);return b;}
void emit(const string&s){lock_guard<mutex>l(output);cout<<s<<endl;}
shared_ptr<Job> makeJob(string seed,string addr,string target,string prefix){
 auto s=unhex(seed),a=unhex(addr),t=unhex(target),p=unhex(prefix);if(s.size()!=32||a.size()!=20||t.size()!=32||p.size()!=24)throw runtime_error("length");
 auto j=make_shared<Job>();uint8_t m[200]={0};memcpy(m,s.data(),32);memcpy(m+32,a.data(),20);memcpy(m+52,p.data(),24);m[84]=1;m[135]=128;memcpy(j->base.data(),m,200);memcpy(j->target.data(),t.data(),32);for(auto &w:j->target)w=Swap(w);j->prefix=prefix;return j;
}
void hashNonce(const Job&j,uint64_t nonce,uint64_t*a){memcpy(a,j.base.data(),200);uint64_t b=Swap(nonce);a[9]=(a[9]&0xffffffffULL)|(b<<32);a[10]=(a[10]&0xffffffff00000000ULL)|(b>>32);Permute(a);}
string digest(uint64_t*a){ostringstream o;for(int k=0;k<4;k++)o<<hex<<setfill('0')<<setw(16)<<Swap(a[k]);return o.str();}
bool under(uint64_t*a,const Job&j){for(int k=0;k<4;k++){uint64_t w=Swap(a[k]);if(w<j.target[k])return true;if(w>j.target[k])return false;}return false;}
void work(int id,int nthreads){uint64_t n=id,a[25];while(!quit){auto j=atomic_load(&current);if(!j){this_thread::sleep_for(chrono::milliseconds(30));continue;}for(int k=0;k<4096;k++){hashNonce(*j,n,a);if(under(a,*j)){ostringstream o;o<<"FOUND "<<j->prefix<<hex<<setfill('0')<<setw(16)<<n<<" "<<digest(a);emit(o.str());}n+=nthreads;}total+=4096;}}
int main(int argc,char**argv){try{
 if(argc==5&&string(argv[1])=="hash"){auto n=unhex(argv[4]);if(n.size()!=32)throw runtime_error("nonce");auto j=makeJob(argv[2],argv[3],string(64,'f'),string(argv[4]).substr(0,48));uint64_t nonce,a[25];memcpy(&nonce,n.data()+24,8);hashNonce(*j,Swap(nonce),a);cout<<digest(a)<<endl;return 0;}
 int count=argc>1?stoi(argv[1]):thread::hardware_concurrency();if(count<1||count>512)throw runtime_error("threads");vector<thread> threads;for(int i=0;i<count;i++)threads.emplace_back(work,i,count);
 thread rate([]{uint64_t last=0;auto before=chrono::steady_clock::now();while(!quit){this_thread::sleep_for(chrono::seconds(1));uint64_t n=total.load();auto now=chrono::steady_clock::now();double seconds=chrono::duration<double>(now-before).count();emit("RATE "+to_string(uint64_t((n-last)/seconds))+" "+to_string(n));last=n;before=now;}});
 string line;while(getline(cin,line)){if(line=="QUIT")break;if(line=="STOP"){atomic_store(&current,shared_ptr<Job>());continue;}istringstream in(line);string cmd,seed,addr,target,prefix;in>>cmd>>seed>>addr>>target>>prefix;if(cmd!="JOB")throw runtime_error("command");atomic_store(&current,makeJob(seed,addr,target,prefix));}
 quit=true;for(auto&t:threads)t.join();rate.join();return 0;
 }catch(...){cerr<<"Invalid worker input"<<endl;return 1;}}
````
<!-- END FILE -->

### gpu-miner.cu

<!-- FILE: gpu-miner.cu SHA256: 954e969bb7d4954b6c009f53c60686d4bd50a6f3a27aa08385370e0c26212a40 -->
````cpp
// PRSPCT: Ethereum Keccak-256(seed[32] || address[20] || nonce[32]).
// One process per CUDA device; no wallet keys or network access in this worker.
#include <cuda_runtime.h>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>
#include <array>
#include <memory>
#include <atomic>
#include <thread>
#include <mutex>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <cstdint>
#include <algorithm>
using namespace std;
using Clock = chrono::steady_clock;
static void check(cudaError_t e) { if(e != cudaSuccess) throw runtime_error(cudaGetErrorString(e)); }
__device__ __forceinline__ uint64_t rol(uint64_t x, int n) {return (x << n) | (x >> (64-n));}
__host__ __device__ inline uint64_t swap64(uint64_t x) {
  x=((x & 0x00ff00ff00ff00ffULL)<<8)|((x>>8)&0x00ff00ff00ff00ffULL);
  x=((x & 0x0000ffff0000ffffULL)<<16)|((x>>16)&0x0000ffff0000ffffULL);
  return (x<<32)|(x>>32);
}
__constant__ uint64_t RC[24] = {
  0x0000000000000001ULL,0x0000000000008082ULL,0x800000000000808aULL,0x8000000080008000ULL,
  0x000000000000808bULL,0x0000000080000001ULL,0x8000000080008081ULL,0x8000000000008009ULL,
  0x000000000000008aULL,0x0000000000000088ULL,0x0000000080008009ULL,0x000000008000000aULL,
  0x000000008000808bULL,0x800000000000008bULL,0x8000000000008089ULL,0x8000000000008003ULL,
  0x8000000000008002ULL,0x8000000000000080ULL,0x000000000000800aULL,0x800000008000000aULL,
  0x8000000080008081ULL,0x8000000000008080ULL,0x0000000080000001ULL,0x8000000080008008ULL
};
__device__ __forceinline__ void permute(uint64_t *a) {
  #pragma unroll 1
  for(int r=0;r<24;r++) {
    uint64_t c0=a[0]^a[5]^a[10]^a[15]^a[20];
    uint64_t c1=a[1]^a[6]^a[11]^a[16]^a[21];
    uint64_t c2=a[2]^a[7]^a[12]^a[17]^a[22];
    uint64_t c3=a[3]^a[8]^a[13]^a[18]^a[23];
    uint64_t c4=a[4]^a[9]^a[14]^a[19]^a[24];
    uint64_t d0=c4^rol(c1,1),d1=c0^rol(c2,1),d2=c1^rol(c3,1),d3=c2^rol(c4,1),d4=c3^rol(c0,1);
    a[0]^=d0;a[5]^=d0;a[10]^=d0;a[15]^=d0;a[20]^=d0;
    a[1]^=d1;a[6]^=d1;a[11]^=d1;a[16]^=d1;a[21]^=d1;
    a[2]^=d2;a[7]^=d2;a[12]^=d2;a[17]^=d2;a[22]^=d2;
    a[3]^=d3;a[8]^=d3;a[13]^=d3;a[18]^=d3;a[23]^=d3;
    a[4]^=d4;a[9]^=d4;a[14]^=d4;a[19]^=d4;a[24]^=d4;
    uint64_t t=a[1],u;
    u=a[10];a[10]=rol(t,1);t=u;
    u=a[7];a[7]=rol(t,3);t=u;
    u=a[11];a[11]=rol(t,6);t=u;
    u=a[17];a[17]=rol(t,10);t=u;
    u=a[18];a[18]=rol(t,15);t=u;
    u=a[3];a[3]=rol(t,21);t=u;
    u=a[5];a[5]=rol(t,28);t=u;
    u=a[16];a[16]=rol(t,36);t=u;
    u=a[8];a[8]=rol(t,45);t=u;
    u=a[21];a[21]=rol(t,55);t=u;
    u=a[24];a[24]=rol(t,2);t=u;
    u=a[4];a[4]=rol(t,14);t=u;
    u=a[15];a[15]=rol(t,27);t=u;
    u=a[23];a[23]=rol(t,41);t=u;
    u=a[19];a[19]=rol(t,56);t=u;
    u=a[13];a[13]=rol(t,8);t=u;
    u=a[12];a[12]=rol(t,25);t=u;
    u=a[2];a[2]=rol(t,43);t=u;
    u=a[20];a[20]=rol(t,62);t=u;
    u=a[14];a[14]=rol(t,18);t=u;
    u=a[22];a[22]=rol(t,39);t=u;
    u=a[9];a[9]=rol(t,61);t=u;
    u=a[6];a[6]=rol(t,20);t=u;
    a[1]=rol(t,44);
    #pragma unroll
    for(int row=0;row<25;row+=5) {
      c0=a[row];c1=a[row+1];c2=a[row+2];c3=a[row+3];c4=a[row+4];
      a[row]=c0^((~c1)&c2);a[row+1]=c1^((~c2)&c3);a[row+2]=c2^((~c3)&c4);
      a[row+3]=c3^((~c4)&c0);a[row+4]=c4^((~c0)&c1);
    }
    a[0]^=RC[r];
  }
}
struct DeviceJob {uint64_t base[25],target[4];};
struct Found {uint64_t nonce,hash[4];};
struct Results {unsigned int count;Found found[64];};
__constant__ DeviceJob deviceJob;
__device__ __forceinline__ void hashNonce(uint64_t nonce,uint64_t *a) {
  #pragma unroll
  for(int i=0;i<25;i++)a[i]=deviceJob.base[i];
  uint64_t n=swap64(nonce);
  a[9]=(a[9]&0xffffffffULL)|(n<<32);
  a[10]=(a[10]&0xffffffff00000000ULL)|(n>>32);
  permute(a);
}
__device__ __forceinline__ bool under(uint64_t *a) {
  #pragma unroll
  for(int i=0;i<4;i++) {
    uint64_t h=swap64(a[i]);
    if(h<deviceJob.target[i])return true;
    if(h>deviceJob.target[i])return false;
  }
  return false; // Contract uses strict hash < target.
}
__global__ void mineKernel(uint64_t start,uint64_t count,Results *result) {
  const uint64_t stride=(uint64_t)gridDim.x*blockDim.x;
  for(uint64_t i=(uint64_t)blockIdx.x*blockDim.x+threadIdx.x;i<count;i+=stride) {
    uint64_t a[25];hashNonce(start+i,a);
    if(under(a)) {
      unsigned int index=atomicAdd(&result->count,1U);
      if(index<64) {
        result->found[index].nonce=start+i;
        #pragma unroll
        for(int k=0;k<4;k++)result->found[index].hash[k]=a[k];
      }
    }
  }
}
__global__ void oneHashKernel(uint64_t nonce,uint64_t *digest) {
  uint64_t a[25];hashNonce(nonce,a);
  for(int k=0;k<4;k++)digest[k]=a[k];
}
struct Job {DeviceJob data{};string prefix;};
shared_ptr<Job> current;atomic<bool> quitting(false);mutex output;atomic<uint64_t> hashes(0);
bool traceJobs=false; // Opt-in integration-test diagnostics; absent in production.
void emit(const string& text) {lock_guard<mutex> lock(output);cout<<text<<endl;}
vector<uint8_t> unhex(string value) {
  if(value.compare(0,2,"0x")==0)value=value.substr(2);
  if(value.size()%2)throw runtime_error("odd hex length");
  vector<uint8_t> out(value.size()/2);
  auto digit=[](char c)->int {if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;throw runtime_error("invalid hex");};
  for(size_t i=0;i<out.size();i++)out[i]=(digit(value[i*2])<<4)|digit(value[i*2+1]);
  return out;
}
string hexBytes(const uint8_t *b,size_t n) {ostringstream out;for(size_t i=0;i<n;i++)out<<hex<<setfill('0')<<setw(2)<<(unsigned)b[i];return out.str();}
shared_ptr<Job> makeJob(const string &seed,const string &address,const string &target,const string &prefix) {
  auto s=unhex(seed),a=unhex(address),t=unhex(target),p=unhex(prefix);
  if(s.size()!=32||a.size()!=20||t.size()!=32||p.size()!=24)throw runtime_error("invalid job field length");
  auto j=make_shared<Job>();uint8_t *base=(uint8_t*)j->data.base;
  memcpy(base,s.data(),32);memcpy(base+32,a.data(),20);memcpy(base+52,p.data(),24);
  base[84]=1;base[135]=128; // Keccak padding (not SHA3 domain separator).
  memcpy(j->data.target,t.data(),32);
  for(auto &v:j->data.target)v=swap64(v);
  j->prefix=hexBytes(p.data(),24);return j;
}
void selectDevice(int device) {check(cudaSetDevice(device));check(cudaFree(nullptr));}
string digestHex(const uint64_t *digest) {return hexBytes((const uint8_t*)digest,32);}
void mine(int device) {
  try {
    selectDevice(device);cudaDeviceProp props;check(cudaGetDeviceProperties(&props,device));
    Results *gpu;check(cudaMalloc((void**)&gpu,sizeof(Results)));
    shared_ptr<Job> loaded;uint64_t start=0,batch=1ULL<<20;
    auto report=Clock::now();uint64_t last=0;
    while(!quitting) {
      auto j=atomic_load(&current);
      if(!j){this_thread::sleep_for(chrono::milliseconds(20));}
      else {
        if(j!=loaded){
          check(cudaMemcpyToSymbol(deviceJob,&j->data,sizeof(DeviceJob)));loaded=j;
          // Target/depth updates reuse this connection's nonce prefix. Preserve
          // the counter so a new JOB never repeats previously searched nonces.
          if(traceJobs)emit("JOB_START "+to_string(start));
        }
        check(cudaMemset(gpu,0,sizeof(unsigned int)));
        const auto began=Clock::now();
        mineKernel<<<props.multiProcessorCount*8,128>>>(start,batch,gpu);check(cudaGetLastError());
        Results found;check(cudaMemcpy(&found,gpu,sizeof(found),cudaMemcpyDeviceToHost));
        hashes+=batch;start+=batch;
        // Drop old-job discoveries if STOP/new JOB arrived during kernel execution.
        if(j==atomic_load(&current))for(unsigned i=0;i<found.count&&i<64;i++) {
          ostringstream line;line<<"FOUND "<<j->prefix<<hex<<setfill('0')<<setw(16)<<found.found[i].nonce<<" "<<digestHex(found.found[i].hash);emit(line.str());
        }
        // Bound kernel latency near 100ms while avoiding tiny launches.
        double elapsed=chrono::duration<double>(Clock::now()-began).count();
        if(elapsed>0){double next=(double)batch*.10/elapsed;batch=(uint64_t)max(65536.0,min(16777216.0,next));}
      }
      auto now=Clock::now();double seconds=chrono::duration<double>(now-report).count();
      if(seconds>=1){uint64_t total=hashes.load();emit("RATE "+to_string((uint64_t)((total-last)/seconds))+" "+to_string(total));last=total;report=now;}
    }
    check(cudaFree(gpu));
  }catch(const exception& e){cerr<<"CUDA worker error: "<<e.what()<<endl;quitting=true;exit(2);}
}
int main(int argc,char **argv) {
  try {
    if(argc>=5&&string(argv[1])=="hash") {
      auto nonce=unhex(argv[4]);if(nonce.size()!=32)throw runtime_error("invalid nonce length");
      selectDevice(argc>5?stoi(argv[5]):0);
      auto j=makeJob(argv[2],argv[3],string(64,'f'),hexBytes(nonce.data(),24));
      check(cudaMemcpyToSymbol(deviceJob,&j->data,sizeof(DeviceJob)));
      uint64_t n;memcpy(&n,nonce.data()+24,8);n=swap64(n);
      uint64_t *gpu;check(cudaMalloc((void**)&gpu,32));oneHashKernel<<<1,1>>>(n,gpu);check(cudaGetLastError());
      uint64_t digest[4];check(cudaMemcpy(digest,gpu,32,cudaMemcpyDeviceToHost));check(cudaFree(gpu));
      cout<<digestHex(digest)<<endl;return 0;
    }
    int device=argc>1&&string(argv[1])!="benchmark"?stoi(argv[1]):0;
    traceJobs=argc>2&&string(argv[2])=="--trace-jobs";
    if(argc>1&&string(argv[1])=="benchmark")device=argc>2?stoi(argv[2]):0;
    if(argc>1&&string(argv[1])=="benchmark") {
      atomic_store(&current,makeJob(string(64,'0'),string(40,'0'),string(64,'0'),string(48,'0')));
      thread worker(mine,device);this_thread::sleep_for(chrono::seconds(argc>3?stoi(argv[3]):3));quitting=true;worker.join();return 0;
    }
    thread worker(mine,device);
    string line;
    try {
      while(getline(cin,line)) {
        if(line=="QUIT")break;
        if(line=="STOP"){atomic_store(&current,shared_ptr<Job>());continue;}
        istringstream in(line);string cmd,seed,address,target,prefix,extra;
        if(!(in>>cmd>>seed>>address>>target>>prefix)||cmd!="JOB"||(in>>extra))throw runtime_error("invalid worker input");
        atomic_store(&current,makeJob(seed,address,target,prefix));
      }
    }catch(...){quitting=true;worker.join();throw;}
    quitting=true;worker.join();return 0;
  }catch(const exception& e){cerr<<"Worker error: "<<e.what()<<endl;return 1;}
}
````
<!-- END FILE -->

### deploy_cpu_selected.py

<!-- FILE: deploy_cpu_selected.py SHA256: c8957ef972543ada0ae5af06ef8fc8ce72a742bfc69cb554bd63262ea0f2ea26 -->
````python
"""Provision CPU workers only for explicitly selected, enabled rental ports."""
import concurrent.futures,json,sys,time,argparse
import controller as m
from setup_credentials import unprotect
from probe_servers import connect
from deploy_new_cpu_fleet import deploy,merge_keys
parser=argparse.ArgumentParser()
parser.add_argument('ports',nargs='+',type=int)
parser.add_argument('--relay',type=int)
args=parser.parse_args()
ports=set(args.ports)
if args.relay is not None:
    from deploy_gpu import deployment_connect
    import deploy_cpu_servers,probe_servers
    def connect(row):return deployment_connect(row,args.relay)
    # Only this setup process uses the explicit relay. Live controller is untouched.
    deploy_cpu_servers.connect=connect
    probe_servers.connect=connect
blocked=json.loads((m.ROOT/'disabled-ports.json').read_text()) if (m.ROOT/'disabled-ports.json').exists() else []
if not ports or ports.intersection(blocked):raise SystemExit('Select enabled ports explicitly')
rows=[r for r in json.loads(unprotect((m.ROOT/'ssh-secrets.dpapi').read_bytes())) if r['port'] in ports]
if len(rows)!=len(ports):raise SystemExit('Missing or ambiguous server credentials')
def run(row):
    for attempt in range(90):
        client=None
        try:
            client=connect(row)
            _,out,_=client.exec_command('command -v g++',timeout=10)
            if out.channel.recv_exit_status()==0:break
        except Exception:
            if attempt==89:raise
        finally:
            if client:client.close()
        time.sleep(2)
    else:raise RuntimeError('Compiler not ready')
    result=deploy(row)
    if result['status']=='ready':merge_keys([result])
    return {k:v for k,v in result.items() if k!='keys'}
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=len(rows)) as pool:
    for task in concurrent.futures.as_completed([pool.submit(run,r) for r in rows]):
        result=task.result();results.append(result)
        m.write_json(m.ROOT/('cpu-'+str(result['port'])+'-ready.json'),result)
        print(json.dumps(result),flush=True)
if any(r['status']!='ready' for r in results):raise SystemExit(1)
````
<!-- END FILE -->

### deploy_cpu_servers.py

<!-- FILE: deploy_cpu_servers.py SHA256: ee8a6c4cba778d7cc3c105cf989050513f86cb0f38c89a6b61b8153f98bde4d5 -->
````python
import controller as m,json,concurrent.futures,time,secrets
from setup_credentials import unprotect
from probe_servers import connect
def deploy(row):
 c=None
 try:
  c=connect(row);transport=c.get_transport();transport.set_keepalive(15)
  cmd='command -v g++ >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq g++)'
  _,out,err=c.exec_command(cmd,timeout=240)
  stdout=out.read();error=err.read();code=out.channel.recv_exit_status()
  if code:raise RuntimeError('Compiler install failed: '+error.decode()[-250:])
  sftp=c.open_sftp()
  try:sftp.mkdir('/root/prspct-cpu')
  except IOError:pass
  sftp.put(str(m.ROOT/'cpu-linux.cpp'),'/root/prspct-cpu/cpu-linux.cpp');sftp.close()
  _,out,err=c.exec_command('g++ -O3 -march=native -std=c++11 -pthread /root/prspct-cpu/cpu-linux.cpp -o /root/prspct-cpu/cpu-miner',timeout=60)
  code=out.channel.recv_exit_status()
  if code:raise RuntimeError('Compile failed '+err.read().decode()[-600:])
  for _ in range(3):
   seed=secrets.token_hex(32);nonce=secrets.token_hex(32)
   _,out,err=c.exec_command('/root/prspct-cpu/cpu-miner hash '+seed+' '+m.ADDRESS[2:]+' '+nonce,timeout=8)
   got=out.read().decode().strip();expected=m.kh(bytes.fromhex(seed+m.ADDRESS[2:]+nonce)).hex()
   if got!=expected:raise RuntimeError('Remote Keccak validation failed')
  _,out,_=c.exec_command('nproc',timeout=8);threads=int(out.read())
  # Remember validated host keys without changing other hosts' entries (caller serializes writes).
  keys=[(name,kind,key.get_base64()) for name,items in c.get_host_keys().items() for kind,key in items.items()]
  return {'port':row['port'],'host':row['host'],'threads':threads,'status':'ready','keys':keys}
 except Exception as e:return {'port':row['port'],'host':row['host'],'status':'error','error':str(e)[:600]}
 finally:
  if c:c.close()
````
<!-- END FILE -->

### deploy_new_cpu_fleet.py

<!-- FILE: deploy_new_cpu_fleet.py SHA256: b522763b13d69c45491e3d91c5c4ae174c7752c2c0eb932fdb0dbbf111144590 -->
````python
"""Deploy and validate CPU workers and merge verified SSH host keys."""
import concurrent.futures
import json
import msvcrt
import os
import secrets
import time
import controller as m
import paramiko
import probe_servers
from setup_credentials import unprotect
import deploy_cpu_servers


class RememberInMemory(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self,client,hostname,key):
        client.get_host_keys().add(hostname,key.get_name(),key)

# Paramiko's default AutoAddPolicy writes implicitly. Keep additions in memory,
# then merge them below under a file lock with an atomic destination replacement.
probe_servers.paramiko.AutoAddPolicy=RememberInMemory

def merge_keys(rows):
    path=m.ROOT/'ssh-known-hosts'
    lock_path=m.ROOT/'ssh-known-hosts.lock'
    with lock_path.open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        try:
            keys=paramiko.HostKeys()
            if path.exists():keys.load(str(path))
            for result in rows:
                for host,kind,encoded in result.get('keys',[]):
                    entry=paramiko.hostkeys.HostKeyEntry.from_line(host+' '+kind+' '+encoded)
                    if host in keys and kind in keys[host] and keys[host][kind]!=entry.key:
                        raise RuntimeError('Host key changed for '+host)
                    keys.add(host,kind,entry.key)
            tmp=path.with_name(path.name+'.'+secrets.token_hex(4)+'.tmp')
            keys.save(str(tmp));os.replace(tmp,path)
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def deploy(row):
    result=deploy_cpu_servers.deploy(row)
    if result['status']=='ready':
        c=None
        try:
            c=probe_servers.connect(row)
            _,out,_=c.exec_command('lscpu -J',timeout=10)
            values=json.loads(out.read())['lscpu']
            result['cpu']=next((v['data'] for v in values if v['field']=='Model name:'),'unknown')
            result['validation']='3 random 84-byte Keccak-256 vectors matched local Ethereum Keccak'
        except Exception as e:
            result['inventory_error']=type(e).__name__
        finally:
            if c:c.close()
    return result
````
<!-- END FILE -->

### prepare_cuda_tools.py

<!-- FILE: prepare_cuda_tools.py SHA256: f67834363bcff594f77cc3e5509063ac892f38bc8eecb5ff79b6b07d3592e139 -->
````python
"""Install official portable compiler dependencies only; no mining starts."""
import concurrent.futures,json,urllib.request,shlex,sys
import controller as m
from setup_credentials import unprotect
from probe_servers import connect
from merge_gpu_metadata import merge

def metadata():
 url='https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.4.1.json'
 data=json.load(urllib.request.urlopen(url,timeout=30))
 result=[]
 for name in ('cuda_nvcc','cuda_cudart','cuda_cccl'):
  record=data[name]['linux-x86_64'];result.append({'name':name,'url':'https://developer.download.nvidia.com/compute/cuda/redist/'+record['relative_path'],'sha256':record['sha256']})
 return result

def install(row,packages):
 c=None
 try:
  c=connect(row);c.get_transport().set_keepalive(15)
  _,out,err=c.exec_command('command -v g++ >/dev/null 2>&1 || (apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update -qq && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 install -y -qq g++)',timeout=240)
  error=err.read().decode();code=out.channel.recv_exit_status()
  if code: raise RuntimeError('Compiler install failed '+error[-300:])
  print(json.dumps({'port':row['port'],'stage':'g++ ready'}),flush=True)
  script='''import urllib.request,tarfile,hashlib,pathlib,json,subprocess
packages=PACKAGES
root=pathlib.Path('/root/prspct-cuda');root.mkdir(exist_ok=True)
for package in packages:
 archive=root/(package['name']+'.tar.xz')
 if not archive.exists(): urllib.request.urlretrieve(package['url'],archive)
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=package['sha256']: raise RuntimeError('NVIDIA archive checksum mismatch')
 with tarfile.open(archive) as source:
  for member in source.getmembers():
   pieces=pathlib.PurePosixPath(member.name).parts
   if len(pieces)<2: continue
   member.name=str(pathlib.PurePosixPath(*pieces[1:]))
   if '..' in pathlib.PurePosixPath(member.name).parts or member.name.startswith('/'): raise RuntimeError('Unsafe archive member')
   source.extract(member,root)
 print('Installed '+package['name'],flush=True)
if not (root/'lib64').exists(): (root/'lib64').symlink_to('lib',target_is_directory=True)
probe=subprocess.run([str(root/'bin/nvcc'),'--version'],capture_output=True,text=True)
print(probe.stdout,flush=True)
if probe.returncode: raise RuntimeError(probe.stderr)
'''.replace('PACKAGES',repr(packages))
  command='python3 - '+shlex.quote(json.dumps(packages))+' <<\'PRSPCT_TOOLKIT_SCRIPT\'\n'+script+'\nPRSPCT_TOOLKIT_SCRIPT'
  _,out,err=c.exec_command(command,timeout=300)
  log=out.read().decode();errors=err.read().decode();code=out.channel.recv_exit_status()
  return {'port':row['port'],'status':'ready' if not code else 'error','nvcc':'/root/prspct-cuda/bin/nvcc','log':log,'error':errors[-1500:]}
 except Exception as e:return {'port':row['port'],'status':'error','error':str(e)[:500]}
 finally:
  if c:c.close()

if __name__=='__main__':
 packages=metadata();print(json.dumps(packages),flush=True)
 if len(sys.argv)<2: raise SystemExit('Pass explicit enabled SSH ports')
 selected=set(map(int,sys.argv[1:]))
 blocked=json.loads((m.ROOT/'disabled-ports.json').read_text()) if (m.ROOT/'disabled-ports.json').exists() else []
 if selected.intersection(blocked): raise SystemExit('Selected ports include a disabled endpoint')
 rows=[r for r in json.loads(unprotect((m.ROOT/'ssh-secrets.dpapi').read_bytes())) if r['port'] in selected]
 if len(rows)!=len(selected): raise SystemExit('Missing or ambiguous credentials')
 results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for task in concurrent.futures.as_completed([pool.submit(install,row,packages) for row in rows]):
   result=task.result();results.append(result);print(json.dumps(result),flush=True)
   merge(m.ROOT/'cuda-toolkit-ready.json',[result])
````
<!-- END FILE -->

### merge_gpu_metadata.py

<!-- FILE: merge_gpu_metadata.py SHA256: aa232ac12142cd4da18732f637f4c0163a4929383b6d16e59933039a75eddd4b -->
````python
"""Serialize metadata merges; a failed probe cannot undo a successful install."""
import json,os,time,uuid
from pathlib import Path
def merge(path,rows):
 path=Path(path);lock=path.with_suffix(path.suffix+'.lock');deadline=time.monotonic()+20
 while True:
  try:
   descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(descriptor);break
  except FileExistsError:
   if time.monotonic()>deadline:raise TimeoutError('Metadata merge lock busy')
   time.sleep(.05)
 try:
  previous=json.loads(path.read_text()) if path.exists() else []
  data={r['port']:r for r in previous}
  for row in rows:
   old=data.get(row['port'],{})
   if old.get('status') in ('ready','ok') and row.get('status')=='error':
    old['last_probe_error']=row.get('error');data[row['port']]=old
   else:data[row['port']]=row
  temp=path.with_suffix(path.suffix+'.'+uuid.uuid4().hex+'.tmp')
  temp.write_text(json.dumps(list(data.values()),indent=2));os.replace(temp,path)
 finally:lock.unlink()
````
<!-- END FILE -->

### deploy_gpu.py

<!-- FILE: deploy_gpu.py SHA256: 61b26e1b35ab66795b233843ceb13a2710053e1ff583a5c72a7522638b46eb5d -->
````python
"""Build and validate GPU workers; --replace rolls an atomic GPU-only update."""
import argparse, concurrent.futures, json, secrets, time, threading, hashlib, msvcrt, io, base64, zlib, shlex
import controller as m
from setup_credentials import unprotect
from probe_servers import connect, connect_once

REMOTE = '/root/prspct-cpu'
READY_LOCK = threading.Lock()

def deployment_connect(row, relay_port=None):
    if relay_port is None:
        return connect(row)
    rows = json.loads(unprotect((m.ROOT / 'ssh-secrets.dpapi').read_bytes()))
    relay_row = next(r for r in rows if r['port'] == relay_port)
    relay = connect_once(relay_row)
    try:
        channel = relay.get_transport().open_channel('direct-tcpip', (row['host'], row['port']), ('127.0.0.1', 0), timeout=12)
        client = connect_once(row, channel)
        close_client = client.close
        def close_both():
            try:
                close_client()
            finally:
                relay.close()
        client.close = close_both
        return client
    except Exception:
        relay.close()
        raise

def publish_ready(row, device):
    # Fleet watches this file. Publish a device only after its real CUDA hashes
    # and FOUND output have both passed independent host Keccak validation.
    path = m.ROOT / 'gpu-ready.json'
    with READY_LOCK, open(path.with_suffix('.lock'), 'a+b') as gate:
        gate.seek(0)
        if not gate.read(1):
            gate.write(b'0')
            gate.flush()
        gate.seek(0)
        # Separate deployment processes may finish different rented hosts at once.
        # Serialize the read/merge/write so neither drops the other's ready GPUs.
        msvcrt.locking(gate.fileno(), msvcrt.LK_LOCK, 1)
        try:
            ready = json.loads(path.read_text()) if path.exists() else {}
            key = row['host'] + ':' + str(row['port'])
            devices = ready.get(key, {}).get('devices', [])
            ready[key] = {'devices': sorted(set(devices + [device])), 'source_sha256': hashlib.sha256((m.ROOT / 'gpu-miner.cu').read_bytes()).hexdigest()}
            m.write_json(path, ready)
        finally:
            gate.seek(0)
            msvcrt.locking(gate.fileno(), msvcrt.LK_UNLCK, 1)

def command(c, text, timeout=60):
    _, out, err = c.exec_command(text, timeout=timeout)
    stdout = out.read().decode()
    stderr = err.read().decode()
    code = out.channel.recv_exit_status()
    if code:
        raise RuntimeError(str(code) + ': ' + stderr[-2500:] + stdout[-1000:])
    return stdout.strip()

def proof_protocol(c, binary, device, job, updated_job):
    # Wait for actual CUDA output instead of assuming startup finishes before a
    # fixed sleep. Rented CPU hosts can be fully occupied by their CPU miners.
    script = 'config=' + repr({'binary': binary, 'device': device, 'job': job, 'updated_job': updated_job}) + '\n' + r'''
import subprocess,select,os,time
p=subprocess.Popen([config['binary'],str(config['device']),'--trace-jobs'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
p.stdin.write((config['job']+'\n').encode());p.stdin.flush()
buffer=b'';found=0;starts=[];updated=False;deadline=time.monotonic()+25
while time.monotonic()<deadline:
 readable,_,_=select.select([p.stdout],[],[],.25)
 if not readable:
  if p.poll() is not None: break
  continue
 chunk=os.read(p.stdout.fileno(),65536)
 if not chunk: break
 buffer+=chunk
 while b'\n' in buffer:
  raw,buffer=buffer.split(b'\n',1);line=raw.decode()
  if line.startswith('JOB_START '):
   starts.append(int(line.split()[1]));print(line,flush=True)
  elif line.startswith('FOUND ') and found<3:
   print(line,flush=True);found+=1
   if found==3:
    p.stdin.write((config['updated_job']+'\n').encode());p.stdin.flush();updated=True
 if updated and len(starts)>=2: break
try:
 _,error=p.communicate(b'STOP\nQUIT\n',timeout=8)
except subprocess.TimeoutExpired:
 p.kill();_,error=p.communicate();raise RuntimeError('GPU worker did not stop')
if p.returncode: raise RuntimeError(error.decode()[-1000:])
if found!=3 or len(starts)!=2: raise RuntimeError('Incomplete GPU protocol: '+str((found,starts)))
if starts[0]!=0 or starts[1]<=starts[0]: raise RuntimeError('GPU target update reset nonce: '+str(starts))
'''
    return command(c, "python3 - <<'PRSPCT_GPU_TEST'\n" + script + '\nPRSPCT_GPU_TEST', 40)

def run(row, probe=False, existing=False, replace=False, audit=False, only_devices=None, relay_port=None, clone_from=None, atomic_copy=False):
    c = None
    try:
        c = deployment_connect(row, relay_port)
        c.get_transport().set_keepalive(10)
        if audit:
            script = """import os,json,hashlib
from pathlib import Path
binary=Path('/root/prspct-cpu/gpu-miner')
installed=binary.stat()
processes=[]
for process in Path('/proc').iterdir():
 if not process.name.isdecimal(): continue
 try:
  argv=(process/'cmdline').read_bytes().split(b'\\0')
  if argv[0]!=str(binary).encode(): continue
  running=(process/'exe').stat()
  processes.append({'pid':int(process.name),'device':argv[1].decode(),'current_binary':(running.st_dev,running.st_ino)==(installed.st_dev,installed.st_ino)})
 except (OSError,IndexError): pass
print(json.dumps({'processes':processes,'source_sha256':hashlib.sha256(Path('/root/prspct-cpu/gpu-miner.cu').read_bytes()).hexdigest()}))
"""
            info = json.loads(command(c, "python3 - <<'PY'\n" + script + '\nPY'))
            info['port'] = row['port']
            info['all_running_current_binary'] = bool(info['processes']) and all(p['current_binary'] for p in info['processes'])
            return info
        hardware = command(c, 'nvidia-smi --query-gpu=index,name,compute_cap,memory.total --format=csv,noheader; command -v nvcc || test ! -e /usr/local/cuda/bin/nvcc || echo /usr/local/cuda/bin/nvcc')
        result = {'port': row['port'], 'hardware': hardware}
        if probe:
            return result
        binary = REMOTE + ('/gpu-miner.candidate' if replace else '/gpu-miner')
        existing = existing or clone_from is not None
        compiler = ''
        if not existing:
            compiler = command(c, 'command -v nvcc || if test -x /root/prspct-cuda/bin/nvcc; then echo /root/prspct-cuda/bin/nvcc; elif test -x /usr/local/cuda/bin/nvcc; then echo /usr/local/cuda/bin/nvcc; fi')
            if not compiler:
                raise RuntimeError('nvcc is not installed')
        caps = command(c, 'nvidia-smi --query-gpu=compute_cap --format=csv,noheader').splitlines()
        available=set(range(len(caps)))
        if not available: raise RuntimeError('No GPU entries found')
        if only_devices is not None and (not only_devices or not only_devices.issubset(available)):
            raise ValueError('Selected device indices are outside the enumerated range')
        if replace and only_devices is not None and only_devices!=available:
            raise ValueError('Replacement requires validation of every enumerated GPU; do not use partial selection')
        arches = sorted(set(x.strip().replace('.', '') for x in caps))
        command(c, 'mkdir -p ' + REMOTE)
        if clone_from is not None:
            authorized = json.loads(unprotect((m.ROOT / 'ssh-secrets.dpapi').read_bytes()))
            source_row = next(r for r in authorized if r['port'] == clone_from)
            source_client = deployment_connect(source_row)
            try:
                print(json.dumps({'port': row['port'], 'stage': 'clone_source_connected', 'source_port': clone_from}), flush=True)
                with source_client.open_sftp() as source:
                    source.get_channel().settimeout(20)
                    source_text = source.open(REMOTE + '/gpu-miner.cu', 'rb').read()
                    if hashlib.sha256(source_text).digest() != hashlib.sha256((m.ROOT / 'gpu-miner.cu').read_bytes()).digest():
                        raise RuntimeError('Clone source revision mismatch')
                    download = io.BytesIO()
                    source.getfo(REMOTE + '/gpu-miner', download)
                    executable = download.getvalue()
                print(json.dumps({'port': row['port'], 'stage': 'clone_downloaded', 'bytes': len(executable)}), flush=True)
                # Some rental images stall on SFTP pipelined writes. A single
                # compressed payload over SSH stdin avoids that server bug.
                payload = json.dumps({binary: base64.b64encode(zlib.compress(executable)).decode(), REMOTE + '/gpu-miner.cu': base64.b64encode(zlib.compress(source_text)).decode()})
                upload_code = "import sys,json,base64,zlib,os; from pathlib import Path; p=json.loads(sys.stdin.readline()); [(Path(name+'.upload').write_bytes(zlib.decompress(base64.b64decode(data))),os.chmod(name+'.upload',448),os.replace(name+'.upload',name)) for name,data in p.items()]; print('installed')"
                stdin, stdout, stderr = c.exec_command('python3 -c ' + shlex.quote(upload_code), timeout=40)
                stdin.channel.sendall((payload + '\n').encode())
                uploaded = stdout.read().decode()
                upload_error = stderr.read().decode()
                if stdout.channel.recv_exit_status() or uploaded.strip() != 'installed':
                    raise RuntimeError('Compressed binary upload failed: ' + upload_error[-1000:])
                print(json.dumps({'port': row['port'], 'stage': 'cloned_compatible_binary', 'source_port': clone_from}), flush=True)
            finally:
                source_client.close()
        if atomic_copy:
            # Recover from an abandoned SFTP writer holding the old inode open.
            # Existing GPU processes, if any, retain their original executable.
            copy_code = "import os; from pathlib import Path; p=Path(" + repr(binary) + "); q=Path(str(p)+'.staged'); q.write_bytes(p.read_bytes()); q.chmod(448); os.replace(q,p)"
            command(c, 'python3 -c ' + shlex.quote(copy_code))
        if not existing:
            with c.open_sftp() as sftp:
                sftp.put(str(m.ROOT / 'gpu-miner.cu'), REMOTE + '/gpu-miner.cu')
        # Ampere native binary plus forward-compatible PTX for the Blackwell 5060.
        arch = '-gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_86,code=compute_86'
        compile_cmd = compiler + ' -O3 -std=c++14 -Xcompiler -pthread -L/root/prspct-cuda/lib -L/root/prspct-cuda/lib64 ' + arch + ' ' + REMOTE + '/gpu-miner.cu -o ' + binary
        if not existing:
            command(c, compile_cmd, 180)
        print(json.dumps({'port': row['port'], 'stage': 'compiled'}), flush=True)
        for device in range(len(caps)):
            if only_devices is not None and device not in only_devices:
                continue
            for _ in range(1 if replace else 4):
                seed, address, nonce = secrets.token_hex(32), secrets.token_hex(20), secrets.token_hex(32)
                expected = m.kh(bytes.fromhex(seed + address + nonce)).hex()
                got = command(c, binary + ' hash ' + seed + ' ' + address + ' ' + nonce + ' ' + str(device))
                if got != expected:
                    raise RuntimeError('GPU hash mismatch on device ' + str(device))
            print(json.dumps({'port': row['port'], 'device': device, 'stage': 'hash_vectors_passed'}), flush=True)
            if not replace:
                result['benchmark_' + str(device)] = command(c, binary + ' benchmark ' + str(device) + ' 3')
                print(json.dumps({'port': row['port'], 'device': device, 'stage': 'benchmark', 'output': result['benchmark_' + str(device)]}), flush=True)
            # Easy-target JOB must emit full nonce and digest that verify independently.
            seed, address, prefix = secrets.token_hex(32), secrets.token_hex(20), secrets.token_hex(24)
            target = '0000' + 'f' * 60
            job = 'JOB ' + seed + ' ' + address + ' ' + target + ' ' + prefix
            updated_job = 'JOB ' + seed + ' ' + address + ' ' + '0' * 64 + ' ' + prefix
            protocol = proof_protocol(c, binary, device, job, updated_job)
            verified = 0
            for line in protocol.splitlines():
                if line.startswith('FOUND '):
                    _, nonce, digest = line.split()
                    expected = m.kh(bytes.fromhex(seed + address + nonce)).hex()
                    if nonce[:48] != prefix or digest != expected or int(digest, 16) >= int(target, 16):
                        raise RuntimeError('GPU FOUND validation failed')
                    verified += 1
                    if verified == 3:
                        break
            if verified != 3:
                raise RuntimeError('GPU mining protocol test failed: ' + str(verified))
            result['verified_' + str(device)] = verified
            # The proof test changed only target, retaining the nonce prefix.
            starts = [int(line.split()[1]) for line in protocol.splitlines() if line.startswith('JOB_START ')]
            if len(starts) != 2 or starts[0] != 0 or starts[1] <= starts[0]:
                raise RuntimeError('Target update repeated nonce range: ' + repr(starts))
            result['target_update_starts_' + str(device)] = starts
            print(json.dumps({'port': row['port'], 'device': device, 'stage': 'target_update_regression_passed', 'starts': starts}), flush=True)
            if not replace:
                publish_ready(row, device)
        if replace:
            command(c, 'mv -f ' + binary + ' ' + REMOTE + '/gpu-miner')
            # Only production GPU workers; CPU workers and controller are untouched.
            command(c, "pkill -f '^/root/prspct-cpu/gpu-miner [0-9]+$' || test $? -eq 1")
            for device in range(len(caps)):
                if only_devices is None or device in only_devices:
                    publish_ready(row, device)
            result['updated'] = True
        result['status'] = 'ready'
        return result
    except Exception as exc:
        return {'port': row['port'], 'status': 'error', 'error': str(exc)[:3000]}
    finally:
        if c:
            c.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--existing', action='store_true', help='Validate an already compiled worker')
    parser.add_argument('--replace', action='store_true', help='Validate candidate, atomically install, restart only GPU workers')
    parser.add_argument('--audit', action='store_true', help='Read running GPU processes and check loaded executable inode')
    parser.add_argument('--only-devices', help='Comma-separated indices to validate without redoing ready devices')
    parser.add_argument('--relay', type=int, help='Use one already authorized SSH endpoint as a scoped deployment relay')
    parser.add_argument('--clone-from', type=int, help='Copy an already validated same-revision binary from an authorized endpoint before full device validation')
    parser.add_argument('--atomic-copy', action='store_true', help='Replace an uploaded executable inode before validating it')
    parser.add_argument('--ports', required=True)
    args = parser.parse_args()
    ports = {int(p) for p in args.ports.split(',')}
    only_devices = {int(p) for p in args.only_devices.split(',')} if args.only_devices else None
    blocked = json.loads((m.ROOT/'disabled-ports.json').read_text()) if (m.ROOT/'disabled-ports.json').exists() else []
    if ports.intersection(blocked): raise SystemExit('Selected ports include a disabled endpoint')
    rows = [r for r in json.loads(unprotect((m.ROOT / 'ssh-secrets.dpapi').read_bytes())) if r['port'] in ports]
    if len(rows)!=len(ports): raise SystemExit('Missing or ambiguous credentials')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, row, args.probe, args.existing, args.replace, args.audit, only_devices, args.relay, args.clone_from, args.atomic_copy) for row in rows]):
            print(json.dumps(future.result()), flush=True)
````
<!-- END FILE -->

### start.py

<!-- FILE: start.py SHA256: 7cc6ab6d67c714b694fa22f8c09e86cd4602ceb99f0672ae16e4fdf333c0e239 -->
````python
from pathlib import Path
import subprocess,sys,os,json,time
root=Path(__file__).resolve().parent
log=open(root/'service.log','ab')
p=subprocess.Popen([sys.executable,str(root/'controller.py')],cwd=root,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
time.sleep(2)
if p.poll() is not None:raise SystemExit('Controller did not start. Check service.log; an instance may already be running.')
print('Started PID',p.pid)
````
<!-- END FILE -->

### test_miner.py

<!-- FILE: test_miner.py SHA256: 6b0f237b0a5e47102c0c46c84c5397c77ceb174de85d50f977cffdad1f2d5d60 -->
````python
import unittest,tempfile,subprocess,secrets,time,threading,json,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
import controller as m

class NativeTests(unittest.TestCase):
 def test_hash_matches_ethereum_keccak(self):
  cases=[(bytes(32),bytes(20),bytes(32)),(bytes([255])*32,bytes([255])*20,bytes([255])*32)]
  cases += [(secrets.token_bytes(32),secrets.token_bytes(20),secrets.token_bytes(32)) for _ in range(24)]
  for seed,addr,nonce in cases:
   got=subprocess.check_output([str(m.ROOT/'CpuMiner.exe'),'hash',seed.hex(),addr.hex(),nonce.hex()],text=True).strip()
   self.assertEqual(got,m.kh(seed+addr+nonce).hex())
 def test_worker_reports_valid_unique_solutions(self):
  p=subprocess.Popen([str(m.ROOT/'CpuMiner.exe'),'2'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  seed=secrets.token_bytes(32);prefix=secrets.token_hex(24);target=(2**256)//4096
  p.stdin.write('JOB '+seed.hex()+' '+m.ADDRESS[2:]+' '+target.to_bytes(32,'big').hex()+' '+prefix+'\n');p.stdin.flush()
  found=[];deadline=time.time()+15
  try:
   while len(found)<4 and time.time()<deadline:
    f=p.stdout.readline().split()
    if f[0]=='FOUND':
     self.assertEqual(m.kh(seed+bytes.fromhex(m.ADDRESS[2:])+bytes.fromhex(f[1])).hex(),f[2]);self.assertLess(int(f[2],16),target);found.append(f[1])
   self.assertEqual(len(found),4);self.assertEqual(len(set(found)),4)
  finally:p.stdin.write('QUIT\n');p.stdin.flush();p.wait(timeout=5);p.stdin.close();p.stdout.close()

class SafetyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.lp=patch.object(m,'LEDGER',Path(self.tmp.name)/'ledger.json');self.lp.start();self.c=m.Controller()
 def tearDown(self):self.lp.stop();self.tmp.cleanup()
 def test_budget_counts_pending_and_reverts(self):
  ledger={'spent':m.BUDGET-50,'transactions':[{'status':'pending','reserved':30}]}
  self.assertTrue(m.reserve_ok(ledger,20));self.assertFalse(m.reserve_ok(ledger,21));self.assertFalse(m.reserve_ok(ledger,m.PER_TX+1))
  self.assertEqual(m.receipt_cost({'gasUsed':'0xa','effectiveGasPrice':'0x2','gasUsedForL1':'0x3','status':'0x0'}),20)
 def test_wrong_wallet_cannot_unlock(self):
  with self.assertRaisesRegex(ValueError,'does not match'):self.c.unlock('01'*32)
  self.assertIsNone(self.c.account);self.assertFalse(self.c.auto)
 def test_invalid_hash_rejected(self):
  st={'seed':'00'*32,'target':2**256-1}
  with self.assertRaisesRegex(ValueError,'mismatch'):m.validate_nonce(st,'00'*32,'11'*32)
  st['target']=1
  with self.assertRaisesRegex(ValueError,'target'):m.validate_nonce(st,'00'*32)
 def test_uncertain_broadcast_keeps_reservation_and_zero_value(self):
  account=m.Account.from_key('01'*32);self.c.account=account;self.c.auto=True
  st={'seed':'00'*32,'target':2**256-1,'depth':1}
  calls=[]
  def fake(method,params):
   calls.append((method,params))
   if method=='eth_call':return '0x'+('00'*32)
   if method=='eth_estimateGas':return hex(250000)
   if method=='eth_gasPrice':return hex(1000000)
   if method=='eth_getTransactionCount':return '0x0'
   if method=='eth_chainId':return hex(m.CHAIN)
   if method=='eth_getBalance':return hex(10**18)
   if method=='eth_sendRawTransaction':raise TimeoutError('unknown')
   raise AssertionError(method)
  with patch.object(m,'ADDRESS',account.address),patch.object(m,'read_state',return_value=st),patch.object(m,'rpc',side_effect=fake):
   nonce='00'*32;digest=m.validate_nonce(st,nonce).hex();self.c.submit(nonce,digest)
   self.assertEqual(len(self.c.ledger['transactions']),1);self.assertEqual(self.c.ledger['transactions'][0]['status'],'pending')
   with self.assertRaises(m.CandidateDeferred):self.c.submit(nonce,digest)
  self.assertEqual(sum(x[0]=='eth_sendRawTransaction' for x in calls),1)
  for method,params in calls:
   if method=='eth_estimateGas':self.assertEqual(params[0]['value'],'0x0');self.assertEqual(params[0]['to'],m.CONTRACT)
  saved=m.load_ledger();self.assertEqual(saved['transactions'][0]['status'],'pending');self.assertNotIn('0101010101010101',json.dumps(saved))
 def test_failed_receipt_spend_is_persisted(self):
  self.c.ledger['transactions']=[{'hash':'0x1234','reserved':100,'status':'pending'}]
  with patch.object(m,'rpc',side_effect=[{'gasUsed':'0xa','effectiveGasPrice':'0x2','l1Fee':'0x3','status':'0x0','blockNumber':'0x10','logs':[]},'0x12']):self.c.check_receipts()
  self.assertEqual(m.load_ledger()['spent'],20);self.assertEqual(self.c.ledger['transactions'][0]['status'],'reverted')
 def test_rpc_failure_never_signs(self):
  self.c.auto=True;self.c.account=m.Account.from_key('01'*32)
  with patch.object(m,'read_state',side_effect=RuntimeError('offline')):
   with self.assertRaises(RuntimeError):self.c.submit('00'*32,'00'*32)
  self.assertEqual(self.c.ledger['transactions'],[])
 def test_rebroadcast_uses_identical_bytes_and_reservation(self):
  self.c.auto=True
  t={'hash':'0x1234','raw':'0xabcdef','nonce':4,'reserved':100,'status':'pending','created':time.time()-20}
  self.c.ledger['transactions']=[t]
  with patch.object(m,'rpc',side_effect=['0x4','0x1234']) as fake:self.c.retry_pending(t.copy())
  self.assertEqual(fake.call_args_list[-1].args,('eth_sendRawTransaction',['0xabcdef']))
  self.assertEqual(self.c.ledger['transactions'][0]['reserved'],100)
  self.assertEqual(len(self.c.ledger['transactions']),1)
 def test_replacement_keeps_nonce_and_accounts_first_mined_variant(self):
  account=m.Account.from_key('01'*32);self.c.account=account;self.c.auto=True
  tx={'chainId':m.CHAIN,'nonce':4,'to':m.CONTRACT,'value':0,'data':m.data('claim(uint256)',123),'gas':300000,'gasPrice':1000000}
  raw=bytes(account.sign_transaction(tx).raw_transaction);h='0x'+m.kh(raw).hex()
  t={'hash':h,'raw':'0x'+raw.hex(),'nonce':4,'reserved':300000000000,'status':'pending','created':time.time()-60,'tx':tx,'variants':[{'hash':h}]}
  self.c.ledger['transactions']=[t]
  with patch.object(m,'rpc',side_effect=['0x4',hex(1500000),'0x','0xsent']):self.c.retry_pending(t.copy())
  item=self.c.ledger['transactions'][0]
  self.assertEqual(item['tx']['nonce'],4);self.assertEqual(item['tx']['value'],0);self.assertEqual(item['tx']['data'],tx['data']);self.assertEqual(len(item['variants']),2)
  self.assertLessEqual(item['reserved'],m.PER_TX)
  # The original can win the race; count it once and clear the whole nonce reservation.
  with patch.object(m,'rpc',side_effect=[{'transactionHash':h,'gasUsed':'0xa','effectiveGasPrice':'0x2','status':'0x1','blockNumber':'0x10','logs':[]},'0x12']):self.c.check_receipts()
  self.assertEqual(self.c.ledger['spent'],20);self.assertEqual(item['mined_hash'],h);self.assertEqual(item['status'],'confirmed')
 def test_consumed_unknown_nonce_pauses_without_releasing_budget(self):
  self.c.auto=True;t={'hash':'0x1234','raw':'0xab','nonce':4,'reserved':100,'status':'pending','created':time.time()-20,'nonce_moved_at':time.time()-70}
  self.c.ledger['transactions']=[t]
  with patch.object(m,'rpc',return_value='0x5'):self.c.retry_pending(t.copy())
  self.assertFalse(self.c.auto);self.assertEqual(t['status'],'pending');self.assertEqual(t['reserved'],100)
 def test_pending_candidate_is_retained_until_contention_clears(self):
  candidate=('00'*32,'11'*32);self.c.solutions.put(candidate)
  seen=[]
  def submit(*args):
   seen.append(args)
   if len(seen)<3:raise m.CandidateDeferred('Pending claim')
   self.c.stop_event.set()
  with patch.object(self.c,'submit',side_effect=submit),patch.object(self.c,'check_receipts') as receipts,patch.object(self.c.stop_event,'wait',return_value=False):self.c.signer_loop()
  self.assertEqual(seen,[candidate]*3);self.assertEqual(receipts.call_count,3)
 def test_rpc_failure_after_five_attempts_does_not_discard_candidate(self):
  candidate=('00'*32,'11'*32);self.c.solutions.put(candidate)
  seen=[]
  def submit(*args):
   seen.append(args)
   if len(seen)<=6:raise RuntimeError('RPC offline')
   self.c.stop_event.set()
  with patch.object(self.c,'submit',side_effect=submit),patch.object(self.c,'check_receipts') as receipts,patch.object(self.c.stop_event,'wait',return_value=False):self.c.signer_loop()
  self.assertEqual(seen,[candidate]*7);self.assertEqual(receipts.call_count,7)
 def test_locked_signer_defers_instead_of_discarding_proof(self):
  with self.assertRaises(m.CandidateDeferred):self.c.submit('00'*32,'11'*32)
  self.assertEqual(self.c.ledger['transactions'],[])
 def test_stale_pending_claim_pauses_without_sending_or_releasing_budget(self):
  self.c.account=m.Account.from_key('01'*32);self.c.auto=True
  tx={'chainId':m.CHAIN,'nonce':4,'to':m.CONTRACT,'value':0,'data':m.data('claim(uint256)',123),'gas':300000,'gasPrice':1000000}
  t={'hash':'0x1234','raw':'0xab','nonce':4,'reserved':300000000000,'status':'pending','created':time.time()-60,'tx':tx}
  self.c.ledger['transactions']=[t]
  with patch.object(m,'rpc',side_effect=['0x4',hex(1500000),m.RpcRejected('stale'),m.RpcRejected('stale')]) as fake:self.c.retry_pending(t.copy())
  self.assertFalse(self.c.auto);self.assertEqual(t['status'],'pending');self.assertEqual(t['reserved'],300000000000)
  self.assertNotIn('eth_sendRawTransaction',[call.args[0] for call in fake.call_args_list])
  self.assertTrue(any('signer paused' in event['message'] for event in self.c.events))
 def test_http_requires_host_token_and_origin(self):
  server=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);server.control=self.c
  port=server.server_address[1];threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with patch.object(m,'PORT',port):
    url='http://127.0.0.1:'+str(port)+'/api/status'
    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url)
    req=urllib.request.Request(url,headers={'X-Panel-Token':self.c.token})
    self.assertEqual(json.load(urllib.request.urlopen(req))['auto'],False)
    req=urllib.request.Request(url,headers={'X-Panel-Token':self.c.token,'Origin':'https://evil.example'})
    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
  finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main(verbosity=2)
````
<!-- END FILE -->

### test_fleet_audit.py

<!-- FILE: test_fleet_audit.py SHA256: 2100f7b9fbaf321e003d64fd5ac02314988b9cbf968046c594a7f63e948c3791 -->
````python
"""Offline regression checks for dynamic fleet lifecycle and budget migration."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import controller as m
import fleet

class LedgerMigrationTests(unittest.TestCase):
    def test_authorized_increase_preserves_spend_pending_and_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.json'
            original={'budget':3_000_000_000_000_000,'spent':2_000_000_000_000_000,
                      'transactions':[{'hash':'0x1234','status':'pending','reserved':100_000_000_000_000,
                                       'nonce':9,'raw':'0xf00d','variants':[{'hash':'0x1234'}]}]}
            path.write_text(json.dumps(original))
            with patch.object(m,'LEDGER',path):
                actual=m.load_ledger()
                expected=dict(original,budget=m.BUDGET)
                self.assertEqual(actual,expected)
                self.assertEqual(json.loads(path.read_text()),expected)
                self.assertEqual(m.load_ledger(),expected)
                # Retained pending reservation still participates in the cap.
                near_cap=dict(actual,spent=m.BUDGET-100_000_000_000_005)
                self.assertTrue(m.reserve_ok(near_cap,5))
                self.assertFalse(m.reserve_ok(near_cap,6))

    def test_unrecognized_budget_is_rejected_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.json'
            original=json.dumps({'budget':1_000_000_000_000_000,'spent':7,'transactions':[]})
            path.write_text(original)
            with patch.object(m,'LEDGER',path):
                with self.assertRaisesRegex(RuntimeError,'budget mismatch'):m.load_ledger()
            self.assertEqual(path.read_text(),original)

RealEvent=threading.Event
class FastEvent(RealEvent):
    def wait(self,timeout=None):
        return super().wait(None if timeout is None else min(timeout,0.01))

class FakeChannel:
    def __init__(self):self.buffer=b'';self.closed=False;self.broken=False
    def settimeout(self,value):pass
    def recv_stderr_ready(self):return False
    def recv_ready(self):return bool(self.buffer)
    def recv(self,size):value=self.buffer[:size];self.buffer=self.buffer[size:];return value
    def exit_status_ready(self):return self.broken

class FakeOutput:
    def __init__(self,channel=None,value=b''):self.channel=channel;self.value=value
    def read(self):return self.value

class FakeInput:
    def __init__(self,client):self.client=client
    def write(self,line):
        self.client.commands.append(line.strip())
        if line.startswith('JOB '):self.client.channel.buffer+=b'RATE 123456 123456\n'
    def flush(self):pass

class FakeClient:
    def __init__(self):self.channel=FakeChannel();self.commands=[];self.closed=False
    def get_transport(self):return self
    def set_keepalive(self,value):pass
    def exec_command(self,command,timeout):
        if command=='nproc':return None,FakeOutput(value=b'2\n'),None
        self.command=command
        return FakeInput(self),FakeOutput(self.channel),FakeOutput()
    def close(self):self.closed=True;self.channel.closed=True

class FleetLifecycleTests(unittest.TestCase):
    def test_specs_exclude_disabled_endpoint_and_invalid_gpu_ids(self):
        rows=[{'host':'test','port':1},{'host':'test','port':2379}]
        ready={'test:1':{'devices':[0,1,1,-1,64,True,'2']},'test:2379':{'devices':[0]}}
        self.assertEqual(set(fleet.build_specs(rows,[2379],ready)),{'test:1 / CPU','test:1 / GPU 0','test:1 / GPU 1'})

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.row={'host':'test','port':2327,'password':'test-only'}
        (self.root/'ssh-secrets.dpapi').write_bytes(json.dumps([self.row]).encode())
        (self.root/'gpu-ready.json').write_text('{}')
        self.clients=[]
        with patch.object(m,'LEDGER',self.root/'ledger.json'):self.control=m.Controller()
        self.control.stop_event=FastEvent();self.control.mining=True
        self.control.state={'read_at':time.time(),'depth':1,'seed':'00'*32,'work_target':2**255}
        self.patches=[patch.object(fleet,'__file__',str(self.root/'fleet.py')),
                      patch.object(fleet,'unprotect',side_effect=lambda value:value),
                      patch.object(fleet,'connect',side_effect=self.connect),
                      patch.object(fleet.threading,'Event',FastEvent)]
        for item in self.patches:item.start()
        self.supervisor=fleet.start_fleet(self.control)

    def connect(self,row):
        client=FakeClient();self.clients.append(client);return client

    def wait_for(self,predicate):
        deadline=time.time()+3
        while time.time()<deadline:
            if predicate():return
            time.sleep(0.01)
        self.fail('Lifecycle condition timed out; events='+str(self.control.events))

    def tearDown(self):
        self.control.stop_event.set();self.supervisor.join(timeout=2)
        deadline=time.time()+2
        while self.control.remote and time.time()<deadline:time.sleep(0.01)
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def test_gpu_added_dynamically_then_both_workers_cancelled(self):
        self.wait_for(lambda:self.control.remote.get('test:2327 / CPU',{}).get('rate',0)>0)
        m.write_json(self.root/'gpu-ready.json',{'test:2327':{'devices':[0]}})
        self.wait_for(lambda:self.control.remote.get('test:2327 / GPU 0',{}).get('rate',0)>0)
        m.write_json(self.root/'disabled-ports.json',[2327])
        self.wait_for(lambda:not self.control.remote and all(c.closed for c in self.clients))
        self.assertEqual(len(self.clients),2)
        self.assertTrue(all('QUIT' in c.commands for c in self.clients))

    def test_disconnected_worker_reconnects_with_new_nonce_prefix(self):
        self.wait_for(lambda:self.control.remote.get('test:2327 / CPU',{}).get('rate',0)>0)
        first=self.clients[0]
        prefix=next(line for line in first.commands if line.startswith('JOB ')).split()[-1]
        first.channel.broken=True
        self.wait_for(lambda:len(self.clients)>=2 and any(line.startswith('JOB ') for line in self.clients[1].commands))
        self.assertTrue(first.closed)
        new_prefix=next(line for line in self.clients[1].commands if line.startswith('JOB ')).split()[-1]
        self.assertNotEqual(prefix,new_prefix)

if __name__=='__main__':unittest.main(verbosity=2)
````
<!-- END FILE -->

### test_local_cpu.py

<!-- FILE: test_local_cpu.py SHA256: 74bd792f7e6c384a98b4d42dcb272e0d9db23595d4dee9f46516ce6ade8e5072 -->
````python
"""Offline checks that local CPU controls cannot stop the remote signer/fleet."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import controller as m

class FakeProcess:
    def __init__(self,lines=(),exitcode=None):
        self.stdin=io.StringIO();self.stdout=iter(lines);self.exitcode=exitcode
    def poll(self):return self.exitcode

class LocalCpuTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(m,'LEDGER',self.root/'ledger.json'),patch.object(m,'LOCAL_SETTINGS',self.root/'local-settings.json')]
        for p in self.patches:p.start()
        self.c=m.Controller()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def arm(self):
        self.c.account=object();self.c.auto=True;self.c.mining=True
        self.c.proc=FakeProcess();self.c.rate=987654
        self.c.remote={'remote / GPU 0':{'rate':1e9,'updated':time.time()}}

    def test_missing_setting_defaults_enabled_and_disabled_state_persists(self):
        self.assertTrue(self.c.local_cpu_enabled)
        self.c.set_local_cpu_enabled(False)
        self.assertEqual(json.loads(m.LOCAL_SETTINGS.read_text()),{'local_cpu_enabled':False})
        other=m.Controller()
        self.assertFalse(other.local_cpu_enabled)
        self.assertFalse(other.status()['local_cpu_enabled'])

    def test_toggle_preserves_account_global_flags_and_live_worker(self):
        self.arm();account=self.c.account;proc=self.c.proc
        self.c.last_work=('old','job')
        with patch.object(m.subprocess,'Popen') as spawn:
            self.c.set_local_cpu_enabled(False)
            self.assertEqual(proc.stdin.getvalue(),'STOP\n')
            status=self.c.status()
            self.assertEqual(status['threads'],0);self.assertEqual(status['local_rate'],0)
            self.assertEqual(status['rate'],1e9);self.assertFalse(status['local_mining'])
            self.assertIs(self.c.account,account);self.assertTrue(self.c.auto);self.assertTrue(self.c.mining)
            self.assertIs(self.c.proc,proc);self.assertIsNone(proc.poll());self.assertIsNone(self.c.last_work)
            self.c.set_local_cpu_enabled(True)
            spawn.assert_not_called()
        self.assertIs(self.c.account,account);self.assertTrue(self.c.auto);self.assertTrue(self.c.mining)
        self.assertEqual(proc.stdin.getvalue(),'STOP\n')

    def test_disabled_start_does_not_launch_local_process(self):
        self.c.set_local_cpu_enabled(False)
        with patch.object(m.subprocess,'Popen') as spawn:self.c.start()
        spawn.assert_not_called();self.assertIsNone(self.c.proc);self.assertTrue(self.c.mining)

    def test_enable_while_globally_stopped_does_not_launch_process(self):
        self.c.set_local_cpu_enabled(False)
        with patch.object(m.subprocess,'Popen') as spawn:self.c.set_local_cpu_enabled(True)
        spawn.assert_not_called();self.assertFalse(self.c.mining);self.assertIsNone(self.c.proc)

    def test_enable_while_mining_launches_worker_without_changing_signer(self):
        self.arm();account=self.c.account
        self.c.set_local_cpu_enabled(False);self.c.proc=None
        proc=FakeProcess();old_prefix=self.c.prefix
        with patch.object(m.subprocess,'Popen',return_value=proc) as spawn,patch.object(m.threading,'Thread'):
            self.c.set_local_cpu_enabled(True)
        spawn.assert_called_once();self.assertIs(self.c.proc,proc)
        self.assertNotEqual(self.c.prefix,old_prefix)
        self.assertIs(self.c.account,account);self.assertTrue(self.c.auto)

    def test_target_refresh_cannot_restart_disabled_local_cpu(self):
        self.arm();self.c.set_local_cpu_enabled(False)
        states=[{'seed':'00'*32,'target':2**250,'depth':depth,'read_at':time.time()} for depth in (100,101)]
        with patch.object(m,'rpc',return_value=hex(m.CHAIN)),patch.object(m,'read_state',side_effect=states),patch.object(m,'call',return_value=hex(2**249)),patch.object(self.c.stop_event,'is_set',side_effect=[False,False,True]),patch.object(self.c.stop_event,'wait',return_value=False),patch.object(self.c,'ensure_local_worker') as ensure:
            self.c.update_work()
        ensure.assert_not_called();self.assertEqual(self.c.proc.stdin.getvalue(),'STOP\n')
        self.assertEqual(self.c.state['depth'],101);self.assertTrue(self.c.auto);self.assertTrue(self.c.mining)

    def test_global_stop_and_restart_preserve_disabled_preference(self):
        self.arm();account=self.c.account;self.c.set_local_cpu_enabled(False)
        self.c.stop()
        self.assertFalse(self.c.auto);self.assertFalse(self.c.mining);self.assertIs(self.c.account,account)
        with patch.object(m.subprocess,'Popen') as spawn:self.c.start()
        spawn.assert_not_called();self.assertTrue(self.c.mining);self.assertFalse(self.c.auto)
        self.assertFalse(self.c.local_cpu_enabled);self.assertEqual(self.c.status()['threads'],0)

    def test_unexpected_local_exit_keeps_remote_mining_and_signer_active(self):
        self.arm();account=self.c.account
        proc=FakeProcess(['RATE 1234 5678\n'],0);self.c.proc=proc
        self.c.worker_output(proc)
        self.assertTrue(self.c.auto);self.assertTrue(self.c.mining);self.assertIs(self.c.account,account)
        self.assertEqual(self.c.rate,0);self.assertIsNone(self.c.last_work)
        self.assertIn('remote / GPU 0',self.c.remote)

    def test_old_worker_exit_does_not_clear_new_worker_rate(self):
        self.arm();self.c.rate=4321
        self.c.worker_output(FakeProcess(['RATE 9999 1\n'],0))
        self.assertEqual(self.c.rate,4321);self.assertTrue(self.c.auto)

    def test_boolean_is_strict_and_invalid_value_does_not_change_setting(self):
        for value in (None,0,1,'false',[],{}):
            with self.assertRaises(ValueError):self.c.set_local_cpu_enabled(value)
        self.assertTrue(self.c.local_cpu_enabled);self.assertFalse(m.LOCAL_SETTINGS.exists())

    def test_authenticated_endpoint_toggles_without_unlocking_or_stopping_fleet(self):
        self.arm();account=self.c.account
        server=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);server.control=self.c
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.server_address[1]
        try:
            with patch.object(m,'PORT',port):
                url='http://127.0.0.1:'+str(port)+'/api/local-cpu'
                body=json.dumps({'enabled':False}).encode()
                req=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as denial:urllib.request.urlopen(req)
                self.assertEqual(denial.exception.code,403);self.assertTrue(self.c.local_cpu_enabled)
                req=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json','X-Panel-Token':self.c.token})
                self.assertEqual(json.load(urllib.request.urlopen(req)),{'ok':True})
                self.assertFalse(self.c.local_cpu_enabled);self.assertTrue(self.c.auto)
                self.assertTrue(self.c.mining);self.assertIs(self.c.account,account)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main(verbosity=2)
````
<!-- END FILE -->
