/*
 * gpu-miner.cu -- MinerPotatos CUDA worker.
 *
 * Speaks the mp_proto line protocol over stdin/stdout (SSH channel) or over a
 * TCP socket (Vast.ai direct port). It computes nothing but hashes: it is
 * handed a public miner address, a challenge and a nonce prefix, and returns
 * candidate nonces. It never sees a private key, and every candidate it
 * returns is recomputed by the controller before it may reach a transaction.
 *
 * Build for the architecture the rented card reports (nvidia-smi):
 *   nvcc -O3 -std=c++17 -arch=sm_89  -Xcompiler -pthread gpu-miner.cu -o miner
 *   nvcc -O3 -std=c++17 -arch=sm_90  -Xcompiler -pthread gpu-miner.cu -o miner
 *   nvcc -O3 -std=c++17 -arch=sm_120 -Xcompiler -pthread gpu-miner.cu -o miner
 *
 * Host-mode build, no CUDA required. The test suite uses this to prove the
 * hashing core and the line protocol against the Python reference on machines
 * that have no GPU at all:
 *   g++ -O2 -std=c++17 -x c++ gpu-miner.cu -o miner-host
 */
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "keccak.h"
#include "mp_proto.h"

#ifdef __CUDACC__
#include <cuda_runtime.h>
#define MP_BACKEND "cuda"
#else
#define MP_BACKEND "host"
#endif

/* ------------------------------------------------------------------ output */
static FILE *g_out = NULL;

/*
 * Emits exactly one protocol line, always newline-terminated and flushed.
 *
 * The newline is appended here rather than in each format string on purpose:
 * a missing '\n' does not look like an error locally, but it silently merges
 * every message into one stream and blocks any line-oriented reader (the
 * controller, or a test harness) until EOF.
 */
static void mp_emit(const char *fmt, ...) {
  char buf[1024];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if ((size_t)n > sizeof(buf) - 2) n = (int)sizeof(buf) - 2;
  buf[n++] = '\n';
  buf[n] = '\0';
  fputs(buf, g_out);
  fflush(g_out); /* the controller reads line by line; never sit on a proof */
}

static double mp_now(void) {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return (double)tv.tv_sec + (double)tv.tv_usec / 1e6;
}

/* ----------------------------------------------------------------- vectors */
/*
 * Compiled-in 116-byte Keccak vectors. Generated from the Python reference
 * (pycryptodome) by tests/test_keccak_vectors.py and cross-checked against the
 * contract's own pure workFor() view before being written here, so these are
 * not self-consistent -- they are pinned to two independent authorities.
 */
static const struct {
  const char *pre_hex; /* 232 hex chars */
  const char *digest;  /* 64 hex chars */
} g_vectors[] = {
#include "vectors.inc"
};
static const int g_vector_count =
    (int)(sizeof(g_vectors) / sizeof(g_vectors[0]));

/* Returns number of failed vectors; -1 when vectors.inc is unreadable. */
static int mp_selftest_builtin(void) {
  int failed = 0;
  for (int i = 0; i < g_vector_count; ++i) {
    uint8_t pre[116], digest[32];
    if (!mp_unhex(g_vectors[i].pre_hex, 232, pre)) { failed++; continue; }
    mp_keccak116(pre, digest);
    char got[65];
    mp_hex(digest, 32, got);
    if (strncmp(got, g_vectors[i].digest, 64) != 0) {
      mp_emit("ERROR selftest vector %d: got %s want %s", i, got,
              g_vectors[i].digest);
      failed++;
    }
  }
  return failed;
}

/*
 * Optional externally supplied vectors, so a new deployment can re-pin the
 * binary against freshly read contract state instead of trusting this file.
 */
