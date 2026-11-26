# IC-705 — live state (re-read FIRST)

Tags: [FW] firmware static · [HW] radio · [VM] emulator · [EXT] outside · [INF] inferred.

## Now (2026-11-22)

- **The full radio boots clean in emulation [VM].** Run it with `tools/run705_gui.py --dsp`.
  - Running their own firmware:
    - the ARM and the STM32L0 sub-CPU, in QEMU;
    - the main DSP (ADSP-21478 SHARC, `sharc_sim.py`), bridged by `dsp_bridge.py` on
      RSPI3 with handshake P7_6;
    - the DV DSP (C5517, `c55sim.py`), bridged by `dv_bridge.py` on RSPI0 (its McSPI)
      with handshake P3_8.
  - The FPGA is a dummy, by the owner's choice.
  - The main screen shows with no version popup. Keys, dial, touch, settings EEPROM and
    charger all work.
  - Details are in `EMULATION.md`.
- **ICAP audio taps traced [FW]** (`ICAP.md`). Apps get mono 8 kHz audio.
  - RX is the left channel of SSIF0 receive (48 kHz; its peer is not yet traced),
    decimated by a 96-tap FIR.
  - TX takes the place of the USB MOD audio source and is interpolated back to 48 kHz.
  - Consequence: DMR through the current ICAP API is impractical.
- **SHARC FM audio chain mapped [FW]** (`FM_AUDIO.md`).
  - RX: a phase-difference discriminator, then an 8-section voice-band and
    de-emphasis filter, then a selectable 200/300 Hz CTCSS high-pass, then ×20.
  - TX: a 70 Hz high-pass, pre-emphasis (only for modes 0xB/0xC; other modes copy the
    samples through), then a look-ahead limiter. Tones are added afterwards.
  - The filters are table-driven biquads, so a flat RX path is a data-only patch;
    TX also needs the limiter skipped. Responses assume 48 kHz [INF].
- **Next:**
  1. Map the SSIF0/3/5 ports to their peers: which SHARC SPORT, the DV DSP's I2S0/I2S2,
     the AK4951 codecs.
  2. Check the FM chain in `sharc_sim.py`: drive a known tone through the RX and TX
     functions to confirm the 48 kHz rate and the gains. Then try the flat-table patch
     in emulation.
  3. Then: the FIR accelerator model, deterministic version half-replies, speed.

## Milestones (newest first)

- **2026-11-26: outside DMR RX found [EXT].** BI1BIF ships receive-only DMR/YSF/P25 by
  equalizing the stock FM audio on the ARM, with mbelib-neo, no DSP patch and no TX.
  Compared in `DMR_FEASIBILITY.md`. Our open parts are TX (R3) and our own RX code.
- **2026-11-23: front panel complete [FW]/[VM].**
  - MULTI (P8_10/11), AF/RF-SQL (P8_8/9) and PBT (P8_0/1) encoders found on port 8.
  - Three ARM-ADC key ladders found (ch4/ch5/ch6; ch6 L1 = V/M).
  - The stale ch4/ch5 = 0x300 default was reading as a held key.
  - `run705_gui.py` opens a Tk panel with every knob and key (`tools/front_panel.py`).
  - PTT found: sub-CPU PC15 (key bit 0x8000) sets the ARM transmit flag; the panel has a
    PTT hold button and latch. The screen does not show transmit yet. P0_5 is a Baudot
    (RTTY) line, not the mic keys. ARM ADC ch7 is battery voltage.
- **2026-11-22: DV DSP linked [VM].** The 0x3500 block is the C5517 McSPI, set up as a
  slave. Its master is the ARM's RSPI0, carrying length-framed packets
  `[L][type][data][FF]`. It reports DV version 1.11. The supposed "periodic McSPI re-init"
  was a simulator bug (postfix `xccpart`), not firmware behaviour.
- **2026-11-22: main DSP linked [VM].**
  - RSPI3 is the ARM ↔ SHARC link: 4-byte big-endian words, P7_6 = DSP has a frame.
  - The DSP reads its version tags from the DSP flash through its own SPI master.
  - The bridge holds the ARM's clock still while the slow simulator catches up (time
    sync).
- **2026-11-22: SHARC simulator runs the real firmware** through init into its main loop,
  with SPORT DMA, TWI (codec at I2C 0x18) and the SPIB link modelled.
  - Main DSP = ADSP-21478 [EXT: service manual IC202].
  - Boot-stream encoding solved: bit-reversed bytes, LE32 words, two 48-bit instructions
    per three words [FW].
- **2026-11-22: real power-on + real sub-CPU [VM].** POWER press → deep-standby cancel →
  full bring-up, with no boot hook. Also done:
  - the adjust-mode trap (ARM ADC ch6 window 721..802);
  - key ladders, main dial, touch (TSC2046 on port 3);
  - settings EEPROM (RIIC0 writes, bit-banged reads), so settings persist;
  - charger (RIIC1 0x6B).
- **2026-11-21: full radio emulated to the UI [VM]** on a port of jeskko/ic7300-homebrew's
  QEMU RZ/A1H machine (`qemu/`).
- **2026-11-21: DMR modulator PoC done [VM]** (`POC.md`).
  - The patched DV DSP `gmsk_mod_bit` emits DMR 4FSK that dsd-neo decodes identically to
    an ideal reference.
  - It is re-sealed into a `.dat`, and the radio's own updater code writes it in
    emulation. Not for flashing.
  - R3: the DV DSP alone reaches ≈ 794 Hz of the 1944 Hz needed (2.45× short).
- **2026-11-21: updater rules [FW]/[VM].** IV = byte sum of the header version fields. A
  DSP region is rewritten only when its version string changes.

## Backups on record

None. There has been no radio access; this is static analysis of `705E140.dat`
(MD5 `2c9c1d7f…`).

## Open questions

- Audio wiring:
  - which ARM SSIF faces the SHARC, which faces the DV DSP (I2S0/I2S2) and which faces
    the codecs;
  - where the SHARC's FM RX output block goes, and whether squelch reads it before or
    after the filters.
- Where the extra TX gain after the DV DSP comes from in DV mode (R3), and whether FM
  modes can provide ±1944 Hz.
- What the FPGA does. It has no direct ARM link identified so far; the RSPI3 "status
  words" are the SHARC's.
- The running UI's time source (the clock shows 0:00), and front-panel power-off.
- Why the screen shows no transmit when PTT (sub-CPU PC15) sets the transmit flag, and the
  red "TX" status box shown on every emulated boot. The mic UP/DOWN keys are not found yet.
- Everything [HW]: no claim here has been checked on a radio.

## Done

- Container decode, component map, RTOS (Keil RTX), WLAN (CC31xx), FPGA vendor [FW].
- DV DSP in IDA (`TMS32055`): vectors, I2S0 roles, GMSK TX chain, host queue, McSPI
  link [FW].
- R1: D-STAR AMBE codebooks found (positive control), no AMBE+2 anywhere [FW].
- ARM Unicorn harness with CRC32 positive control; updater emulation [VM].
- C55x micro-emulator and whole-firmware simulator; 4FSK patch, dsd-neo validation [VM].
- DSP-group re-seal (identity + patched), accepted by the independent decoder [FW].
- SHARC loader, disassembler (48-bit + VISA) and whole-firmware simulator [FW]/[VM].
- Full-radio QEMU with both DSPs on their own firmware [VM].
- ICAP audio API and taps [FW].
- SHARC FM RX/TX filter chain and biquad engine [FW].
