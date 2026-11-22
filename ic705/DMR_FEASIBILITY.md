# Can the IC-705 do DMR?

## Verdict (2026-11-21, updated after PoC)

**Technically yes, as a firmware project. It is not a settings change.** There is
no hidden DMR support: the radio's vocoder knows only D-STAR AMBE [FW]. Everything
else DMR needs maps onto blocks that already exist for D-STAR:

- a 48 kHz discriminator-like stream at 10 samples/symbol into the DV DSP, read per
  sample by the GMSK demodulator [FW] ("raw" is [INF] until its source is traced)
- a table-driven FM modulator out of the DV DSP into a 48 kHz ring [FW]
- a host CPU with spare MIPS and an existing app loader (ICAP) [FW]. Its audio API is
  only 8 kHz, post-DSP audio (`ICAP.md`), so a DMR app would need a new 48 kHz tap
  and an unprocessed path

Two things are not known yet:

- **RF deviation reach (R3).** Needs a measurement on a real radio.
- **Receive channel width in DV mode (R4).** An outside test report suggests it is
  wide enough [EXT].

Biggest effort: **a software AMBE+2 codec**. Biggest legal question: **AMBE+2
licensing/patents** (the owner's call).

**PoC ([POC.md](POC.md)):** the DV DSP's own modulator, patched and run in a C55x
emulator, emits DMR 4FSK that dsd-neo locks onto, frame-for-frame identical to an ideal
reference. It ships inside a re-sealed `.dat` that an independent decoder accepts.
That leaves the hard parts:

- **Deviation (R3):** the DV DSP alone reaches only about 41 % of DMR's ±1944 Hz.
  The gain after it has to change for this mode.
- **Host dibit feed:** the DV DSP's caller still passes D-STAR bits.
- **AMBE+2** (R1).

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
| R2 | 4FSK TX modulator | **Done in emulation** ([POC.md](POC.md)). A 6-site patch plus a new boot section turns `gmsk_mod_bit` into a DMR 4FSK modulator. dsd-neo decodes its emulated output identically to an ideal reference. | [VM] |
| R3 | TX deviation ±1.944 kHz | **Blocker inside the DV DSP.** With RRC peak-to-average, the worst-case no-clip outer level is 0.53 FS ≈ **794 Hz** at the D-STAR scale (0.80 FS = 1.2 kHz), **2.45× short**. The stage after the DV DSP must raise its gain for this mode. | [VM] numbers, [INF] D-STAR-scale linearity |
| R4 | RX discriminator, ≥ ±4.8 kHz channel | Rate is OK: the discriminator arrives at 48 kHz, 10 samples/symbol. Width is likely OK: the FPGA→DSP baseband is reported as 12 kHz wide. | [FW] rate, [EXT] width |
| R5 | TDMA timing | Simplex/hotspot: continuous TX with one used slot. Repeater uplink: needs about 30 ms T/R keying. | [INF] |
| R6 | DMR framing, FEC, LC | Software only (ARM ICAP app or DV DSP) | [INF] |
| R7 | Patch delivery | **Done in emulation**. `tools/dsp_reseal.py` re-seals the DSP group, bumps the region-0 version and re-encrypts with the header-derived IV. The radio's own updater code, run in Unicorn, writes exactly the patched image into the DSP flash ([POC.md](POC.md)). On a real radio: unverified [HW]. | [FW] rules, [VM] run |
| R8 | ARM↔DV DSP bandwidth | The link is the ARM's RSPI0 ↔ the C5517 McSPI (slave), with packets of up to 0xA5 bytes and a GPIO handshake (`DV_DSP.md`). The bit rate is not measured yet. Being SPI, it is likely well above the 9.6 kbit/s raw DMR dibit stream. (An earlier note said SCIF5 at about 10 kbit/s; that link is not used.) | [FW] link, [INF] rate |

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
  the same path the ICAP decoders use. That path is 8 kHz post-DSP audio today, which is
  fine for voice but not for the modem (`ICAP.md`).
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
3. **[FW]/[VM] Host protocol:** the RSPI0 ↔ McSPI packet framing is known and runs in
   emulation. Still to decode: the packet types for D-STAR voice/data, which a DMR payload
   would reuse.
4. **[FW]/[VM] Audio path through the SHARC:** which SPORT feeds the DV DSP's I2S0 and
   the ARM's SSIF0, and what filtering sits between the demodulator/modulator and them.
   This decides R3/R4 and whether an ICAP-side modem can work.
5. ~~C55x micro-emulator~~ and ~~DSP-group re-seal~~: done (`POC.md`).

## Fallback: flat data path

Expose the discriminator and modulator (I2S0 L/R taps) to USB audio. DMR then runs
entirely in an external modem (MMDVM/DSD-FME/M17 software). This needs R3/R4, but
not R1/R2/R6 on the radio. It is the same request as sq6emm/icom-custom-firmware#6.
Icom showed a comparable flat 9600 bps USB path for the IC-9700 as a prototype
(JA1OGZ, 2026-08-29) [EXT].