static int mp_selftest_file(const char *path) {
  FILE *f = fopen(path, "r");
  if (!f) { mp_emit("ERROR cannot open selftest file %s", path); return -1; }
  char line[4096];
  int checked = 0, failed = 0;
  while (fgets(line, sizeof(line), f)) {
    if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
    char pre_hex[512] = {0}, want[128] = {0};
    if (sscanf(line, "%511s %127s", pre_hex, want) != 2) { failed++; continue; }
    uint8_t pre[116], digest[32];
    if (!mp_unhex(pre_hex, 232, pre)) { failed++; continue; }
    mp_keccak116(pre, digest);
    char got[65];
    mp_hex(digest, 32, got);
    if (strncmp(got, want, 64) != 0) {
      mp_emit("ERROR selftest mismatch at vector %d: got %s", checked, got);
      failed++;
    }
    checked++;
  }
  fclose(f);
  mp_emit("ERROR selftest-file checked %d vectors, %d failed", checked, failed);
  return failed;
}

/* ------------------------------------------------------------------- CUDA */
#ifdef __CUDACC__

/*
 * One thread walks a strided run of 64-candidate batches. Digests are compared
 * with an unsigned big-endian compare; equality counts as a solve, matching the
 * site's own browser worker (digest <= target).
 *
 * A solve is recorded via atomicMin on the digest's leading 64 bits. Losing a
 * duplicate costs nothing: the controller re-verifies and simply asks again.
 */
__global__ void mp_search(const uint8_t *__restrict__ base, /* 108 bytes */
                          const uint8_t *__restrict__ target, uint64_t start,
                          uint32_t per_thread,
                          unsigned long long *__restrict__ claim,
                          uint64_t *__restrict__ best_counter,
                          uint8_t *__restrict__ best_digest,
                          uint64_t *__restrict__ done) {
  const uint64_t stride = (uint64_t)gridDim.x * blockDim.x;

  uint8_t pre[116];
#pragma unroll
  for (int i = 0; i < 108; ++i) pre[i] = base[i];

  for (uint32_t k = 0; k < per_thread; ++k) {
    const uint64_t batch_start = start + (uint64_t)k * stride * 64ULL;
    for (uint32_t j = 0; j < 64; ++j) {
      const uint64_t counter = batch_start + (uint64_t)threadIdx.x * 64ULL +
                               (uint64_t)blockIdx.x * blockDim.x * 64ULL + j;
      for (int i = 0; i < 8; ++i)
        pre[108 + i] = (uint8_t)(counter >> (56 - 8 * i));
      uint8_t d[32];
      mp_keccak116(pre, d);
      if (mp_le256(d, target)) {
        /* Exactly one thread may publish, and it publishes counter and digest
         * together. The earlier version here ranked candidates with
         * atomicMin on the digest's leading word and then wrote counter and
         * digest in separate stores: a slower winner could be overwritten by
         * a delayed loser, pairing one candidate's counter with another's
         * digest. The controller re-verifies and would reject it, so the
         * result was a silently lost proof rather than a bad transaction --
         * but a lost proof is the whole product, so the race goes.
         *
         * "First solve wins" instead of "best solve wins" costs nothing: any
         * nonce under target mints the same NFT, and the digest we publish is
         * always the digest of the counter we publish. */
        if (atomicCAS(claim, 0ULL, 1ULL) == 0ULL) {
          *best_counter = counter;
          for (int i = 0; i < 32; ++i) best_digest[i] = d[i];
          __threadfence(); /* make the pair visible before the flag is read */
        }
      }
    }
  }
  if (blockIdx.x == 0 && threadIdx.x == 0 && done)
    *done = (uint64_t)per_thread * stride * 64ULL;
}

struct mp_device {
  int index = 0;
  std::string name = "cuda";
  int threads = 0;
  uint32_t per_thread = 4;
  uint8_t *d_base = NULL;
  uint8_t *d_target = NULL;
  unsigned long long *d_claim = NULL;
  uint64_t *d_best_counter = NULL;
  uint8_t *d_best_digest = NULL;
  uint64_t *d_done = NULL;
};

