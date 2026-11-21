# Can the IC-705 do DMR?

Working answer: **open**. This file tracks each requirement, what is known, and
the cheapest experiment that settles it.

## What DMR needs (ETSI TS 102 361) vs what D-STAR uses

| | D-STAR (present) | DMR (needed) |
|---|---|---|
| Modulation | GMSK, 4800 bit/s, h = 0.5, BT 0.5 | 4FSK, 4800 sym/s, ±648 / ±1944 Hz, RRC α = 0.2 |
| Peak deviation | ±1.2 kHz | ±1.944 kHz (+ RRC overshoot) |
| Highest baseband freq | ≈ 2.4 kHz | 2.88 kHz |
| Channel | 6.25 kHz | 12.5 kHz (Carson ≈ 9.6 kHz, 99 % ≈ 7.6 kHz) |
| Vocoder | AMBE 3600x2400 (D-STAR) | **AMBE+2 3600x2450** |
| Access | continuous | 2-slot TDMA, 30 ms slots (27.5 ms bursts) |
| Framing | header + 9-byte voice + 3-byte slow data | 264-bit bursts, BPTC/Golay/QR FEC, CACH, embedded LC |

## Requirements

| # | Requirement | Status |
|---|---|---|
| R1 | AMBE+2 encode/decode somewhere in the radio | **not in hardware vocoder** (see below); software AMBE+2 needed |
| R2 | 4FSK TX modulator | **likely OK**: GMSK modulator in DV DSP is a table-driven FIR into a 48 kHz ring → swap table + symbol mapping [FW structure, INF feasibility] |
| R3 | TX deviation reach ±1.944 kHz | open — current DV scale may top out near ±1.5 kHz [INF]; needs FPGA/main-DSP gain path |
| R4 | RX: flat discriminator, ≥ ±4.8 kHz channel | open — FPGA→DSP baseband reported 12 kHz wide [EXT]; DV-mode filter width unknown |
| R5 | TDMA timing (MS bursts) | open — for simplex/hotspot use, continuous TX with one used slot may be acceptable [INF] |
| R6 | DMR framing/FEC/LC | pure software; ARM (ICAP app) or DV DSP [INF] |
| R7 | Patch delivery | ARM: existing `repack`. DSP group: separate MD5 domain, not yet re-sealed [FW] |

## R1 — vocoder tables (2026-11-21)

Method: `tools/table_scan.py` fits every run of 8 consecutive table values against
every position of every image read as s16 BE/LE, s32 LE and f32 LE (any scale, row-
or column-major), against mbelib's reference tables for both codecs. Smooth tables
(W0, L, Dg) match any ramp and are ignored; the random-looking codebooks (PRBA24,
PRBA58, HOCb5–8) are the discriminating test.

- **Positive control:** the D-STAR AMBE 3600x2400 codebooks are in the DV DSP image,
  exact in Q11 (scale 2048, residual ≈ 0): PRBA24 at `dsp.image.bin+0x1A256`,
  PRBA58 `+0x1AE56`, HOCb5–8 `+0x1B256…+0x1B456`. [FW]
- **AMBE+2 3600x2450 codebooks: zero hits** in the DSP group, the ARM main image and
  the sub-CPU image. [FW]
- Caveat: the `0x80000` blob (~830 KiB, entropy 7.1) is neither plain LZSS nor the
  container's autokey; it could hide tables. It sits outside the DV DSP boot table
  and is most likely main-DSP code [INF], so this is a small residual risk.

Conclusion: the IC-705's vocoder is a D-STAR-only AMBE implementation. **DMR voice
needs a software AMBE+2 encoder/decoder** — on the ARM (Cortex-A9, ample MIPS), or
ported into the DV DSP next to the existing MBE code. Open implementations exist
(mbelib decode; OP25 has a half-rate AMBE+2 encoder) [EXT]; patent/licensing status
is the user's call.

## Fallbacks if R1 fails

- Software AMBE+2 on the ARM (Cortex-A9 has the cycles; encoder quality/licensing
  are the issue).
- A "flat data" path to USB audio (sq6emm/icom-custom-firmware#6) and run DMR in an
  external modem (MMDVM/DSD) — needs R3/R4 anyway.
