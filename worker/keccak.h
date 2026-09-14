/*
 * keccak.h -- Ethereum Keccak-256, single-rate-block specialization.
 *
 * Shared by the CUDA worker (compiled by nvcc as __device__) and by the
 * plain-C++ host build (compiled by g++ for self-test and for machines
 * without a GPU). One implementation, two toolchains: the GPU path and the
 * reference path cannot drift apart because they are the same code.
 *
 * MinerPotatos preimages are exactly 116 bytes, so a whole proof fits in one
 * 136-byte Keccak rate block. Domain padding is 0x01 at byte 116 and 0x80 at
 * byte 135. Nothing here needs multi-block support, and adding it would only
 * invite a divergence between the two builds.
 */
#ifndef MP_KECCAK_H
#define MP_KECCAK_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#if defined(__CUDACC__)
#define MP_DEV __device__
#define MP_HOSTDEV __host__ __device__
#else
#define MP_DEV
#define MP_HOSTDEV
#endif

#define MP_RATE_BYTES 136   /* 1088-bit rate for 256-bit output */
#define MP_PREIMAGE_LEN 116 /* address(20) | prevWork(32) | anchor(32) | nonce(32) */
#define MP_LANES 25
/* 136 / 8 = 17 lanes exactly. Getting this wrong silently drops the trailing
 * padding lane (which carries the 0x80 at byte 135) and yields a hash that is
 * stable, fast and completely wrong. */
#define MP_STATE_LANES 17

MP_HOSTDEV static const uint64_t mp_rc[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL};

/* Rotation offsets, rho[x][y]. Indexed [x][y], matching the Keccak team's
 * reference pseudocode. Note the standard table is often printed transposed;
 * transposing it here yields a valid-looking but incorrect Keccak. */
MP_HOSTDEV static const unsigned mp_rot[5][5] = {
    {0, 36, 3, 41, 18},
    {1, 44, 10, 45, 2},
    {62, 6, 43, 15, 61},
    {28, 55, 25, 21, 56},
    {27, 20, 39, 8, 14}};

MP_HOSTDEV static inline uint64_t mp_rotl(uint64_t x, unsigned n) {
  n &= 63; /* rho offset 0 exists at lane (0,0); x >> 64 would be UB */
  return n ? ((x << n) | (x >> (64 - n))) : x;
}

/* In-place Keccak-f[1600]. */
MP_HOSTDEV static inline void mp_keccakf(uint64_t a[MP_LANES]) {
  for (int round = 0; round < 24; ++round) {
    uint64_t c[5], d[5], b[MP_LANES];
#pragma unroll
    for (int x = 0; x < 5; ++x)
      c[x] = a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20];
#pragma unroll
    for (int x = 0; x < 5; ++x)
      d[x] = c[(x + 4) % 5] ^ mp_rotl(c[(x + 1) % 5], 1);
/* rho + pi. Lane (x,y) lives at index x + 5*y, and pi sends it to
   * index y + 5*((2x+3y) mod 5) -- that is B[index(y, 2x+3y)] in the reference
   * implementation. Swapping the two index arguments here compiles cleanly and
   * produces a stable, fast, completely wrong hash. */
#pragma unroll
    for (int x = 0; x < 5; ++x)
#pragma unroll
      for (int y = 0; y < 5; ++y)
        b[y + 5 * ((2 * x + 3 * y) % 5)] =
            mp_rotl(a[y * 5 + x] ^ d[x], mp_rot[x][y]);
#pragma unroll
    for (int x = 0; x < 5; ++x)
#pragma unroll
      for (int y = 0; y < 5; ++y)
        a[y * 5 + x] = b[y * 5 + x] ^ ((~b[y * 5 + ((x + 1) % 5)]) &
                                       b[y * 5 + ((x + 2) % 5)]);
    a[0] ^= mp_rc[round];
  }
}

/*
 * Absorb a single rate block: XOR `lanes` (little-endian 64-bit words over the
 * 136-byte block, already containing padding) into a zero state, permute.
 * Only the first 16 lanes can be non-zero for a 136-byte rate.
 */
MP_HOSTDEV static inline void mp_keccak_block(const uint64_t lanes[MP_STATE_LANES],
                                              uint8_t out[32]) {
  uint64_t a[MP_LANES];
#pragma unroll
  for (int i = 0; i < MP_LANES; ++i) a[i] = 0;
#pragma unroll
  for (int i = 0; i < MP_STATE_LANES; ++i) a[i] = lanes[i];
  mp_keccakf(a);
  /* Digest is the first 32 bytes of the state, little-endian lanes. */
  for (int i = 0; i < 4; ++i) {
    uint64_t v = a[i];
    for (int b = 0; b < 8; ++b) out[i * 8 + b] = (uint8_t)(v >> (8 * b));
  }
}

/*
 * Build the 16 absorbed lanes for a 116-byte preimage.
 *
 * The layout matters and is the single most likely place for a byte-order bug:
 *   preimage[0:20]   miner address, packed, NOT ABI-padded
 *   preimage[20:52]  prevWork
 *   preimage[52:84]  anchor (blockhash of anchorBlock)
 *   preimage[84:116] nonce = prefix(24) || counter(8, big-endian)
 * Bytes 116..135 are padding: 0x01 at 116, zeros, 0x80 at 135.
 */
MP_HOSTDEV static inline void mp_absorb_preimage(const uint8_t pre[MP_PREIMAGE_LEN],
                                                 uint64_t lanes[MP_STATE_LANES]) {
  uint8_t blk[MP_RATE_BYTES];
  memset(blk, 0, sizeof(blk));
  memcpy(blk, pre, MP_PREIMAGE_LEN);
  blk[MP_PREIMAGE_LEN] = 0x01;      /* domain separator at byte 116 */
  blk[MP_RATE_BYTES - 1] = 0x80;    /* final pad bit at byte 135 */
  for (int i = 0; i < MP_STATE_LANES; ++i) {
    uint64_t v = 0;
    for (int b = 7; b >= 0; --b) v = (v << 8) | blk[i * 8 + b];
    lanes[i] = v;
  }
}

/* Convenience: full 116-byte preimage in, 32-byte digest out. */
MP_HOSTDEV static inline void mp_keccak116(const uint8_t pre[MP_PREIMAGE_LEN],
                                           uint8_t out[32]) {
  uint64_t lanes[MP_STATE_LANES];
  mp_absorb_preimage(pre, lanes);
  mp_keccak_block(lanes, out);
}

/* Unsigned big-endian 32-byte compare. Returns true when a <= b. */
MP_HOSTDEV static inline bool mp_le256(const uint8_t a[32], const uint8_t b[32]) {
  for (int i = 0; i < 32; ++i) {
    if (a[i] != b[i]) return a[i] < b[i];
  }
  return true; /* equal counts as a solve: the site accepts digest <= target */
}

/* Count leading zero bits of a 32-byte big-endian value (for reporting). */
MP_HOSTDEV static inline int mp_zero_bits(const uint8_t d[32]) {
  int n = 0;
  for (int i = 0; i < 32; ++i) {
    if (d[i] == 0) { n += 8; continue; }
    uint8_t v = d[i];
    while ((v & 0x80) == 0) { v <<= 1; ++n; }
    break;
  }
  return n;
}

#endif /* MP_KECCAK_H */
