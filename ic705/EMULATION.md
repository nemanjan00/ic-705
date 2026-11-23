# IC-705 emulation

Everything here is **[VM]**: it shows what the firmware code does in isolation, not
what the radio does.

## Layers

| Layer | Status | Tool |
|---|---|---|
| ARM function-level (call one firmware function, log MMIO) | **working** | `tools/arm_emu.py` (Unicorn 2.1.4) |
| ARM updater (DSP flash writer) | **working** | `tools/arm_dsp_update_emu.py`: firmware code with HLE for SD read + SPI-NOR model |
| ARM full-system (boot RTX, UI, screen) | **boots to the main screen through the real power-on path** (QEMU, IC-705 port of the IC-7300 machine) | `qemu/`, `tools/qemu705.py`, `tools/run705_gui.py`; see "Full-system status" |
| Sub-CPU (STM32L0, aux2 firmware) | **runs its own firmware** inside the QEMU machine (second CPU cluster) | `qemu/ic705-port.patch` (`stm32l0.c`) |
| Main DSP (ADSP-21478 SHARC) | **runs its own firmware, linked to QEMU on RSPI3** | `tools/sharc_sim.py` + `tools/dsp_bridge.py` |
| DV DSP (C5517) | **runs its own firmware, linked to QEMU on RSPI0** | `tools/c55sim.py` + `tools/dv_bridge.py`; `tools/c55_emu.py` (single routines, modulator PoC) |
| FPGA | dummy (owner's choice) | — |
| Audio (SSIF/SPORT/I2S/codecs) | **no samples flow yet** | — |

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
sets pin-function registers for ports 4, 5, 6, 7 and 10 (`0xFCFE3x2C`). It was once the
candidate DV DSP link; that link turned out to be RSPI0 ↔ McSPI, and SCIF5 stays silent
during boot. Its role is open.

## `tools/c55_emu.py` (DV DSP) [VM]

- Executes the instruction list exported from IDA (`address, size, bytes, text`)
  against a word memory built from the DV DSP boot table (`dspB.mem`).
- Implements only the forms used by `gmsk_mod_bit` (`aadd/amar/rpt/mov/neg/bcc/b/
  cmpu/xccpart/nop/call/ret`) and raises `NotImplementedError` on anything else.
- `call fir` (`0xAAC8`, TI DSPLIB `fir`) is high-level emulated: Q15, FRCT, round to
  nearest, history kept per delay-buffer address.
- IDA's stack-variable notation (`*SP(#16h+var_4)`) is in **bytes**: word offset = /2.
- `xccpart` is modelled as "condition the next instruction". The firmware uses two
  encodings: `0x96..` is a prefix and `0x9E..` a postfix that gates the *preceding*
  instruction (found later with `c55sim.py`). With the invert flag at its init value (0),
  both readings give the same stock output, and the 4FSK patch removes both instructions.
- **Positive control:** stock `gmsk_mod_bit` equals an independent GMSK model exactly.

## `tools/arm_dsp_update_emu.py` [VM]

`Arm.hle(addr, fn)` replaces one firmware function with Python (Unicorn code hook,
return via LR with Thumb interworking). The DSP flash writer runs as real code. Only
`f_read` (`0x201F29F8`), the FatFs result mapper (`0x200641D4`) and the two RSPI4
SPI-transaction functions (`0x20069F30`, `0x2006A0CC`) are replaced; the latter are
backed by a 16 MiB SPI-NOR model that enforces WREN before erase/program and AND-
programs pages. Results are in `POC.md` (cases A–D). One run takes about 1 s.

## Full-system plan (screen + peripherals) — historical

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
3. **Peripheral differences found so far** (superseded where noted):
   - ~~DV DSP on a clock-synchronous SCIF5~~: the DV DSP is on RSPI0 (McSPI), and the main
     DSP is on RSPI3.
   - DSP/FPGA config flash on **RSPI4** [FW]. The 7300 machine only models RSPI2 (its FPGA).
   - Sub-CPU (`"ICOM4075S"`) on SCIF3 with a `0x55` handshake [FW]; the 7300 front panel
     is also on SCIF3.
   - WLAN CC31xx (SPI, NWP), Bluetooth, GPS: absent, stubs.
   - Display panel size and touch controller: to be read from the VDC5 setup the firmware
     writes.
4. **Dummies:** only the FPGA (owner's choice). Both DSPs now run their own firmware.

Build: `qemu-machine/setup.sh` (clone QEMU v11.1.1, symlink the sources,
`configure --target-list=arm-softmmu`, ninja); see `../qemu/README.md`.

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

Screen language: English, because System Language was set once through the touch UI and
the settings EEPROM now persists (see below). A blank EEPROM gives the Japanese default.

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
steps (UP/DOWN-type) · ch0 L1 opens the BW/SFT (twin PBT) window · ch1 L3, ch1 L4, ch15 L2
show nothing visible on the main screen yet.

**Front-panel encoders [FW]/[VM]:** besides the main dial (MTU2 ch1), three quadrature
encoders sit on **port 8**. The ARM samples PPR8 from its ADC tick (`0x20104B0C` →
`0x2008E2F0`) and keeps their counts in front-panel bytes +16..+18 (block `0x203E1F1B`).
- P8_0/P8_1 → byte +16. Decoder `0x2008E200` (one count per two Gray transitions).
  Turning it opens and moves the BW/SFT (twin PBT) window [VM].
- P8_8/P8_9 → byte +17. Same decoder. Shows and changes **AF GAIN** [VM], so it is the
  AF/RF-SQL knob (its push is ch1 L2).
- P8_10/P8_11 → byte +18. Active-low inputs, decoder `0x2008E13C` (one count per full
  Gray cycle). Changes the frequency by 1 kHz per count [VM], **MULTI** [INF]. Turning it
  fast gives bigger steps (speed-up).

Model: no new device. The `pins` property steps the pins through Gray code; idle is
`8_0=0,8_1=0,8_8=0,8_9=0,8_10=1,8_11=1`. The main dial runs at 10 Hz per MTU2 count.

**ARM ADC key ladders [FW]/[VM]:** the ARM's own ADC (`0x2011DF28` copies DR0..7 into
`0x203E1E30`) carries three more ladders. They are classified every 4 ticks into
front-panel bytes +4/+5:
- **ch6** (`0x2008DBF0`): 160–236 / 56–124 / 312–397 / 509–597 = levels 0–3 → byte +4 bits
  0x04/0x08/0x01/0x02; released when ≥ 836. ch6 L1 (≈ 90) switches VFO↔MEMO, **V/M**
  [VM, name INF]. The adjust-mode window 721..802 lies between these levels.
- **ch4 and ch5** (`0x2008DE40`): 412–514 / 576–669 / 700–781 / 809–880 = levels 1–4 →
  byte +5 bits 0x01–0x08 (ch4) and 0x10–0x80 (ch5); released when ≥ 920; ≤ 372 is a
  separate "shorted" state. No visible effect on the main screen [VM]. Probably mic or
  accessory keys [INF].
- The old default of 0x300 on ch4/ch5 read as **level 3 held**. The GUI now starts them at
  0x3FF. Live control: `qom-set /machine/adc vals "6=90"` (10-bit; -1 returns the channel
  to `RZA1H_ADC_VALS`).
- Other ARM ADC channels: ch0 (threshold checks in `0x200198E4`, level mapping in
  `0x20061618`); ch7 = **battery voltage** (smoothed into +876; 0 shows LOW BATTERY, 0x300 a red, nearly empty icon, 0x3FF full) [VM]; ch2/ch3 only in one
  power mode.

**PTT [FW]/[VM]:** sub-CPU **PC15**, active low with a pull-up. The sub-CPU's report loop
(`0x800425C`) puts it in key-word bit 0x8000 (ARM `0x203E1F1D` bit 7), next to the 15 ladder
keys. Holding it sets the ARM transmit flag (`0x203806F4` bit 6, read by `0x2002F04C` into
`0x203E1E28`) within one tick, and releasing it clears the flag [VM]. The factory input test
(`0x20019978`, steps 0..15 from the table at `0x202DA0A6`) checks this bit along with the
ladder keys. The mic-jack PTT is assumed to be wired to the same line [INF]. Live control:
`qom-set /machine/subcpu pins "C15=0"` (press), `"C15=-1"` (release to the pull-up).
- The same loop reports **PC14** and **PD2** in its status byte (+53, bits 0/1). PC14 high
  sets ARM `0x203E1D97`/`0x203E1DDB`. Their roles are unknown. They are not PTT [VM].
- **Open:** the screen does not change while the flag is set, even with `--dsp`. The red
  "TX" box shows in the status bar on every boot and stays with ch4/ch5 released and with
  full battery, so it is not either of those. Whatever is still missing for transmit is
  probably DSP/FPGA confirmation [INF].

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
- RSPI3 = **main-DSP (SHARC) link**, not an FPGA port as first noted (see "ARM <-> main-DSP
  link" below). The status words it returns carry a type nibble (`0x20118FA4`): type 1/9 =
  level bytes queued per step into `0x204988BC` and smoothed (`0x2011E4D0`, S-meter-like);
  type 8 = 10-bit value + 2 flags; type F = version reply.
- RIIC1 0x6B = battery charger, TI BQ2571x-style (16-bit registers 0x00–0x3A, status/ADC
  0x28/0x2C read back; `0x20019B28`, `0x2001A174`). Modelled as a register file
  (`bq2571x.c`, IDs 0x40/0x88); the firmware writes ChargeOption0 = 0x860F at power-on [VM].
- RIIC1 0x32 = RX-8803 RTC; its interrupt is **IRQ0**, polled through IRQRR in
  `0x200E4588` → reads 7 bytes from register 0x10 (`0x200E3B98`).
- SCIF1 = CI-V `FE…FD` (`0x2010C134`); SCIF2 = shared driver whose pins are switched by
  `0x2011862C` (CI-V on the remote jack with IRQ2, `0x20028FFC`, plus another port);
  SCIF4 = byte ring of 0x36C (GPS NMEA likely); SCIF5 = clock-synchronous, silent at boot,
  role open; RSPI0 = DV DSP link; USB0 at 0xE8010000.
- P0_5 bit-banged serial line (`0x20118938`): mode 2 receives (`0x20118770`, start bit, 22 ticks per bit, 5 data bits), mode 4 transmits (`0x2011881C`). Codes 27/31 switch FIGS/LTRS, so it is **Baudot (RTTY)**, not the mic keys [FW].
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

**I2C write-busy fix [VM]:** the IC-7300 RIIC model holds any bus busy for an EEPROM write
cycle (5 ms) after a write, NACKing the next addresses. On the IC-705 that only applies to
RIIC0 (EEPROM); on RIIC1 it was refusing transactions after every charger write.

**Clock (open) [FW]/[VM]:** the status-bar clock stays 0:00/0:01. The RX-8803 read path
(`0x200E4588` polls IRQ0 in IRQRR, `0x200E3B98` reads 7 bytes from register 0x10 at device
0x32 over RIIC1) lives in the standby/charge loops; the model now raises IRQ0 once per second
(`rza1h_gpio_ext_irq`), but in normal running mode no RIIC1 transaction to 0x32 is ever made
(all RIIC1 traffic is charger writes to 0x6B). Where the running UI gets its time is not
found yet.

**Window input [VM]:** in the GTK window, the mouse is the touch panel (pixel → raw with the
calibration above), the wheel and Left/Right are the main dial, M/F/S/Q/X(Esc)/K/A/R/L/U/D/1/2/3
are the ladder keys (held while held), and P is POWER.
`tools/run705_gui.py` also opens a Tk **front-panel window** (`tools/front_panel.py`). It
has the main dial, the MULTI, AF/RF-SQL and PBT encoders (wheel or ◀ ▶), every sub-CPU
and ARM ladder key (held while the mouse is held), and POWER. Checked against the
emulator: MENU opens the menu, MULTI ±5 moves ±5 kHz, AF/RF-SQL shows AF GAIN, and
100 main-dial counts = 1 kHz [VM].

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
| RSPI0–4 | generic SPI master with RX trigger level, TX-empty IRQ and RSPI0 DMA; RSPI4 backed by SPI-NOR (DSP group image); RSPI3 = main-DSP link (socket to `dsp_bridge.py`); RSPI0 = DV DSP link (socket to `dv_bridge.py`) | DSP flash `0x2006A218`; DSP link state machine `0x20118E68`; DV link `0x201FFF08` |
| GPIO | P3_9 POWER (active low), P7_9 sub-CPU handshake, live `pins` QOM property | `0x2011D6EC`, `0x20120960` |
| Deep standby | DSFR reset value; STBCR1 bits 7:6 + WFI = deep standby; POWER press cancels it (A9 reset, DSFR factor bit) | `0x2007BB10`, `0x20078278`, loader `0x20004840` |
| (from the IC-7300 machine, unchanged) | GIC, OSTM, WDT, RIIC0-2 (+EEPROM, RTC), SCIF, SDHI, VDC5 compositor, OpenVG, L2C | — |

### Known gaps

- The ARM's boot stream into the SHARC (RSPI4, gated by P7_6) is not fed to the simulator.
  `sharc_sim.py` loads the program from the parsed loader blocks instead. The DV DSP
  likewise starts from its boot table rather than through the C55x ROM loader.
- **Audio:** no samples move between the ARM SSIFs, the SHARC SPORTs, the DV DSP I2S and
  the codecs. The SHARC FIR accelerator is not modelled.
- The FPGA is a dummy. RF, Wi-Fi, BT and GPS are absent.
- The clock shows 0:00, and power-off by long press does not work (see above).
- Bridge timing: the second halves of the DSP-program and FPGA version tags sometimes get
  no reply.

DSP boot background, kept because the encoding work is reusable [FW]:
  - *DV DSP* = TI C55x. The region-0 "DSP-A" boot table is **its** second-stage loader (C55x
    code driving the C55x SPI controller at I/O 0x3000–0x300A with SPI-flash READ/WREN/PP/
    RDSR; checks a tag at flash 0x3FFFC, then loads the DSP-B image and jumps through
    word 0x100) [FW].
  - *Main DSP* is **booted by the ARM** (`0x2011D3E0`, called from full bring-up
    `0x2007B168`): an FPGA/SPI command `0x02000000|(n<<4)` (`0x20119520`, n=1), then RSPI4
    READ (`03`) at flash **0x40008**, then the RSPI4 TX-empty ISR `0x2011D378` keeps
    clocking 32-bit dummy words while **P7_6** is high, at most 0x40000 bytes, so the flash
    streams region 1 straight into the DSP. Then n=2, and the ARM waits for **P7_7** to go
    high (DSP up) [FW].
  - Region-1 stream layout: 0x600 bytes = 256 × 48-bit instructions (zero padded at the
    end; all-zero words at the start = NOP), then three-word block headers. **The main DSP is an ADSP-21478 SHARC** [EXT: service manual,
    IC202]. The earlier signature tests (SHARC, DSP563xx, C55x, Nios II, Blackfin) were all
    negative because of the stream encoding, now solved [FW]: every byte of region 1 is
    stored **bit-reversed** (LSB-first, like the FPGA region); the bytes form 32-bit
    little-endian words from flash 0x40008; each group of three words (a, b, c) holds two
    48-bit instructions, `(b & 0xFFFF) << 32 | a` and `c << 16 | b >> 16`. Decoded that way
    the 0x600-byte kernel is SHARC code: runs of `0x0Fxx` "Ureg = imm32" loads, 36
    jump/call-class words and RTS (0x0A3E00000000) ×2, versus 0 under every other layout.
    The first word is 0xA5.
    Stream structure (`tools/sharc_ldr.py`): 256-instruction kernel, then 231 standard
    ADI loader blocks consuming region 1 exactly (36,087 words, ending at byte 0x233E4):
    INIT_L32 ×95 (data 0xB2000..0xB9D3E), ZERO_LDATA ×113, INIT_L16 ×19 (0xD22B 16-bit
    parcels at short-word 0x124354..0x1315D3), INIT_L48 ×2 (12 instructions), ZERO_L48 ×1,
    and FINAL_INIT (256 instructions over the kernel: the interrupt vector table, each vector
    `PUSH STS; JUMP 0x8C0B7 (DB); DM(I7,M7) = R2; R2 = <handler>`). INIT_L48 needs 3 words
    per *pair* of instructions (rounded up) — getting that wrong misaligned everything after
    it. The main program is the INIT_L16 payload: **VISA** code (16/32/48-bit compressed
    instructions in short-word space), so the decoder needs the VISA forms too.
    Lesson: a negative ISA signature test means little until the
    container encoding (bit order, word size, packing) is known.

### Running it

```sh
python3 tools/build_705_flash.py 705E140.dat ~/icom/out/705_flash.bin
python3 tools/run705_gui.py                 # GTK window + terminal controls (keys, dial, POWER)
python3 tools/qemu705.py --force-on --secs 60 --shot out.png   # headless + screenshot
echo 0=ff > <scratch>/substat.txt           # poke sub-CPU status byte 0 live
```

## DV DSP simulator (`tools/c55sim.py`) [VM]

A whole-firmware TMS320C55x simulator. Decoding comes from IDA (TMS32055): an export of
every code head with mnemonic, operand text and IDA's numeric operand fields
(`~/icom/out/705E140_full/dspB_insns.json`, gitignored; regenerate with the IDA script in
the commit message / STATE). The simulator implements the semantics: 40-bit accumulators
with SXMD/M40/SATD/FRCT/SMUL, MMR-mapped registers, direct / indirect / circular / stack /
MMR / I/O addressing, `Tn=Smem` loads, dual MAC/MAS::MOV forms, repeat single and block
repeat, conditional execution (`xcc`/`xccpart`), parallel pairs, dual-stack call/return,
and interrupts (IFR write-1-to-clear, IVPD vectors). Unknown forms raise instead of
guessing.

Status: from the DSP-B entry (byte 0xA9AC) the firmware sets the clocks/PLL (0x1C00 block),
SAR ADC (0x7012..0x701A), the McSPI (0x3510..0x353C) and I2S0 (0x2800: 0x20, 0x1C, 0x28,
enable), enables IER0 = 0x0AA0 and runs its main loop, serving the host link and polling
its I2S receive rings. Five million instructions run with no unimplemented form. Linked
to QEMU, it answers the ARM's version request (see "ARM <-> DV DSP link").

Findings on the way:
- IDA reports some dual MAC/MAS::MOV forms one byte short (the trailing byte stays
  undecoded); the simulator absorbs that byte.
- Computed switch tables (`mov dbl(*AR3), AC0; b AC0`, e.g. table at word 0x1FC24) point at
  code IDA never disassembled; those entries were turned into code in the IDA database and
  the export regenerated.
- **Correction [FW]: the DV DSP has a host link, its McSPI.** The "unidentified 0x3510..0x353C
  block" is the C5517 **McSPI** (C5517 data sheet table 6-16: 0x3500 REVISION, 0x3510
  SYSCONFIG, 0x3514 SYSSTATUS, 0x3518/19 IRQSTATUS, 0x351C/1D IRQENABLE, 0x3528 MODULCTRL,
  0x352C/2D CH0CONF, 0x3530 CH0STAT, 0x3534 CH0CTRL, 0x3538/39 CH0TX, 0x353C/3D CH0RX,
  0x3580/0x35A0 DMA FIFO ports). DSP-B sets it up as a **slave** (MODULCTRL = 4), channel 0
  with 8-bit words (CH0CONF 0x0001_03C3), IRQENABLE = TX0_EMPTY + RX0_FULL, CH0CTRL = 1, TX
  = 0xFF (0x18539..0x1858F). (An apparent periodic re-init was a simulator bug, see below.)
  The master is the ARM's **RSPI0**: its length-framed packets `[L][type][L-2 bytes][FF]`
  (ARM 0x201FFA44 / 0x201FFF08 / 0x20202128) carry the DV DSP's status, including the
  version shown as "DV DSP" (block 0x203E1B67). The service manual's C5517 pin list shows
  the McSPI pins (shared with MMC1), which fits.
- The DV DSP's other links are I2S0 (discriminator in, modulation out) and I2S2
  (pass-through). Their peers are not traced yet.

## Main DSP (ADSP-21478 SHARC) [FW]/[EXT]

`tools/sharc_dis.py`: own 48-bit SHARC decoder/disassembler written from the opcode
tables of the SHARC Processor Programming Reference (rev 2.4; in gitignored `docs/`).
It decodes every instruction of the 256-instruction boot kernel. The kernel is the
standard ADI SPI-slave loader: it clears IMASK/IRPTL, reads the next block header into
R8/R2/R3 through the SPI receive buffer (`DM(0x92003..0x92005)`), dispatches on the tag
(zero fill, INIT_L32, INIT_L48 via PX, INIT_L64), and on FINAL_INIT programs the SPI
receive DMA (`DM(0x1080)` = 0x92000, `DM(0x1081)` = 1, `DM(0x1082)` = 0x180) to overwrite
itself with the final 256 instructions, IDLEs until the DMA interrupt and jumps to 0x8C003.

Manual errata noted: the chapter-10 table prints the Type 6a "with mem data move" layout
twice; the "without move" form is group 000 00010 with the same SHIFTIM field (inferred
from the kernel word 0x023E7800FF22 = `R2 = LSHIFT R2 BY -1`, used to halve a count).

VISA decoding (`sharc_dis.decode_visa` / `visa_stream`): instruction length comes from the
first parcel (1100 = 2c; 1001 + bits 36:34 = 16a/16b/15b/3c; bit 39 for 2b/17b/11c; 21c/22c
bit 32; 0x1901 = 25c) or from the `0111111` marker in bits 22:16 that replaces COMPUTE in the
32-bit b-forms. The first 4,000 parcels of the INIT_L16 program decode with no unknown
words and read as compiled C (register saves through `DM(I7,M7)`, frames via I6/I7,
`JUMP (M14,I12) (DB)` returns) [FW].

### SHARC simulator (`tools/sharc_sim.py`) [VM]

Own SHARC core: 48-bit ISA + VISA execution, fixed/float ALU, multiplier with MRF/MRB,
shifter, multifunction, DAGs with circular buffers and bit-reverse, delayed branches (two
slots), PC/loop/status stacks, DO UNTIL, interrupts through the vector table at 0x8C000
(4 instructions per vector), alternate register sets (MODE1 SRRF/SRD swaps), and SIMD
(PEy replay: S registers, ASTATY, next-word memory access). Memory is one store of 16-bit
parcels with the 2147x LW/48-bit/NW/SW windows; IOP registers below 0x40000 are pluggable.
The image is loaded straight from the parsed loader blocks (no boot-kernel emulation) and
starts at the reset vector 0x8C004.

Start-up order seen [VM]: ADI C-runtime start (DAG/stack setup for both register sets, C
stack 0xE2000..0xE3FFE), data init, SIMD enable, peripheral setup (IOP 0x1000.. SPI,
0x1084, 0x1C00.., 0x2000/0x2001, 0x4400.., SYSCTL 0x30024), a timed busy-wait (count to
0x5DC0), then heap and object set-up.

Conventions learned from the code [FW]:
- VISA calls: `CJUMP f (DB); DM(I7,M7) = R2; DM(I7,M7) = <last parcel of the call
  sequence>`; returns `I12 = DM(M7,I6); JUMP (M14,I12) (DB); RFRAME` with M14 = 1.
- The PC universal register reads the address of the executing instruction; delayed
  branches execute two slot instructions.
- Type 15b (VISA 32-bit `Ureg <-> DM|PM(data7, Ix)`): I bits 43..41, D 40, L 39, **G bit 37**
  (bit 38 is unused; type 16b has G at 37 too), UREG bits 29..23, DATA bits 22..16
  (verified against prologue/epilogue pairs and the heap-table lookup `PX = PM(3,I12)`).
- `PX = DM|PM(...)` on a 48-bit (instruction-width) address moves all 48 bits; PX2 is the
  upper 32. The run-time heap table at 0x8C100 (5-word entries, base 0xE0000, length
  0x2000) is read this way.
- Divide by zero is real: RECIPS of ±0 gives ±inf with AV set, the Newton steps turn it into
  NaN, and FIX/TRUNC of NaN gives 0xFFFFFFFF. The firmware does this once during init
  (`3072000 / 0` in the routine at 0x12C2A1) and carries on, so the simulator must not trap.
- S registers (PEy file) are used as ordinary Uregs even outside SIMD.
- Enhanced MODIFY (types 7/19): the destination I register is Is XOR IdIs
  (`I5 = MODIFY(I6,-17)` sets up a frame pointer); modifying Is in place breaks frames.
- SIMD: PEy's half of a post-modify access uses the I value from before the instruction
  (address + 1), not the already-incremented one.

Main loop [VM]: the firmware runs its complete init (~500,000 instructions: C runtime,
heap, malloc'd objects, filter tables, codec set-up) and enters its main loop at 0x128BC9,
which calls three service routines and then `IDLE` (0x13046F) until an interrupt.
(An earlier stop at ~62,000 instructions was `exit()` after malloc failed; that was the
type 15b G-bit decode bug, not the firmware waiting.) Peripherals it uses during init:
- SPI (IOP 0x1000, SPICTL 0x5205): **master**, 8-bit, transfer started by a TXSPI write —
  the DSP drives a local peripheral (one transfer during start-up).
- SPIB (IOP 0x2800, SPICTL 0x4206): **slave, DMA mode**, DMA set up at 0x2880..0x2884 — the
  link the ARM drives on RSPI3.
- TWI (IOP 0x4400): **I2C master to 7-bit address 0x18**, interrupt-driven (TWIIMASK 0x50 =
  master-complete + TX service; DPI_IMASK_RE bit 6; core P14I). Init sends 41 two-byte
  register writes (reg, value), e.g. 07=0A, 09=20, 0E=80, 25=E0, 26=10, 3A=2F, 47=80,
  48=0F, 00=00, 01=80, then repeats part of the set. The register numbers stay within
  0x00..0x4F; the only codecs in the service manual are two AK4951s, so this is most
  likely one of them [INF] (address not yet checked against the data sheet).
- Interrupt enables are built at run time (see PX below). An early reading of "LIRPTL = 0,
  nothing enabled" came from the simulator's too-narrow PX register, not from the firmware.
`Twi` and `Dpi` (DPI_IRPTL at 0x1C32, read-to-clear) in `sharc_sim.py` model this.

Interrupts and the audio heartbeat [FW/VM]:
- The vector address is 0x8C000 + 4 x *interrupt number*, not the latch bit. Numbers: IRPTL
  bits 0..16 -> 0..16; LIRPTL P6I..P13I -> 17..24; IRPTL P14I..P16I (bits 17..19) -> 25..27;
  LIRPTL P17I/P18I -> 28/29. The firmware's handler-registration routine (0x1313B4, table
  at 0xB201A, 6 words per entry) uses these same numbers. It builds `BIT CLR LIRPTL x` /
  `BIT CLR IRPTL y` instructions at 0x90748 at run time out of PX1/PX2 and calls them,
  so PX must be modelled exactly (64-bit; PX2 = 63..32, PX1 = 31..0; a 48-bit memory
  move uses PX[63:16]).
- Init registers number 0x13 = P8I -> 0x1256F9 and 0x19 = P14I (DPI) -> 0x128BF6. P8I
  defaults to SP4I, so **SPORT4 DMA completion is the DSP's audio heartbeat.**
- All eight SPORTs run chained DMA. Each TCB is a self-loop: an 8-word block, modifier 1,
  link with PCI set (interrupt every block). The chain pointer addresses the TCB's II word,
  and the TCB is read downward (II, IM, C, CP). Buffers live at 0xB87AA..0xB8829 (8 words
  each). SPCTL directions: SPORT1/2/6/7 transmit, SPORT0/3/4/5 receive [VM].
- With `Sports` (one block every N instructions) the P8I handler runs repeatedly
  (biquad cascades at 0x128842); between blocks the main loop wakes from IDLE, runs its
  services and idles again. Speed is about 200k instructions/s in CPython.
- Per block the firmware also starts the **FIR accelerator** (FIRCTL1 0x5000 = 0x101,
  chain pointer CPFIR 0x501C). It is not modelled, so filter outputs are zeros; that only
  affects audio content, not control flow [VM].

ARM <-> main-DSP link: **RSPI3** on the ARM (master, PIO), SPIB slave on the DSP [FW]
(correction: an earlier note said RSPI0. RSPI0 carries length-framed packets
`[L][type][L-2 bytes][FF]` to the DV DSP, see below.)
- Words are 4 bytes, big-endian, one chip-select frame each (ARM 0x20118E00 writes SPCR then
  four SPDR bytes; RX trigger level 4, SPBFCR = 0x32). Handshake: DSP FLAG2 -> ARM **P7_6**
  (PPR7 bit 6), high while the DSP has a status frame queued.
- ARM state machine (0x20118E68, timed by MTU2 TGI4D, GIC 162; receive ISR 0x2011913C on
  SPRI3, GIC 280): send a queued command word unless P7_6 is high; send `80000000` (status
  request); after the timer, if P7_6 is high, clock `FFFFFFFF` words and hand each received
  word to 0x20118FA4, which files it by its top nibble (1 = state, 8 = status bits, 0xF =
  version reply into 0x20498770 + 0x3C with flag +65).
- DSP side, **half-duplex, switched by the DSP**: flag DM(0xB2AF5) selects the mode; a
  main-loop service (0x126F28) applies changes.
  - RX mode (flag 0): SPIB receive DMA, 40-word ring at 0xB2C3E (chain TCB 0x386E5, SPICTL
    0x84206 = slave, DMA, enabled), FLAG2 cleared. 0x126EAA reads CSPIB (0x2882): words
    received = 0x28 - CSPIB; each SPI byte lands in its own DMA word; four make a command.
    0x126E20 dispatches on the top byte: 0x44 -> 0x126E97 (tuning value), 0x45 -> sets
    DM(0xB86D1), **0x80 -> 0x125B1E (set TX mode = status request)**, anything else -> a
    queue at 0xB2BFA, emptied by 0x125E17 through a handler table at 0xB2AFA indexed by the
    top byte (e.g. 0x02 -> 0x126415, 0xE0 -> 0x1259FD; most entries are 0x125C3B = ignore).
  - TX mode: 0x125C85 builds a frame in 0xB2C66 (40 words) with the status values that
    changed since the last frame, padded with `10000000`, starts a transmit DMA (chain TCB
    0x386E9, SPICTL 0x40202) and sets FLAG2. 0x125C5B waits until 4 x n + 9 words are out
    and SPIF = 1, then switches back to RX.
- **Version handshake** (this is what the boot popup checks):
  - `020000A0` (bits 5..4 = 2, one-shot flag 0xB2CB1) makes the DSP read three 8-character
    ASCII tags from its SPI flash through its own **SPI master** (IOP 0x1000, READ 03 +
    24-bit address, MSB first, chip select FLAG8 = FLAGS bit 16, low = selected): flash
    0x40000 -> 0xB2C90 ("30601120"), 0x180000 -> 0xB2D18 ("30001010"), 0x80000 -> 0xB2D20
    ("30001000"). A tag containing 0x00 or 0xFF is cleared (0x127326). The flash is the same
    SPI-NOR the ARM reads on RSPI4.
  - `E000000k`: bit 0 picks the half of the tag, bits 2..1 the table; the reply word is
    `F` + digit nibble + three ASCII characters, e.g. `F0313132`, which the ARM (0x2011955C)
    formats as "1.12" + "00".
- The ARM reads these in 0x201195B0: queue `E000000k`, then up to 18 status polls waiting
  for a class-F reply.

QEMU <-> simulator bridge (`tools/dsp_bridge.py`; `rspi_gen.c` RZA1H_RSPI3_SOCK /
RZA1H_RSPI3_SOCK_PIN=7_6) [VM]:
- Each byte the ARM clocks goes over a Unix socket as (flags, byte); flag bit 0 marks the
  first byte after an SPCR write (a new chip-select frame). The reply is (byte, line state):
  FLAG2 is applied to P7_6 at that moment.
- The simulator pauses while a word is in flight, so SPIB never changes direction inside a
  word.
- **Time sync.** The CPython SHARC is about 1000x slower than the real DSP. The bridge holds
  back the reply to the last byte of a word, which freezes the ARM's virtual clock, until
  the DSP main loop has run: 2 SPORT blocks after a command, 1 after a `80000000` poll and
  after the last byte of a status frame. Without this, replies arrive after the ARM's 18
  polls, or several version requests overwrite each other's reply.
- A command word that arrives while the DSP transmits is held and delivered later; an
  `FFFFFFFF` read word that arrives while it receives is not delivered.
- Other QEMU pieces the link needed: RSPI receive-trigger level (SPBFCR.RXTRG). Without it
  the ARM's receive ISR ran after the first byte of every word and split words. Also DMAC
  CHSTAT.END is bit 5 (bit 6 is TC; both are set at the end), and RSPI0 RX/TX DMA
  is modelled (the RX side completes after TX has clocked its bytes).
- Result (main DSP alone): the ARM accepts the main-DSP and FPGA versions, so the popup moves
  from "DSP/FPGA firmware is wrong version" to "DV DSP firmware is wrong version".
- SPIB transmit prefetch: the firmware ends a status frame when CSPIB shows 4n+9 words
  fetched (0x125C5B), but the ARM stops clocking at the first `10000000` filler word. On the
  chip the SPI DMA runs ahead of the clock (4-word DMA FIFO + TXSPI, [INF]), so `SpibLink`
  reports CSPIB 5 words ahead in transmit mode. Without that the DSP stays in transmit mode
  with P7_6 high, and the ARM drops its next queued command (0x2011EEA8 pops the command,
  then 0x20118E68 skips sending it while P7_6 is high).

ARM <-> DV DSP link: **RSPI0** on the ARM (master, DMA + PIO polls), C5517 **McSPI** slave
on the DV DSP; handshake DV GPIO15 -> ARM **P3_8** [FW]:
- DV side (DSP-B): McSPI interrupt on CPU vectors 9 and 11 (both -> 0x181F9; IER0 0x0AA0). On
  RX0_FULL it stores CH0RX into an 8 K ring (0x3B640, write index 0x3B638, read 0x3B63A); on
  TX0_EMPTY it sends the next byte of a 256-byte TX ring (0x3F640, write 0x3B63C, read
  0x3B63E) while 0x3F844 = 1, else 0xFF; when the ring empties it clears GPIO15
  (IODATAOUT1 0x1C0A bit 15). 0x1C15 = McSPI interrupt aggregation flag (W1C).
- Main loop (0x17C18 init once, loop 0x17C6E): link service 0x7D81 -> 0x7D66 -> parser
  0x7CFB (length byte 2..0xA5, 0xFF = idle) / 0x7C92 (packet body); replies queued by
  0x6D5A from pending bits 0x6A5A/0x6A5C (jump table 0x1FC24), built by 0x180F9 (append
  bytes with 0x180D6, then GPIO15 = 1, 0x3F844 = 1). GPIO16 (IOINDATA2 bit 0) high with
  both rings empty sends the DSP to sleep (0x17F6A).
- Packets: `[L][type][L-2 data][FF]` both ways. At boot the DV DSP queues a type-0 frame on
  its own: `16 00 "1.11-000" "1.41-000" 01 01 01 01 FF`; the ARM's `03 00 00 FF` asks for it.
  The ARM copies the two version strings to 0x203E1B67 (DV version slot, flag +71).
- The frame often starts while the ARM is still clocking a command (the DSP raised GPIO15
  earlier): the ARM finds the length byte inside its RX buffer (0x201FFF08) and DMAs the rest.
- `dv_bridge.py` + `c55sim.McSpi`: the same socket protocol (RZA1H_RSPI0_SOCK,
  RZA1H_RSPI0_SOCK_PIN=3_8); a byte's reply waits until the handler has read CH0RX and
  loaded the next TX byte; after the last byte of a packet the DV DSP runs 20 000 more
  instructions (its main loop parses and may queue a reply).
- Needed fixes on the way:
  - **c55sim postfix `xccpart`**: opcode 0x9E/0x9F gates the instruction *before* it
    (`mov #1,T0` / `xccpart !TC1` / `ret`). Prefix 0x96/0x97 gates the next one. Treating
    both as prefix skipped `ret`s and fell into the link reset, which is where the "periodic
    McSPI re-init" noted earlier came from.
  - c55sim interrupt entry follows the branch stored in the vector slot (IDA does not decode
    the slots).
  - QEMU RSPI: while an RX DMA from SPDR is armed, SPRI is not raised (the DMA takes the
    bytes). Otherwise the ARM's poll ISR stole the first byte of a DMA'd packet and the
    version arrived with two 0xFF bytes.

**Full-radio result (2026-11-22) [VM]:** with both bridges (`run705_gui.py --dsp`), the
boot reaches the main screen (14.100.00 USB, VFO A, S-meter) with **no version popup**: the
ARM accepts main-DSP, FPGA and DV-DSP versions that both DSP firmwares read from their own
flash and report over the emulated links. Known gaps: the second halves of the DSP-program
and FPGA tags sometimes get no reply (bridge timing). Audio paths (SPORT/I2S between the
ARM, both DSPs and the codecs) carry no samples yet. FIR accelerator not modelled.
