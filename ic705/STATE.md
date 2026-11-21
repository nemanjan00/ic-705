# IC-705 — live state (re-read FIRST)

Tags: [FW] firmware static · [HW] radio · [VM] emulator · [EXT] outside · [INF] inferred.

## Now

- **2026-11-21: DMR verdict written** (`DMR_FEASIBILITY.md`): feasible as a firmware
  project. There is no AMBE+2 in hardware [FW]. The discriminator and modulator at
  48 kHz / 10 sps exist in the DV DSP [FW]. Deviation reach (R3) and DV RX width
  (R4) are still open.
- **Next:** R3 needs a radio: key DV into a dummy load and measure deviation. Without a
  radio: decode the SCIF5 ↔ DV DSP host protocol (item 3 in the feasibility doc).

## Backups on record

None — no radio access so far; static analysis of `705E140.dat` (MD5 `2c9c1d7f…`).

## Open questions

- Main DSP chip/format (`0x40000`, `0x80000` blobs; the latter is neither LZSS nor autokey).
- Is SCIF5 the DV DSP host link; its frame format.
- I2S0 peer (main DSP vs FPGA); per-mode gain after the DV DSP.

## Done

- Container decode, component map, RTOS (Keil RTX), WLAN (CC31xx), FPGA vendor [FW].
- DV DSP in IDA (`TMS32055`): vectors, I2S0 roles, GMSK TX chain, host queue [FW].
- R1: D-STAR AMBE codebooks found (positive control), no AMBE+2 anywhere [FW].
- ARM Unicorn harness with CRC32 positive control; SCIF5 init traced [VM].
