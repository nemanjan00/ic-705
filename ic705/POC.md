# PoC — DMR 4FSK modulator in the IC-705 DV DSP (emulated)

Status: **works in emulation [VM]; never run on a radio.** The sealed `.dat` this
produces is **not for flashing**: the DV DSP's caller still feeds D-STAR *bits*, so on
air it would send garbage until the host side feeds dibits (see "What's missing").

## What was built

A patch to the DV DSP's `gmsk_mod_bit` (byte `0x16B60`) that turns the D-STAR GMSK
modulator into a DMR 4FSK modulator, reusing the firmware's own DSPLIB `fir` call,
output buffer and I2S path unchanged.

| Address | Stock | Patched | Bytes |
|---|---|---|---|
| `0x16B6D` | bit → ±dev selection, invert flag (35 bytes) | `amar *(#w0x7500), XAR3` | `EC31 BE00 7500` |
| `0x16B73` | ″ | `mov *AR3(T0), AR3` (AR3 = level[dibit]) | `AB6B` |
| `0x16B75` | ″ | 27 × `nop` | `20…` |
| `0x16B95` | `mov #20h, T1` (32 taps) | `mov #50h, T1` (80 taps) | `7600 5058` |
| `0x16B9B` | `amar *(#gmsk_pulse), XAR1` | `amar *(#w0x7400), XAR1` | `EC31 9E00 7400` |
| `0x16BA1` | `amar *(#gmsk_delayline), XAR3` | `amar *(#w0x7480), XAR3` | `EC31 BE00 7480` |

New boot-table section, word `0x7400`, `0x104` words (free RAM: the DSP-A loader
area above the system-stack top `0x7000`; no static references in `0x7000–0x7F7F`
except the SSP init) [FW]:

- `0x7400` 80-tap RRC, α = 0.2, 10 samples/symbol, 8-symbol span, Q15 (max 32000)
- `0x7480` 82-word zeroed FIR delay line
- `0x7500` level[dibit]: `01`→+3, `00`→+1, `10`→−1, `11`→−3 (× amp/3), amp = `0x4800`

Encodings were hand-assembled from encodings the firmware already uses (no C55x
assembler available; TI's public CGT URL is gone) and **checked by decoding them in
IDA** (`TMS32055`) before use.

## Verification chain

1. **Positive control [VM]:** stock `gmsk_mod_bit` in `tools/c55_emu.py` vs an
   independent Python GMSK model: max difference **0** over 2000 samples; peak 0.80 FS.
2. **Patched routine [VM]:** 214 random dibits + MS voice sync → own RRC receiver:
   **0 symbol errors**, sync found at the expected symbol (+8 symbols of filter delay).
3. **External decoder [VM]:** 4.32 s DMR stream (BS layout, 12 superframes, both
   slots) through the emulated patch → 48 kHz WAV → **dsd-neo: 18 DMR syncs, slot
   tracking**; frame-for-frame identical to dsd-neo's decode of an ideal floating-point
   4FSK reference made from the same dibits. Remaining `CACH/EMB ERR` lines come from
   the test content (random CACH/EMB bits), equally present in the reference.
4. **Container [FW + independent tool]:** `tools/dsp_reseal.py` decodes the DSP group
   identically to sq6emm's decoder; an identity re-seal and the patched re-seal both
   decode with sq6emm's `ic705_decode.py`, main/aux/WLAN unchanged; the patched code
   bytes inside the sealed file equal the bytes executed in the emulator; the image
   decoded back out of the sealed `.dat` (tables from the new boot section) emulates to
   a stream dsd-neo decodes identically to the reference.

Negative-control caveat: a 2-level signal still syncs (16 vs 18) because DMR sync
words use only ±3 symbols — sync count alone does not prove 4-level correctness; the
0-error dibit recovery in step 2 does.

## The radio's own updater writes it (ARM emulation) [VM]

`tools/arm_dsp_update_emu.py` runs the IC-705's SD-card DSP-flash writer as real
firmware code in Unicorn. The chain is MD5Init `0x20099B68` → `0x2006AB5C(fh, 0, f54,
md5ctx, 0)` → MD5Final `0x2009A760`. Inside that chain the firmware does its own SD read,
MD5 of the stored bytes, autokey de-obfuscation, streaming LZSS, region gating,
per-64 KiB read-compare, sector erase and page program. Only the SD file read and the
SPI-NOR chip on RSPI4 are modelled (`06 05 03 D8 02`).

| Case | MD5 | IV | Region mask | SPI activity | DSP flash after |
|---|---|---|---|---|---|
| A stock `.dat` over stock flash | ok | `0x0D` | `0000` | none | unchanged |
| **B patched, region 0 version `1.42`** | ok | `0x0E` | `0001` | 780 READ, 414 WREN, erase `0x10000`+`0x20000`, 412 PP | **== patched image** |
| C patched, version left `1.41` | ok | `0x0D` | `0000` | none | unchanged (**patch silently skipped**) |
| D version bumped, stream not re-encrypted | **ok** | `0x0E` | `0001` | erases `0x0`…`0x30000` | **260 162 wrong bytes in region 0** |

So a deliverable DSP patch must (1) bump the region-0 version string in the header
(`hdr[28:32]`) and (2) be encrypted with IV = sum(`hdr[28:44]`). `dsp_reseal.py
--region0-version` does both. Case D shows the MD5 does **not** catch a wrong IV: the
updater would overwrite the DSP boot stub with garbage.

What is still not emulated: the DV DSP booting from that SPI flash (C55x ROM boot
loader → DSP-A stub at `0x0` → DV DSP boot table at `0x10000`), and the pre-scan
`0x2006C708` itself (its IV and mask rules are applied from the decompiled logic).

## Reproduce (needs your own `705E140.dat` and the sq6emm decoder)

```sh
python3 tools/dv_mod_poc.py                       # steps 1-2
python3 tools/dmr_poc_wav.py /tmp/dmr.wav          # step 3 input
dsd-neo -i /tmp/dmr.wav -s 48000 -o null
python3 tools/dv_patch_4fsk.py dsp.image.bin dsp_4fsk.bin
python3 tools/dsp_reseal.py reseal 705E140.dat dsp_4fsk.bin out.dat --region0-version 1.42
python3 tools/arm_dsp_update_emu.py                # runs cases A-C against out/
```
The emulator needs `dspB.mem` and IDA-exported instruction JSON for `0x16B60–0x16BAF`
(stock and patched); see `EMULATION.md`.

## What it shows / what's missing

Shows: R2 (4FSK modulator) is a small, self-contained DV DSP patch; R7 (delivery of a
patched DSP image) works at the file level.

Missing for on-air DMR:
- **Deviation (R3):** worst-case no-clip amp is `0x49F7`; RRC peak-to-average leaves the
  steady outer level at **0.53 FS ≈ 794 Hz** at the D-STAR scale (0.80 FS = 1.2 kHz) —
  **2.45× short of 1944 Hz**. The gain after the DV DSP must go up for this mode.
- **Dibit source:** the bit pump (`0x1575F`) passes D-STAR bits; it needs a dibit stream
  from the host (new packet type on the RSPI0 ↔ McSPI link, `DV_DSP.md`), or a DSP-side
  DMR framer.
- **RX:** 4FSK demodulator on the I2S0-L discriminator (same structure in reverse).
- **AMBE+2** in software (R1) and DMR framing/FEC (R6).
- Radio acceptance of a re-sealed DSP group is unverified [HW].
