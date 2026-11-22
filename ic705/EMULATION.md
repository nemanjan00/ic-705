# IC-705 emulation

Everything here is **[VM]**: it shows what the firmware code does in isolation, not
what the radio does.

## Layers

| Layer | Status | Tool |
|---|---|---|
| ARM function-level (call one firmware function, log MMIO) | **working** | `tools/arm_emu.py` (Unicorn 2.1.4) |
| ARM updater (DSP flash writer) | **working** | `tools/arm_dsp_update_emu.py`: firmware code with HLE for SD read + SPI-NOR model |
| ARM full-system (boot RTX, UI, screen) | **boots to the IC-705 UI** (QEMU, IC-705 port of the IC-7300 machine) | `qemu/`, `tools/qemu705.py`, `tools/run705_gui.py`; see "Full-system status" |
| DV DSP (C55x) | **working for the modulator** | `tools/c55_emu.py`: interprets the IDA-decoded instruction stream; DSPLIB `fir` is emulated at high level |
| Sub-CPU (STM32L0, aux2 firmware) | **runs its own firmware** inside the QEMU machine (second CPU cluster) | `qemu/ic705-port.patch` (`stm32l0.c`) |
| Main DSP, FPGA | out of reach for now | main DSP likely C55x (see below); FPGA stays a dummy by design |

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

## Full-system status (2026-11-22) [VM]

The stock IC-705 E 1.40 firmware boots on the patched `rz-a1h` QEMU machine
(`RZA1H_BOARD=ic705`) **through the real power-on path, with no boot hook**, and with the
**sub-CPU running its own firmware** (decoded `aux2`, STM32L0 / Cortex-M0+). Sequence:

1. Reset: the ARM finds no power-on factor and enters deep standby (WFI at `0x2007848C`);
   the sub-CPU sleeps in STOP, waiting on EXTI13.
2. POWER (P3_9 low) while in deep standby: the machine performs a deep-standby cancel
   (A9 reset, DSFR = bit 10 = POWER-key factor, `RZA1H_DSWAKE_BIT`).
3. Loader `0x20004840`, DSFR bit 10 branch: sets bit 7 of the u16 at `0xFCFE7020`, waits up
   to 0x4E000 polls for P8_7 high; on timeout sets P10 bit 3. P8_7 is held low
   (`RZA1H_PINS=8_7=0`) so the wait times out [FW]/[VM].
4. Power-state code `0x2007BB10`: DSFR bit 10 + P10 bit 3 → flag `0x20380741` = 1 →
   dispatcher `0x2007B7C0` a1=1 → full bring-up.
5. ARM ↔ sub-CPU exchange over SCIF3 (clock-synchronous) ↔ STM32 SPI1 slave with the four
   handshake lines; the sub reports its version `1.20-E00` (shown in adjust mode as
   `S 1.20-E00`), so the old 「サブCPUが正常に動作していません」 popup is gone.
6. Main screen after ~90 s wall time (-icount shift=1).

Screen language came out English in this run (Japanese before; not yet explained [INF]).

**Adjust (service) mode trap [FW]:** `0x2007AF48` selects adjust mode (`0x20380707` = 1,
"ADJUST MODE" window with M/S/DSP(P)/DSP(D)/FPGA/DV DSP versions) when the power-on flag is
set, byte `0x203E1F1C` has bits 3 and 7 set, and ARM ADC channel 6 (10-bit) reads
721..802. The old default of 0x300 (768) for every channel hit that window; channel 6 is
now 0x3FF. The byte `0x203E1F1C` reads 0x89 after normal power-on, so the ADC window is the
real discriminator (a jig/ladder level on that channel) [INF].

**Factory block 0xFC0000 [FW]:** `0x20090FA4` checks the tag "ICOM INC 2019.12" at
`0x18FFFFF0`, a 512-byte header at `0x18FE3F50`, and a byte sum over 0x3FC00 bytes at
`0x18FC0000`. 0x3FC00 = 480×272×2, so this is a full-screen RGB565 image (the boot logo),
not calibration data. Absent in the `.dat`, so the emulated radio just skips it.

Earlier (2026-11-21) state, kept for reference:

The stock IC-705 E 1.40 firmware boots on the patched `rz-a1h` QEMU machine
(`RZA1H_BOARD=ic705`): boot loader → main image → Keil RTX → full bring-up → the
480×272 UI, with real text (fonts/resources from flash 0xC00000), status bar (TX, battery,
USB/FIL2/P.AMP1/AGC-M, clock, VFO A, BLANK), and the boot splash (ICOM / IC-705 / 1.40).
UI language is Japanese (factory default: the settings EEPROM is blank).

### What is modelled for the IC-705 (all in `qemu/ic705-port.patch`)