static bool mp_device_init(mp_device *dev, int want_threads, int want_batch) {
  if (cudaSetDevice(dev->index) != cudaSuccess) return false;
  cudaDeviceProp p;
  if (cudaGetDeviceProperties(&p, dev->index) != cudaSuccess) return false;

  /* Size the grid to finish one window in roughly 100 ms. 64 hashes per
   * thread-tick, so threads*batch*64 is the window size. */
  int default_threads = (int)((uint64_t)p.multiProcessorCount * 256ULL * 8ULL);
  dev->threads = want_threads > 0 ? want_threads : default_threads;
  if (dev->threads < 1024) dev->threads = 1024;
  dev->per_thread = want_batch > 0 ? (uint32_t)want_batch : 2;

  bool ok = cudaMalloc(&dev->d_base, 108) == cudaSuccess &&
            cudaMalloc(&dev->d_target, 32) == cudaSuccess &&
            cudaMalloc(&dev->d_claim, 8) == cudaSuccess &&
            cudaMalloc(&dev->d_best_counter, 8) == cudaSuccess &&
            cudaMalloc(&dev->d_best_digest, 32) == cudaSuccess &&
            cudaMalloc(&dev->d_done, 8) == cudaSuccess;
  if (!ok) return false;

  char meta[256];
  snprintf(meta, sizeof(meta), "%s sm_%d%d sms=%d", p.name, p.major, p.minor,
           p.multiProcessorCount);
  dev->name = meta;
  return true;
}

/*
 * Runs one bounded search window and returns. Bounding the window is what
 * stops a worker from burning a whole target lifetime on a challenge the chain
 * has already replaced -- on this contract prevWork and the anchor roll every
 * block, so stale work is the normal failure mode, not an edge case.
 */
static uint64_t mp_search_window(mp_device *dev, const mp_job *job,
                                 uint64_t start, bool *found,
                                 uint64_t *found_counter,
                                 uint8_t *found_digest) {
  *found = false;
  cudaMemcpy(dev->d_base, job->pre_head, 108, cudaMemcpyHostToDevice);
  cudaMemcpy(dev->d_target, job->target, 32, cudaMemcpyHostToDevice);
  /* 0 = nobody has published yet; the kernel CAS-es it to 1 exactly once. */
  const unsigned long long kUnclaimed = 0ULL;
  cudaMemcpy(dev->d_claim, &kUnclaimed, 8, cudaMemcpyHostToDevice);

  const int threads_per_block = 256;
  const int blocks = dev->threads / threads_per_block > 0
                         ? dev->threads / threads_per_block
                         : 1;
  mp_search<<<blocks, threads_per_block>>>(
      dev->d_base, dev->d_target, start, dev->per_thread, dev->d_claim,
      dev->d_best_counter, dev->d_best_digest, dev->d_done);
  cudaError_t e = cudaDeviceSynchronize();

  unsigned long long claimed = kUnclaimed;
  uint64_t total = 0;
  cudaMemcpy(&claimed, dev->d_claim, 8, cudaMemcpyDeviceToHost);
  cudaMemcpy(&total, dev->d_done, 8, cudaMemcpyDeviceToHost);
  if (e != cudaSuccess) {
    mp_emit("ERROR kernel: %s", cudaGetErrorString(e));
    return total;
  }
  if (claimed != kUnclaimed) {
    *found = true;
    cudaMemcpy(found_counter, dev->d_best_counter, 8, cudaMemcpyDeviceToHost);
    cudaMemcpy(found_digest, dev->d_best_digest, 32, cudaMemcpyDeviceToHost);
  }
  return total;
}

#else /* ------------------------- host mode: same protocol, CPU keccak core */

struct mp_device {
  int index = 0;
  std::string name = "host-cpu";
  int threads = 1;
  uint32_t per_thread = 1;
};

static bool mp_device_init(mp_device *dev, int want_threads, int want_batch) {
  dev->threads = want_threads > 0 ? want_threads : 1;
  dev->per_thread = want_batch > 0 ? (uint32_t)want_batch : 1;
  return true;
}

