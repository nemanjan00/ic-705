# IC-705 — live state (re-read FIRST)

Tags: [FW] firmware static · [HW] radio · [VM] emulator · [EXT] outside · [INF] inferred.

## Now

- **2026-11-21: DV DSP mapped enough to locate the GMSK TX chain** (`DV_DSP.md`).
  Next: decide R1 (vocoder) by searching the DSP group for AMBE vs AMBE+2
  quantizer tables, with the D-STAR AMBE tables as positive control.
- Emulation plan in `EMULATION.md` (ARM function-level first).

## Backups on record

None — no radio access in this work so far; everything is static analysis of
`705E140.dat` (MD5 `2c9c1d7f…`).

## Open questions

- Main DSP: which chip, which blob (`0x40000`, `0x80000`).
- ARM → FPGA / main-DSP control link (mode, filter, deviation).
- I2S0 peer of the DV DSP.

## Done

- Container decode, component map, RTOS, WLAN, FPGA vendor [FW].
- DV DSP boot table parsed, loaded in IDA (`TMS32055`), vectors + GMSK chain named [FW].
