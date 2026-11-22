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

**Front-panel keys [FW]/[VM]:** four resistor ladders on sub-CPU ADC channels 0, 1, 14, 15
(sub `0x8004714`; scan schedule `0x800467E`: even steps CHSELR 0xC003 → slots for ch0/ch1/
ch14/ch15, step 5 0x3F0, step 10 0xC3F3; 10-bit mode). The 8-bit value (ADC bits 9:2) is
classified with thresholds the ARM sends to sub RAM `0x200001BE` (E6 CC B9 8D 74 48 35 19):
≥0xE6 open; 0xB9–0xCC level 4; 0x74–0x8D level 3; 0x35–0x48 level 2; ≤0x19 level 1; anything
else ignored. Ladder → key-word bits: ch0 L1/L2/L3 = 0x1/0x2/0x4; ch1 L1..L4 = 0x8..0x40;
ch14 = 0x80..0x400; ch15 = 0x800..0x4000 (`0x2000011C`, debounced twice, sent to the ARM,
seen at ARM `0x203E1F1C`). Press from QMP: `qom-set /machine/subcpu adc "0=0x0c0"` (12-bit
level values ≈ L1 0x0C0, L2 0x400, L3 0x800, L4 0xC00; release = 0xFFF). The old model's
0x800 on every channel read as four held keys (key word 0x0889), which together with ARM
ADC ch6 was what opened adjust mode.

**Key map [VM]** (short tap unless noted; names inferred from the screen each opens):
ch1 L1 MENU · ch14 L1 FUNCTION · ch14 L2 M.SCOPE · ch14 L3 QUICK · ch14 L4 EXIT ·
ch0 L2 MULTI push (RF POWER/MIC GAIN/COMP/MONITOR) · ch1 L2 AF/RF push (RF GAIN/SQL) ·
ch0 L3 toggles RIT · ch15 L1 held = dial lock (SPEECH/LOCK) · ch15 L3/L4 held = frequency
steps (UP/DOWN-type) · ch0 L1, ch1 L3, ch1 L4 not identified yet.

**Main dial [FW]/[VM]:** MTU2 channel 1 in phase-counting mode 1 (TMDR_1 = 0x04, TSTR CST1),
10 Hz per count in the default step. The old timer model free-ran TCNT1, which looked like the
dial spinning (frequency drifting by itself). `mtu2g.c` now freezes phase-counting channels
and takes encoder counts from QOM `/machine/mtu2` `dial` (signed int).

**Sub-CPU analog sense [FW]/[VM]:** ch4..9 are reported to the ARM on request (sub
`0x8003BD4`, 10-bit, split 3+7 bits). Any level on **ch4** keeps the ARM out of deep standby
(charge/external-DC behaviour) → model default 0; ch5–9 mid-scale are harmless.

**Settings EEPROM [FW]/[VM]:** RIIC0 pins, device addresses 0x50..0x53, two word-address
bytes → 256 KiB 24M02-class part (A17:A16 in the device address); 64-byte records, bit 6 of
the record number selects the primary/backup copy (`0x2005C32C`). **Writes go through the
RIIC0 peripheral, reads are bit-banged on the same pins** (`0x2005E3D8` → `0x2005E2DC`):
SCL = P1_0 via PSR1, SDA = P1_1 open-drain via PMSR1 (input = released, output = low),
read back on PPR1 bit 1; device byte 0xA1 | A17<<2 | A16<<1 (`0x2005D690`). Both paths are
modelled on one image (`m24m02.c` + the bit-bang slave in `rz_a1h.c`, with a PM/PMSR hook
added to `gpio.c`), persisted to `RZA1H_IC705_EEPROM`. Before the bit-bang slave existed every
read failed, so the firmware rewrote its defaults on each power-on. Now a cold start reads
~104 KB and writes nothing, and settings survive: System Language set to English through the
touch UI (SET > Display > System Language) is still English after a fresh QEMU start [VM].

