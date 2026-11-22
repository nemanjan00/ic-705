# IC-705 — live state (re-read FIRST)

Tags: [FW] firmware static · [HW] radio · [VM] emulator · [EXT] outside · [INF] inferred.

## Now

- **2026-11-22: ICAP audio taps traced [FW]** (`ICAP.md`). Apps get mono 8 kHz:
  RX is SSIF0-RX left (48 kHz from the main DSP), decimated by a 96-tap FIR; TX replaces
  the USB MOD source and is interpolated back to 48 kHz. 8 kHz plus the FM audio chain
  make DMR through the stock ICAP API impractical. Next: trace what the SHARC does to the
  SSIF0 audio in FM mode, the deciding question for an ICAP-side DMR app.

- **2026-11-22: full radio boots clean with both DSPs [VM].** `tools/run705_gui.py --dsp`:
  ARM + sub-CPU in QEMU, the main DSP (SHARC, `dsp_bridge.py`, RSPI3/P7_6) and the DV DSP
  (C5517, `dv_bridge.py`, RSPI0 McSPI/P3_8) on their own firmware, FPGA dummy. The main
  screen shows with no version popup. Next: audio paths (SPORT/I2S between ARM, both DSPs
  and codecs), FIR accelerator, making the half-tag version replies deterministic, and
  speed.

- **2026-11-22: main DSP linked into the radio [VM].** QEMU's RSPI3 (the ARM's real main-DSP
  link) talks over a socket to `tools/dsp_bridge.py`, which runs the SHARC firmware in
  `sharc_sim.py`. With the time sync and the DSP's own flash reads, the ARM accepts the
  main-DSP and FPGA versions, and the boot popup now reads **"DV DSP firmware is wrong
  version"**. Next: the DV DSP. **RSPI0 is the ARM <-> DV DSP link**: the C5517's McSPI, set up as a
  slave by DSP-B (the old "unidentified 0x3500 block"). It carries length-framed packets with
  the DV version. To do: a McSPI slave model in `c55sim.py` (plus its DMA path) and a
  second bridge on RZA1H_RSPI0_SOCK.

- **2026-11-22: DV DSP firmware runs in `tools/c55sim.py`** (whole-firmware C55x core) to its
  main loop. The ARM reaches the DV DSP only through the main DSP (DV version arrives in the
  RSPI0 main-DSP handler `0x20202128`), so linking it needs the main DSP as well. **Main DSP = ADSP-21478 SHARC** (service manual IC202); boot-stream encoding solved
  (bit-reversed bytes, LE32, 2×48-bit per 3 words). `tools/sharc_dis.py` decodes kernel + VISA program; `tools/sharc_sim.py` runs the real
  firmware through its whole init (~500k instructions incl. heap, malloc, I2C codec set-up
  at 0x18 via a TWI model) into its main loop; with the SPORT chained-DMA model the SPORT4 (P8I) audio interrupt
  runs its filter chain every block [VM]. The ARM link protocol on SPIB is decoded:
  half-duplex, 4-byte big-endian commands in, changed-status frames out (EMULATION.md).
  Next: SPIB slave-DMA + interrupt (ARM link on RSPI0), SPORT to DV DSP/codecs, then the
  QEMU <-> sim bridge.

- **2026-11-22: real power-on + real sub-CPU [VM].** POWER press → deep-standby cancel →
  full bring-up → main screen, no boot hook; the STM32L0 sub-CPU runs its own decoded
  firmware in the same QEMU machine (`EMULATION.md` "Full-system status (2026-11-22)").
  Adjust-mode trap understood (ARM ADC ch6 window 721..802). Remaining boot popup:
  DSP/FPGA version mismatch, because the main DSP and DV DSP are not emulated.
  **Next:** main DSP core unidentified (SHARC disproved by opcode check); confirm and its host link (RSPI0 command
  queue `0x20201F4C`, ready P3_8), then decide between a full C55x core in QEMU or a
  firmware-accurate version responder; FPGA stays a dummy (owner's choice).
- **2026-11-22 (later): owner chose input/peripherals before DSPs.** Keys work end to end
  (sub ADC ladders); settings EEPROM modelled (RIIC0 0x50..0x53, 256 KiB); charger-sense
  default fixed. Key map and main dial done (EMULATION.md); `tools/run705_gui.py` drives keys/dial/POWER
  from the terminal. **Touch done** (TSC2046 on port 3; mouse in the GTK window). Previously: Ruled out: sub-CPU (no I2C; ADC ch2/3/10 only in the
  init conversion), RIIC0 (EEPROM), RIIC1 0x6B (codec-like), RSPI0/1/3/4, SCIF1/4, GIC pin
  IRQs (none registered). Open leads: FPGA register pages polled over RSPI3 (`E0 00 00 0N`
  + 17× reg 0 reads), GIC 302–304 handler `0x2012642C`, SSIF3 status polling.
  EEPROM persistence fixed (bit-banged read path); UI switched to English and kept.
  Open: POWER-off by long press does not take effect in the VM; FPGA status words unmodelled
  (charger modelled); both DSPs not emulated.

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

- **Full radio emulated to the UI [VM]:** stock 1.40 firmware boots in QEMU to the drawn
  480×272 Japanese UI (status bar, splash, text) with sub-CPU, MTU2, ADC, DMAC, RSPI+NOR,
  GPIO models (`EMULATION.md` "Full-system status"). Gaps: boot hook for power-on, dummy
  DSP/FPGA → version-error popup, input path unknown.

## Backups on record

None — no radio access so far; static analysis of `705E140.dat` (MD5 `2c9c1d7f…`).

## Open questions

- Main DSP chip/format (`0x40000`, `0x80000` blobs; the latter is neither LZSS nor autokey). **Not C55x:** region 0 "DSP-A" is the DV DSP's own loader [FW]; the main DSP is booted by the ARM streaming flash 0x40008.. over RSPI4 (`0x2011D3E0`), the stream's ISA is **unknown** (SHARC and DSP563xx signature checks negative) [INF].
- Is SCIF5 the DV DSP host link; its frame format.
- I2S0 peer (main DSP vs FPGA); per-mode gain after the DV DSP.

## Done

- Container decode, component map, RTOS (Keil RTX), WLAN (CC31xx), FPGA vendor [FW].
- DV DSP in IDA (`TMS32055`): vectors, I2S0 roles, GMSK TX chain, host queue [FW].
- R1: D-STAR AMBE codebooks found (positive control), no AMBE+2 anywhere [FW].
- ARM Unicorn harness with CRC32 positive control; SCIF5 init traced [VM].
- C55x micro-emulator, positive control vs GMSK model; 4FSK patch, dsd-neo validation [VM].
- DSP-group re-seal (identity + patched), accepted by the independent decoder [FW].