static uint64_t mp_search_window(mp_device *dev, const mp_job *job,
                                 uint64_t start, bool *found,
                                 uint64_t *found_counter,
                                 uint8_t *found_digest) {
  (void)dev;
  *found = false;
  const uint64_t window = 262144;
  for (uint64_t c = start; c < start + window; ++c) {
    uint8_t pre[116], d[32];
    mp_preimage(job, c, pre);
    mp_keccak116(pre, d);
    if (mp_le256(d, job->target)) {
      *found = true;
      *found_counter = c;
      memcpy(found_digest, d, 32);
      return c - start + 1;
    }
  }
  return window;
}

#endif

/* --------------------------------------------------------------------- io */
static volatile sig_atomic_t g_quit = 0;
static void mp_on_signal(int) { g_quit = 1; }

/*
 * Non-blocking line reading over a single fd.
 *
 * This deliberately avoids FILE* on the input side. Buffering a pipe behind
 * fgets means a STOP or QUIT sitting in the same buffer as the JOB it is meant
 * to cancel would be consumed before any hashing happens -- the worker would
 * read QUIT and exit without ever searching. Polling the raw fd lets the loop
 * interleave input with bounded search windows.
 *
 * Returns: 1 = a full line was produced, 0 = nothing complete yet,
 *         -1 = peer closed.
 */
static int mp_next_line(int fd, std::string &pending, std::string &out) {
  for (;;) {
    size_t nl = pending.find('\n');
    if (nl != std::string::npos) {
      out.assign(pending, 0, nl);
      pending.erase(0, nl + 1);
      while (!out.empty() && (out.back() == '\r' || out.back() == '\n'))
        out.pop_back();
      return 1;
    }
    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = POLLIN;
    pfd.revents = 0;
    int pr = poll(&pfd, 1, 0); /* never block: search windows come first */
    if (pr <= 0) return 0;
    char buf[1024];
    ssize_t r = read(fd, buf, sizeof(buf));
    if (r <= 0) return -1;
    if (pending.size() + (size_t)r > MP_LINE_MAX) return -1; /* protocol abuse */
    pending.append(buf, (size_t)r);
  }
}

static void usage(const char *argv0) {
  fprintf(stderr,
          "usage: %s [--tcp PORT] [--device N] [--threads N] [--batch N]\n"
          "            [--selftest FILE] [--selftest-only]\n"
          "  default transport is stdin/stdout (SSH channel)\n",
          argv0);
}