**Other buses / devices identified [FW]:**
- RSPI3 = FPGA register port (`0x20118E00` commands; `0x2011913C` read state machine). FPGA
  status words carry a type nibble (`0x20118FA4`): type 1/9 = level bytes queued per step
  into `0x204988BC` and smoothed (`0x2011E4D0`, S-meter-like); type 8 = 10-bit value + 2 flags;
  type F = flag. The FPGA stays a dummy (owner's choice), so these read 0xFF now.
- RIIC1 0x6B = battery charger, TI BQ2571x-style (16-bit registers 0x00–0x3A, status/ADC
  0x28/0x2C read back; `0x20019B28`, `0x2001A174`). Modelled as a register file
  (`bq2571x.c`, IDs 0x40/0x88); the firmware writes ChargeOption0 = 0x860F at power-on [VM].
- RIIC1 0x32 = RX-8803 RTC; its interrupt is **IRQ0**, polled through IRQRR in
  `0x200E4588` → reads 7 bytes from register 0x10 (`0x200E3B98`).
- SCIF1 = CI-V `FE…FD` (`0x2010C134`); SCIF2 = shared driver whose pins are switched by
  `0x2011862C` (CI-V on the remote jack with IRQ2, `0x20028FFC`, plus another port);
  SCIF4 = byte ring of 0x36C (GPS NMEA likely); SCIF5 = DV DSP link; USB0 at 0xE8010000.
- P0_5 bit-banged single-wire receiver (`0x20118770`, microphone keys likely).
- Steady-state MMIO census [VM] (3 s trace on the main screen): ARM ADC ch0/1/4/5/6/7 read
  every tick, ch2/ch3 never (gated by bit 5 of config byte `0x20380BD2`, set per power-source
  mode); IRQRR polled every tick (RTC).

**Touch panel [FW]/[VM]:** a TSC2046/ADS7843-class serial touch ADC bit-banged on port 3.
P3_10 /CS, P3_4 DCLK, P3_5 DIN, P3_6 DOUT, P3_11 BUSY, P3_12 /PENIRQ. Pen poll `0x2008D830`
(PPR3 bit 12, release after 25 ticks) sets an RTX event for task `0x2008F5C4`, which reads
X then Y through `0x2008E524`. Command bits come from DMAC ch2 waveforms (`0x202E55BC`,
48 bytes each, into PSR3, completion DMAINT2 → `0x2008CF34`); the tail of the command, the
BUSY handshake and the 12 data bits are clocked by hand. Commands 0xD0 = X and 0x90 = Y.
Results at `0x203DA4E0` (state, X, Y); calibrated pixels in the front-panel block
`0x203E1F1B` (+8 tag 0 = pixels, +9/+11 X/Y BE16), read by the getter `0x20108634` (9999 when
no touch, same as the IC-7300's). Default calibration (blank EEPROM), measured: raw 500/3500 →
X 443/52 px, Y 259/12 px. Modelled in `rz_a1h.c` (`touch` machine property, raw "X,Y" or
"" = up); DMAC ch1–7 got a generic path for transfers to/from the GPIO block (`dmac.c`).

**Power-off (open) [FW]/[VM]:** the debounced P3_9 state (`0x2011D6EC` → `0x20380B4C`,
getter `0x2011C14C`) does follow a hold in the VM, and after 50 ticks `0x20122864` raises a
long-press flag (`0x20380BD8`) and queues status frames to the sub-CPU; the flag feeds
`0x2011DF28` → block +876, used by TX-path logic (`0x20060B84`). Power-off itself is
`0x2007CF44`, gated on the request byte `0x203800C3` (set by the CI-V power-off command
`0x2001FB2C`); the front-panel path to it is not found. Holding P3_9 (~10 s emulated) or the
unidentified ladder keys ch0 L1 / ch1 L3 / ch1 L4 (~5 s) does not power off. Note P3_9 may be a
sub-CPU-driven wake/request line rather than the key itself.

**Window input [VM]:** in the GTK window, the mouse is the touch panel (pixel → raw with the
calibration above), the wheel and Left/Right are the main dial, M/F/S/Q/X(Esc)/K/A/R/L/U/D/1/2/3
are the ladder keys (held while held), and P is POWER.

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
    end; all-zero words at the start = NOP), then three-word block headers. First read as an ADI SHARC boot stream, but **checked and not confirmed**: no SHARC
    signature opcodes (RTS 0x0A3E00000000, absolute JUMP/CALL 0x063E…) under any byte order
    or alignment of the 0x600-byte kernel; DSP563xx (IDA `dsp56k`) decoding of the same bytes
    is also implausible, and it is not C55x either (RET 0x4804 density 0.27/KB vs ≈6/KB in the
    DV DSP image). Core **unknown** [INF]; a chip marking or a statistical ISA classifier
    (cpu_rec) would settle it.
- **Input:** touch/keys path not identified yet (not on I2C: RIIC0=EEPROM 0x53,
  RIIC1=0x6B/0x35; most likely via the sub-CPU status frame).
- No audio, no RF; Wi-Fi/BT/GPS absent.

### Running it

```sh
python3 tools/build_705_flash.py 705E140.dat ~/icom/out/705_flash.bin
python3 tools/run705_gui.py                 # GTK window + terminal controls (keys, dial, POWER)
python3 tools/qemu705.py --force-on --secs 60 --shot out.png   # headless + screenshot
echo 0=ff > <scratch>/substat.txt           # poke sub-CPU status byte 0 live
```
