# icom-re

Clean-room research on the Icom IC-705 firmware (v1.40 E): what every chip does,
how the D-STAR digital-voice chain works, a DMR feasibility study with an emulated
proof of concept, and a full-radio QEMU emulation that boots the real firmware.

Start with [`ic705/STATE.md`](ic705/STATE.md) (live state, resume point), then
[`ic705/ARCHITECTURE.md`](ic705/ARCHITECTURE.md) and
[`ic705/DMR_FEASIBILITY.md`](ic705/DMR_FEASIBILITY.md) (verdict). Emulation details
are in [`ic705/EMULATION.md`](ic705/EMULATION.md). How SQ6EMM's ICAP app runtime taps
audio, and what that means for DMR, is in [`ic705/ICAP.md`](ic705/ICAP.md).

## Hardware map

| Block | Part | Runs |
|---|---|---|
| Main CPU | Renesas RZ/A1H (Cortex-A9) | Main image on Keil RTX: UI, VDC5 display, SD, settings, radio control; boots and updates the DSPs and FPGA |
| Sub CPU | ST STM32L051R8 (Cortex-M0+) | Power sequencing, front-panel keys (ADC ladders), dial, charger; talks to the main CPU over a SCIF3 link |
| Main DSP | Analog Devices ADSP-21478 (SHARC) | Main signal processing; booted by the ARM streaming its program over RSPI4; control link = its SPIB (slave) on the ARM's RSPI3 |
| DV DSP | TI TMS320C5517 (C55x) | D-STAR digital-voice vocoder path and GMSK modulator; host link = its McSPI (slave) on the ARM's RSPI0 |
| FPGA | Intel Cyclone 10 LP (10CL025) | RF-side logic (role not traced); configuration stored in the DSP flash group |
| Codecs | AKM AK4951 (x2) | Audio ADC/DAC; one is set up by the main DSP over I2C (0x18) [INF] |
| WLAN | TI CC31xx | Wi-Fi network processor |

## Emulation status

The IC-705 port of an RZ/A1H QEMU machine (see [`qemu/README.md`](qemu/README.md))
runs the stock firmware to the live English UI, including the real power-on path.

Works:

- real POWER-key power-on (deep-standby cancel, full bring-up, main screen), no boot hook
- the real sub-CPU firmware, running on an STM32L0 model inside the same QEMU machine
- front-panel keys, main dial and touch (mouse)
- settings EEPROM, with persistence across runs
- charger sensing, English UI

- **both DSPs on their own firmware** (`--dsp`): the main DSP (SHARC, `tools/sharc_sim.py`)
  and the DV DSP (C5517, `tools/c55sim.py`) run as co-simulated devices on the ARM's RSPI3
  and RSPI0. The boot passes the DSP/FPGA and DV DSP version checks and reaches the main
  screen with no popup (the FPGA stays a dummy)

Open:

- audio: SPORT/I2S paths between the ARM, both DSPs and the codecs carry no samples yet;
  the SHARC FIR accelerator is not modelled
- speed (the DSP simulators are CPython) and fully deterministic DSP timing
- clock, power-off by long press

## DMR proof of concept

The DV DSP's own GMSK modulator is patched into a DMR 4FSK modulator, run in a C55x
emulator, and its output is locked onto by an external DMR decoder
(dsd-neo), identical to an ideal reference. The patched DSP group is re-sealed into
a `.dat` that an independent decoder accepts. Emulation only, never run on a radio
and not for flashing. Open points are RF deviation reach, the host-side dibit feed
and a software AMBE+2 codec. See [`ic705/POC.md`](ic705/POC.md).

## Tools

| File | Purpose |
|---|---|
| `tools/build_705_flash.py` | Build the 16 MiB QSPI flash image for QEMU from your `.dat` |
| `tools/run705_gui.py` | Run the emulated radio in a GTK window with key/dial/POWER controls |
| `tools/qemu705.py` | Headless QEMU runner with PC sampling and screenshots |
| `tools/rsp.py` | Minimal GDB remote-protocol client for QEMU |
| `tools/arm_emu.py` | Unicorn function-level emulator for the ARM main image |
| `tools/arm_dsp_update_emu.py` | Runs the radio's own SD-card DSP-flash updater in emulation |
| `tools/c55sim.py` | Whole-firmware TMS320C55x simulator for the DV DSP |
| `tools/c55_emu.py` | Small C55x interpreter for single DV DSP routines |
| `tools/sharc_ldr.py` | Decode the main-DSP SHARC boot stream |
| `tools/sharc_dis.py` | SHARC 48-bit and VISA instruction decoder/disassembler |
| `tools/sharc_sim.py` | Whole-firmware ADSP-21478 simulator for the main DSP (SPI, SPIB, TWI, SPORT DMA models) |
| `tools/dsp_bridge.py` | Main DSP as QEMU's RSPI3 device (socket, handshake P7_6, time sync) |
| `tools/dv_bridge.py` | DV DSP as QEMU's RSPI0 device (McSPI slave, handshake P3_8) |
| `tools/dv_patch_4fsk.py` | Build the 4FSK patch into a decoded DSP image |
| `tools/dv_mod_poc.py` | Verify the stock and patched modulator against models |
| `tools/dmr_poc_wav.py` | DMR dibits through the patched modulator to a WAV file |
| `tools/dsp_reseal.py` | Decode and re-seal the DSP group of a `.dat` |
| `tools/table_scan.py` | Scale-invariant numeric table search in binary images |

## Running it

You supply `705E140.dat` and the decoded images (see below). Then:

```sh
python3 tools/build_705_flash.py 705E140.dat ~/icom/out/705_flash.bin
python3 tools/run705_gui.py          # GTK window plus terminal controls
python3 tools/run705_gui.py --dsp    # ... with both DSPs running their own firmware
```

Building the QEMU machine and the environment variables it expects are described in
[`qemu/README.md`](qemu/README.md) and `ic705/EMULATION.md` ("Running it").

Controls: mouse in the window = touch, mouse wheel = dial. Keys (window or terminal
with focus): `m` MENU, `f` FUNCTION, `q` QUICK, `x`/Esc EXIT, `s` M.SCOPE,
`k` MULTI push, `a` AF/RF push, `l` SPEECH/LOCK, `u`/`d` UP/DOWN, `p` POWER tap,
`P` POWER hold, arrow keys and PgUp/PgDn for the dial, `Q` quit.
`tools/run705_gui.py` documents the full list.

## Clean-room note

This repository contains no Icom firmware, decoded images, disassembly, IDA
databases, manuals or screenshots; findings are written in our own words, with
addresses, sizes and constants only. Supply your own firmware `705E140.dat` from
Icom (https://www.icomjapan.com/support/firmware_driver/4422/) and decode it with
https://github.com/sq6emm/ic705-ic9700-ic905-bring-your-own-firmware. `docs/` is
gitignored and holds vendor manuals locally; `out/` and `fw/` hold decoded images
and are ignored too.