int main(int argc, char **argv) {
  g_out = stdout;
  signal(SIGPIPE, SIG_IGN);
  signal(SIGTERM, mp_on_signal);
  signal(SIGINT, mp_on_signal);

  int tcp_port = -1, dev_index = 0, threads = 0, batch = 0;
  const char *selftest_file = NULL;
  bool selftest_only = false;

  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&](const char *what) -> const char * {
      if (i + 1 >= argc) {
        fprintf(stderr, "missing value for %s\n", what);
        exit(2);
      }
      return argv[++i];
    };
    if (a == "--tcp") tcp_port = atoi(next("--tcp"));
    else if (a == "--device") dev_index = atoi(next("--device"));
    else if (a == "--threads") threads = atoi(next("--threads"));
    else if (a == "--batch") batch = atoi(next("--batch"));
    else if (a == "--selftest") selftest_file = next("--selftest");
    else if (a == "--selftest-only") selftest_only = true;
    else if (a == "--help" || a == "-h") { usage(argv[0]); return 2; }
    else { fprintf(stderr, "unknown argument: %s\n", a.c_str()); usage(argv[0]); return 2; }
  }

  /* Correctness gate before this worker is allowed to claim any hashrate. */
  int built_in_failures = mp_selftest_builtin();
  if (built_in_failures != 0) {
    mp_emit("ERROR builtin selftest failed (%d vectors)", built_in_failures);
    mp_emit("BYE");
    return 1;
  }
  if (selftest_file) {
    if (mp_selftest_file(selftest_file) != 0) {
      mp_emit("BYE");
      return 1;
    }
  }
  mp_emit("SELFTEST ok %d vectors", g_vector_count);

  mp_device dev;
  dev.index = dev_index;
  if (!mp_device_init(&dev, threads, batch)) {
    mp_emit("ERROR device init failed (index %d)", dev_index);
    mp_emit("BYE");
    return 1;
  }
  mp_emit("READY %s %s %d %u", MP_BACKEND, dev.name.c_str(), dev.threads,
          dev.per_thread);
  if (selftest_only) { mp_emit("BYE"); return 0; }

  /* ------------------------------------------------------------- transport */
  int listen_fd = -1, conn_fd = -1;
  if (tcp_port > 0) {
    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) { mp_emit("ERROR socket: %s", strerror(errno)); return 1; }
    int one = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = htonl(INADDR_ANY);
    sa.sin_port = htons((uint16_t)tcp_port);
    if (bind(listen_fd, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
      mp_emit("ERROR bind %d: %s", tcp_port, strerror(errno));
      return 1;
    }
    if (listen(listen_fd, 1) < 0) {
      mp_emit("ERROR listen: %s", strerror(errno));
      return 1;
    }
    conn_fd = accept(listen_fd, NULL, NULL);
    if (conn_fd < 0) { mp_emit("ERROR accept: %s", strerror(errno)); return 1; }
    int nodelay = 1;
    setsockopt(conn_fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
  }

  /* ------------------------------------------------------------ main loop */
  mp_job job;
  memset(&job, 0, sizeof(job));
  bool have_job = false;
  uint64_t cursor = 0, total_hashes = 0;
  double window_start = mp_now(), last_rate = mp_now();
  int best_bits = 0;
  const int in_fd = conn_fd >= 0 ? conn_fd : 0;
  std::string pending, line;

  while (!g_quit) {
    int lr = mp_next_line(in_fd, pending, line);
    if (lr < 0) break; /* peer closed / oversized line */
    if (lr > 0) {
      if (line.empty()) continue;
      mp_cmd c = mp_parse(line.c_str(), &job);
      if (c == MP_QUIT) break;
      if (c == MP_STOP) { have_job = false; continue; }
      if (c == MP_BAD) { mp_emit("ERROR malformed line"); continue; }
      /* New job carries a fresh random prefix from the controller, so this
       * worker cannot re-search space an earlier prefix already covered. */
      have_job = true;
      cursor = 0;
      best_bits = 0;
      window_start = mp_now();
      continue; /* drain any further buffered commands before hashing */
    }

    if (!have_job) { usleep(20000); continue; }

    bool found = false;
    uint64_t found_counter = 0;
    uint8_t found_digest[32];
    uint64_t n = mp_search_window(&dev, &job, cursor, &found, &found_counter,
                                  found_digest);
    if (n == 0) n = 1;
    cursor += n;
    total_hashes += n;

    if (found) {
      uint8_t nonce[32];
      mp_nonce_bytes(&job, found_counter, nonce);
      char nh[65], dh[65];
      mp_hex(nonce, 32, nh);
      mp_hex(found_digest, 32, dh);
      mp_emit("FOUND %s %s %llu", nh, dh, (unsigned long long)job.anchor_block);
      best_bits = mp_zero_bits(found_digest);
      /* Keep mining: the controller may need a replacement if this proof
       * loses the race for the current challenge. */
    }

    double now = mp_now();
    if (now - last_rate >= 1.0) {
      double dt = now - window_start;
      mp_emit("RATE %.0f %llu %d", dt > 0 ? (double)total_hashes / dt : 0.0,
              (unsigned long long)total_hashes, best_bits);
      last_rate = now;
    }

    /* Never let the cursor wrap into space this prefix already covered. */
    if (cursor >= 0x00000000FFFFFFFFULL) {
      mp_emit("ERROR counter space exhausted; issue a new prefix");
      have_job = false;
    }
  }

  mp_emit("BYE");
  if (conn_fd >= 0) close(conn_fd);
  if (listen_fd >= 0) close(listen_fd);
  return 0;
}
