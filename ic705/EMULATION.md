# IC-705 emulation

Everything here is **[VM]**: it shows what the firmware code does in isolation, not
what the radio does.

## Layers

| Layer | Status | Tool |
|---|---|---|
| ARM function-level (call one firmware function, log MMIO) | **working** | `tools/arm_emu.py` (Unicorn 2.1.4) |
| ARM updater (DSP flash writer) | **working** | `tools/arm_dsp_update_emu.py`: firmware code with HLE for SD read + SPI-NOR model |
| ARM full-system (boot RTX, UI) | **in progress**: port of the IC-7300 QEMU machine | see "Full-system plan" below |
| DV DSP (C55x) | **working for the modulator** | `tools/c55_emu.py`: interprets the IDA-decoded instruction stream; DSPLIB `fir` is emulated at high level |
| Main DSP, FPGA | out of reach for now | chip/format not identified |

## `tools/arm_emu.py`

Maps on-chip RAM (`0x20000000`, 10 MiB), loads `main.bin` at `0x20005000`, maps
the external-bus, SPIBSC, `0xE8xxxxxx` and `0xFCFExxxx` windows as logged MMIO, and
calls a function with R0–R3 (+ stack args) and LR pointing at a stop trampoline.

**Positive control:** `0x201B8868` is zlib `crc32_little(crc, buf, len)`;
`crc32(0, "123456789")` returns `0xCBF43926` in the emulator, equal to zlib. [VM]

## Findings

### SCIF5 init (`0x201FF850`) [VM]

| Reg | Value | Meaning |
|---|---|---|
| SCSPTR | `0x3` | |
| SCFCR | `0x6` then `0x30` | FIFO reset, then trigger levels |
| SCSMR | `0x80` → `0x82` | **clock-synchronous mode**, CKS = 2 (Pφ/16) |
| SCBRR | `0xCF` (207) | |

Bit rate in clock-sync mode is Pφ / (4 · 2^(2n−1) · (N+1)) = Pφ / 6656; with
P1φ = 66.67 MHz → **≈ 10 kbit/s** [INF: clock source not confirmed]. The routine also
sets pin-function registers for ports 4, 5, 6, 7 and 10 (`0xFCFE3x2C`). Whether this
link is the DV DSP host link is [INF] (see `DV_DSP.md`).

## `tools/c55_emu.py` (DV DSP) [VM]

- Executes the instruction list exported from IDA (`address, size, bytes, text`)
  against a word memory built from the DV DSP boot table (`dspB.mem`).
- Implements only the forms used by `gmsk_mod_bit` (`aadd/amar/rpt/mov/neg/bcc/b/
  cmpu/xccpart/nop/call/ret`) and raises `NotImplementedError` on anything else.
- `call fir` (`0xAAC8`, TI DSPLIB `fir`) is high-level emulated: Q15, FRCT, round to
  nearest, history kept per delay-buffer address.
- IDA's stack-variable notation (`*SP(#16h+var_4)`) is in **bytes**: word offset = /2.
- `xccpart` is modelled as "condition the next instruction". The firmware uses two
  encodings (`0x96..`, `0x9E..`) whose placement suggests they differ. With the invert
  flag at its init value (0), both readings give the same stock output, and the 4FSK
  patch removes both instructions.
- **Positive control:** stock `gmsk_mod_bit` equals an independent GMSK model exactly.

## `tools/arm_dsp_update_emu.py` [VM]

`Arm.hle(addr, fn)` replaces one firmware function with Python (Unicorn code hook,
return via LR with Thumb interworking). The DSP flash writer runs as real code. Only
`f_read` (`0x201F29F8`), the FatFs result mapper (`0x200641D4`) and the two RSPI4
SPI-transaction functions (`0x20069F30`, `0x2006A0CC`) are replaced; the latter are
backed by a 16 MiB SPI-NOR model that enforces WREN before erase/program and AND-
programs pages. Results are in `POC.md` (cases A–D). One run takes about 1 s.

## Full-system plan (screen + peripherals)

Base: `jeskko/ic7300-homebrew` `qemu-machine/` (MIT), a custom QEMU 11.1.1 machine for the
same RZ/A1H. It boots stock IC-7300 firmware to the main screen, with GIC, OSTM, MTU2,
GPIO, RIIC (+EEPROM, RTC), SCIF (front panel, DSP link), SDHI SD card, SPIBSC flash,
WDT, DMAC, VDC5 compositing, an OpenVG rasterizer, and a GTK window with touch, keys and
dial. Cloned at `~/icom/ic7300-homebrew` (outside this repo).

IC-705 port, deltas to make (static evidence in brackets):

1. **Flash image:** `tools/build_705_flash.py` → 16 MiB, main domain at 0 (reset vector
   `18 F0 9F E5`), loaded with `-kernel`. Same XIP base `0x18000000` [FW].
2. **Boot loader:** the IC-705 bootstrap copies a loader to `0x20004000` and unpacks main
   to `0x20005000` (entry `0x20005060`) [EXT sq6emm]. Check that the machine's reset at
   `0x18000000` takes this path unchanged.
3. **Peripheral differences found so far:**
   - DV DSP on a clock-synchronous SCIF5 (`SCSMR=0x82`) [VM], not the IC-7300's async DSP
     UART.
   - DSP/FPGA config flash on **RSPI4** [FW]. The 7300 machine only models RSPI2 (its FPGA).
   - Sub-CPU (`"ICOM4075S"`) on SCIF3 with a `0x55` handshake [FW]; the 7300 front panel
     is also on SCIF3.
   - WLAN CC31xx (SPI, NWP), Bluetooth, GPS: absent, stubs.
   - Display panel size and touch controller: to be read from the VDC5 setup the firmware
     writes.
4. **Dummies:** FPGA and both DSPs. The user agreed only a dummy FPGA is needed.

Status: the QEMU build (`qemu-machine/setup.sh`: clone QEMU v11.1.1, symlink the sources,
`configure --target-list=arm-softmmu`, ninja) **needs the owner's approval to run**; it
was blocked by the session's permission policy.
