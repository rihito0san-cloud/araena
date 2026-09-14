/*
 * mp_proto.h -- line protocol shared by the CUDA worker and the CPU worker.
 *
 * Both workers speak exactly this, over stdin/stdout (SSH channel) or over a
 * TCP socket (Vast.ai direct port). All fields are hex WITHOUT a 0x prefix.
 *
 *   controller -> worker
 *     JOB <address40hex> <prevWork64hex> <anchor64hex> <target64hex>
 *         <prefix48hex> <anchorBlockDec>
 *     STOP
 *     QUIT
 *
 *   worker -> controller
 *     READY <backend> <device> <threads> <batch>
 *     RATE <hashes_per_second> <total_hashes> <best_zero_bits>
 *     FOUND <nonce64hex> <digest64hex> <anchorBlockDec>
 *     ERROR <message>
 *     BYE
 *
 * The worker never receives a private key. It receives the public miner
 * address (which is part of the hash preimage) and nothing else sensitive.
 */
#ifndef MP_PROTO_H
#define MP_PROTO_H

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MP_ADDR_HEX 40
#define MP_BYTES32_HEX 64
#define MP_PREFIX_HEX 48
#define MP_PREFIX_LEN 24
#define MP_LINE_MAX 4096

typedef struct {
  uint8_t miner[20];
  uint8_t prev_work[32];
  uint8_t anchor[32];
  uint8_t target[32];
  uint8_t prefix[MP_PREFIX_LEN];
  uint64_t anchor_block;
  /* preimage[0:108] -- everything except the 8-byte counter. The GPU copies
   * these 108 bytes once per window and patches the counter per candidate. */
  uint8_t pre_head[108];
} mp_job;

static inline int mp_hexval(int c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

/* Strict hex decode: exact length, hex only, no 0x accepted. */
static inline bool mp_unhex(const char *s, size_t hexlen, uint8_t *out) {
  if (strlen(s) != hexlen) return false;
  for (size_t i = 0; i < hexlen; i += 2) {
    int hi = mp_hexval(s[i]), lo = mp_hexval(s[i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i / 2] = (uint8_t)((hi << 4) | lo);
  }
  return true;
}

static inline void mp_hex(const uint8_t *in, size_t n, char *out) {
  static const char d[] = "0123456789abcdef";
  for (size_t i = 0; i < n; ++i) {
    out[i * 2] = d[in[i] >> 4];
    out[i * 2 + 1] = d[in[i] & 15];
  }
  out[n * 2] = 0;
}

typedef enum { MP_NONE, MP_JOB, MP_STOP, MP_QUIT, MP_BAD } mp_cmd;

static inline mp_cmd mp_parse(const char *line, mp_job *job) {
  char cmd[16] = {0};
  if (sscanf(line, "%15s", cmd) != 1) return MP_BAD;
  if (strcmp(cmd, "QUIT") == 0) return MP_QUIT;
  if (strcmp(cmd, "STOP") == 0) return MP_STOP;
  if (strcmp(cmd, "JOB") != 0) return MP_BAD;

  char a[MP_ADDR_HEX + 1], pw[MP_BYTES32_HEX + 1], an[MP_BYTES32_HEX + 1];
  char tg[MP_BYTES32_HEX + 1], pf[MP_PREFIX_HEX + 1];
  unsigned long long ab = 0;
  if (sscanf(line, "%*s %40s %64s %64s %64s %48s %llu", a, pw, an, tg, pf, &ab) != 6)
    return MP_BAD;
  if (!mp_unhex(a, MP_ADDR_HEX, job->miner)) return MP_BAD;
  if (!mp_unhex(pw, MP_BYTES32_HEX, job->prev_work)) return MP_BAD;
  if (!mp_unhex(an, MP_BYTES32_HEX, job->anchor)) return MP_BAD;
  if (!mp_unhex(tg, MP_BYTES32_HEX, job->target)) return MP_BAD;
  if (!mp_unhex(pf, MP_PREFIX_HEX, job->prefix)) return MP_BAD;
  job->anchor_block = (uint64_t)ab;
  /* Freeze preimage[0:108] now: miner || prevWork || anchor || prefix. */
  memcpy(job->pre_head, job->miner, 20);
  memcpy(job->pre_head + 20, job->prev_work, 32);
  memcpy(job->pre_head + 52, job->anchor, 32);
  memcpy(job->pre_head + 84, job->prefix, MP_PREFIX_LEN);
  return MP_JOB;
}

/* Assemble the 116-byte preimage for a given counter. */
static inline void mp_preimage(const mp_job *job, uint64_t counter,
                               uint8_t out[116]) {
  memcpy(out, job->miner, 20);
  memcpy(out + 20, job->prev_work, 32);
  memcpy(out + 52, job->anchor, 32);
  memcpy(out + 84, job->prefix, MP_PREFIX_LEN);
  for (int i = 0; i < 8; ++i) out[108 + i] = (uint8_t)(counter >> (56 - 8 * i));
}

/* The 32-byte nonce that gets submitted to mine(nonce, anchorBlock). */
static inline void mp_nonce_bytes(const mp_job *job, uint64_t counter,
                                  uint8_t out[32]) {
  memcpy(out, job->prefix, MP_PREFIX_LEN);
  for (int i = 0; i < 8; ++i) out[24 + i] = (uint8_t)(counter >> (56 - 8 * i));
}

#endif /* MP_PROTO_H */
