# Can the IC-705 do DMR?

## Verdict (2026-11-21)

**Technically yes, as a firmware project. It is not a settings change.** There is
no hidden DMR support: the radio's vocoder knows only D-STAR AMBE [FW]. Everything
else DMR needs maps onto blocks that already exist for D-STAR:

- a 48 kHz raw FM discriminator at 10 samples/symbol into the DV DSP [FW]
- a table-driven FM modulator out of the DV DSP into a 48 kHz ring [FW]
- a host CPU with spare MIPS and an existing app loader (ICAP) [FW]

Two things are not known yet:

- **RF deviation reach (R3).** Needs a measurement on a real radio.
- **Receive channel width in DV mode (R4).** An outside test report suggests it is
  wide enough [EXT].

Biggest effort: **a software AMBE+2 codec**. Biggest legal question: **AMBE+2
licensing/patents** (the owner's call).

Practical scope: **simplex / hotspot DMR, one timeslot, continuous carrier.**
True TDMA bursting on a repeater uplink depends on T/R switching speed and is the
least certain part.

## What DMR needs (ETSI TS 102 361) vs D-STAR

| | D-STAR (present) | DMR (needed) |
|---|---|---|
| Modulation | GMSK, 4800 bit/s, h = 0.5, BT 0.5 | 4FSK, 4800 sym/s, ±648 / ±1944 Hz, RRC α = 0.2 |
| Peak deviation | ±1.2 kHz | ±1.944 kHz (+ RRC overshoot) |
| Highest baseband freq | ≈ 2.4 kHz | 2.88 kHz |
| Channel | 6.25 kHz | 12.5 kHz (Carson ≈ 9.6 kHz, 99 % ≈ 7.6 kHz) |
| Vocoder | AMBE 3600x2400 | **AMBE+2 3600x2450** |
| Access | continuous | 2-slot TDMA, 30 ms slots (27.5 ms bursts) |
| Framing | header + 9-byte voice + 3-byte slow data | 264-bit bursts, BPTC/Golay/QR FEC, CACH, embedded LC |

## Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| R1 | AMBE+2 encode/decode | **Not in hardware.** Needs a software codec. | [FW] |
| R2 | 4FSK TX modulator | Feasible: replace the GMSK pulse table and bit→level map in `gmsk_mod_bit` with RRC taps and a 2-bit→4-level map. The output ring and I2S path stay as they are. | structure [FW], change [INF] |
| R3 | TX deviation ±1.944 kHz | **Open.** At the D-STAR scale the peak sample is 0.80 FS for ±1.2 kHz, so ±1.944 kHz would need about 1.3 FS unless the next stage's gain is set per mode. | [FW] scale, [INF] mapping |
| R4 | RX discriminator, ≥ ±4.8 kHz channel | Rate is OK: the discriminator arrives at 48 kHz, 10 samples/symbol. Width is likely OK: the FPGA→DSP baseband is reported as 12 kHz wide. | [FW] rate, [EXT] width |
| R5 | TDMA timing | Simplex/hotspot: continuous TX with one used slot. Repeater uplink: needs about 30 ms T/R keying. | [INF] |
| R6 | DMR framing, FEC, LC | Software only (ARM ICAP app or DV DSP) | [INF] |
| R7 | Patch delivery | ARM: existing `repack`. DV DSP: lives in the DSP MD5 domain with its own autokey; format is documented, re-seal not implemented. | [FW]/[EXT] |
| R8 | ARM↔DV DSP bandwidth | Candidate link SCIF5, clock-sync, about 10 kbit/s. Enough for vocoder frames (2450–3600 bit/s), too tight for raw dibits plus overhead. | [VM] config, [INF] rate |

## R1 — vocoder tables

Method: `tools/table_scan.py` fits every run of 8 consecutive table values against
every position of every image, read as s16 BE/LE, s32 LE and f32 LE (any scale, row-
or column-major). It uses mbelib's reference tables for both codecs. Smooth tables
(W0, L, Dg) match any ramp and are ignored. The random-looking codebooks (PRBA24,
PRBA58, HOCb5–8) are the discriminating test.

- **Positive control:** the D-STAR AMBE 3600x2400 codebooks are in the DV DSP image,
  exact in Q11 (scale 2048, residual ≈ 0): PRBA24 at `dsp.image.bin+0x1A256`,
  PRBA58 `+0x1AE56`, HOCb5–8 `+0x1B256…+0x1B456`. [FW]
- **AMBE+2 3600x2450 codebooks: zero hits** in the DSP group, the ARM main image and
  the sub-CPU image. [FW]
- Caveat: the `0x80000` blob (~830 KiB, entropy 7.1) is neither plain LZSS nor the
  container's autokey, so it could hide tables. It sits outside the DV DSP boot table
  and is most likely main-DSP code [INF], so this is a small residual risk.

Options for the codec:

- **AMBE+2 on the ARM.** The Cortex-A9 has plenty of headroom. Open code exists:
  mbelib (decode), OP25 (half-rate encoder) [EXT]. Audio would reach the ARM through
  the same path the ICAP decoders use.
- **AMBE+2 inside the DV DSP**, next to the existing MBE code. The MBE synthesis and
  analysis exist, so only the quantizers and bit allocation differ. Faster, but it
  means writing C55x code.

## Proposed architecture (if pursued)

```
mic → [main DSP] → I2S0 R → DV DSP 48→8 kHz → (to ARM) → AMBE+2 enc (ARM)
ARM: DMR framing, FEC, LC, voice superframes → payload over host link →
DV DSP: 4FSK symbol mapper + RRC pulse FIR → tx ring → I2S0 R → [main DSP/FPGA] → RF

RF → [FPGA/main DSP] → discriminator → I2S0 L (48 kHz) → DV DSP: RRC matched filter,
4-level slicer, sync search → payload to ARM → DMR deframe → AMBE+2 dec (ARM) → audio
```

## Next experiments, cheapest first

1. **[HW] Deviation scale (R3):** key DV into a dummy load, measure deviation with an
   SDR or service monitor. That gives Hz per LSB of the I2S0 R sample. Then check
   whether FM-N/FM drives the same path with more gain.
2. **[HW] RX bandwidth (R4):** in DV mode, feed a signal generator at ±3/±4/±5 kHz FM
   deviation and log the I2S0 L amplitude. Needs a DSP-side tap (debug patch) or a
   scope on the I2S lines.
3. **[FW] Host protocol:** decode the SCIF5↔DV DSP frame format (ARM driver + DSP
   `0x65FC` struct). Needed to carry DMR payload.
4. **[VM] C55x micro-emulator** for `gmsk_mod_bit`/`fir`, to prove a 4FSK table swap
   produces the right waveform before touching a radio.
5. **[FW] DSP-group re-seal** in `repack` (separate autokey domain + MD5).

## Fallback: flat data path

Expose the discriminator and modulator (I2S0 L/R taps) to USB audio. DMR then runs
entirely in an external modem (MMDVM/DSD-FME/M17 software). This needs R3/R4, but
not R1/R2/R6 on the radio. It is the same request as sq6emm/icom-custom-firmware#6.
Icom showed a comparable flat 9600 bps USB path for the IC-9700 as a prototype
(JA1OGZ, 2026-08-29) [EXT].