| Block | Model | Firmware evidence |
|---|---|---|
| Flash 16 MiB | slot A at 0, resources at 0xC00000, slot-B tag erased | loader `0x200045F4`, `0x201049F0` |
| Sub-CPU (real) | STM32L0 SoC model `stm32l0.c` (Cortex-M0 in CPU cluster 1; RCC, PWR, FLASH, IWDG, SYSCFG, EXTI, GPIO A-D/H, TIM2/6/21/22, ADC+DMA, DMA1, SPI1 master/slave, USART1); runs decoded aux2. Wiring: SCIF3 SCK/TX/RX ↔ SPI1 slave; ARM P7_11 → sub PC13 (EXTI13, TX request / STOP wake); ARM P5_3 → sub PB5 (RX request); sub PB7 → ARM P7_9 (ack); sub PB4 → ARM P5_4 (sub has data) | sub: `0x800220C` power loop, `0x8003F48` TX, `0x8004018` RX, `0x80039EC` EXTI ISR; ARM: `0x20120960`, `0x20120CAC` |
| Sub-CPU (behavioural, when `RZA1H_SUBCPU_FW` unset) | status frames `FC off data… FE` every 10 ms; version `1.20-E00`/`1.00-000`; P7_9 ready/ack follows P7_11 request; live byte overrides via file | RXI3 `0x20120CAC`, TX `0x20120960`, version `0x201211B4`, updater compare `0x2006C708` |
| MTU2 | general model (all channels, compare/clear/overflow, IRQs) | TGI4A starts ADC `0x20005C48`; TGRC_4 at +0x228 |
| ADC | single-conversion ADF, per-channel values | `0x201185AC`; supply class `0x2000B770` (≥0x28B = DC) |
| DMAC ch8–15 | generic block transfers + DMAINT | ch10 GPIO bit-bang `0x2011EBBC`; ch15 SCIF3 TX |
| RSPI0–4 | generic SPI master; RSPI4 backed by SPI-NOR (DSP group image) | DSP flash `0x2006A218`; RSPI3 register writes (RF/codec chip) |
| GPIO | P3_9 POWER (active low), P7_9 sub-CPU handshake, live `pins` QOM property | `0x2011D6EC`, `0x20120960` |
| Deep standby | DSFR reset value; STBCR1 bits 7:6 + WFI = deep standby; POWER press cancels it (A9 reset, DSFR factor bit) | `0x2007BB10`, `0x20078278`, loader `0x20004840` |
| (from the IC-7300 machine, unchanged) | GIC, OSTM, WDT, RIIC0-2 (+EEPROM, RTC), SCIF, SDHI, VDC5 compositor, OpenVG, L2C | — |

### Known gaps

- ~~Boot hook~~: solved, see the power-on sequence above (`--force-on` is kept in
  `qemu705.py` for runs without the sub-CPU firmware).
- **Main DSP and DV DSP not emulated.**
  - *DV DSP* = TI C55x. The region-0 "DSP-A" boot table is **its** second-stage loader (C55x
    code driving the C55x SPI controller at I/O 0x3000–0x300A with SPI-flash READ/WREN/PP/
    RDSR; checks a tag at flash 0x3FFFC, then loads the DSP-B image and jumps through
    word 0x100) [FW].
  - *Main DSP* is **booted by the ARM** (`0x2011D3E0`, called from full bring-up
    `0x2007B168`): an FPGA/SPI command `0x02000000|(n<<4)` (`0x20119520`, n=1), then RSPI4
    READ (`03`) at flash **0x40008**, then the RSPI4 TX-empty ISR `0x2011D378` keeps
    clocking 32-bit dummy words while **P7_6** is high, at most 0x40000 bytes, so the flash
    streams region 1 straight into the DSP. Then n=2, and the ARM waits for **P7_7** to go
    high (DSP up) [FW]. In the VM the stream stops after 4 bytes because `rspi_gen.c` has
    no TX-empty interrupt yet [VM].
  - Region-1 stream layout: 0x600 bytes = 256 × 48-bit instructions (zero padded at the
    end; all-zero words at the start = NOP), then three-word block headers. This is the
    shape of an **Analog Devices SHARC** SPI-slave boot stream (boot kernel + tagged
    blocks); 12-byte periodicity = two 48-bit instructions per three 32-bit words [INF,
    strong]. No SHARC CPU model exists in QEMU/IDA here, so running it needs a new SHARC
    core (large job).
- **Input:** touch/keys path not identified yet (not on I2C: RIIC0=EEPROM 0x53,
  RIIC1=0x6B/0x35; most likely via the sub-CPU status frame).
- No audio, no RF; Wi-Fi/BT/GPS absent.

### Running it

```sh
python3 tools/build_705_flash.py 705E140.dat ~/icom/out/705_flash.bin
python3 tools/run705_gui.py                 # GTK window, real power-on, real sub-CPU
python3 tools/qemu705.py --force-on --secs 60 --shot out.png   # headless + screenshot
echo 0=ff > <scratch>/substat.txt           # poke sub-CPU status byte 0 live
```
