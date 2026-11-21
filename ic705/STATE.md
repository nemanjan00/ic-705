# IC-705 — live state (re-read FIRST)

Tags: [FW] firmware static · [HW] radio · [VM] emulator · [EXT] outside · [INF] inferred.

## Now

- **2026-11-21: DMR modulator PoC done [VM]** (`POC.md`). The patched DV DSP `gmsk_mod_bit`
  produces DMR 4FSK that dsd-neo locks onto, identical to an ideal reference. The
  patched DSP group is re-sealed into a `.dat` that the independent decoder accepts
  (`out/705E140_dv4fsk_EXPERIMENTAL_DO_NOT_FLASH.dat`, MD5 `9601415f…`, gitignored, not
  for flashing).
- **R3 quantified:** the DV DSP alone reaches ≈ 794 Hz of the 1944 Hz needed (2.45× short).
  Next (static): find the gain stage after the DV DSP's I2S0-R (main DSP / FPGA) and
  how DV vs FM modes set it. Or: DSP-side 4FSK demodulator PoC on I2S0-L, and the
  host dibit feed.

- **ARM updater emulated [VM]:** the radio's own DSP-flash writer, fed the patched `.dat`
  (region 0 → `1.42`), programs exactly the patched DV DSP image into the modelled
  SPI-NOR; an unchanged version is silently skipped; a wrong IV writes garbage.
  IV rule solved: IV = byte sum of header version fields [FW].

- **Full-radio emulation started:** porting jeskko/ic7300-homebrew's QEMU RZ/A1H machine.
  IC-705 flash image builder done (`tools/build_705_flash.py`). **Blocked:** building
  QEMU (`qemu-machine/setup.sh`) needs the owner's approval.

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
- C55x micro-emulator, positive control vs GMSK model; 4FSK patch, dsd-neo validation [VM].
- DSP-group re-seal (identity + patched), accepted by the independent decoder [FW].
