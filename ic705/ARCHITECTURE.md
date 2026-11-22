# IC-705 — system architecture (firmware 1.40 E)

## Chips and images

| Block | Identification | Image in `.dat` | Evidence |
|---|---|---|---|
| Main CPU | Renesas RZ/A1H, Cortex-A9 (ARMv7-A), XIP from 16 MiB QSPI (IS25LP128) at `0x18000000`; main image runs from RAM `0x20005000` | main domain, LZSS | [FW] [EXT: sq6emm docs] |
| RTOS | Keil RTX (CMSIS-RTOS v4, Cortex-A port): SVC handler at `0x20005690` dispatches `SVC 0` with target in R12; run/new TCB compare at `0x20381E90` | — | [FW] |
| Display | RZ/A1 VDC5 + LVDS; OpenVG/EGL at `0xE8100000`; libpng, libjpeg-turbo, FreeType, Iwata fonts | — | [FW] |
| SD card | FAT through a driver logging `GRP_FS` | — | [FW] |
| WLAN | TI SimpleLink CC31xx ("NWP" abort strings, `/sys/BTL_sflash.ptc`) | WLAN group | [FW] |
| Sub CPU (`aux2`) | **ST STM32L051R8** (Cortex-M0+), 64 KiB at `0x08000000`; SPI1 slave clocked by the ARM's SCIF3 | aux group | [FW], part [EXT: service manual] |
| `aux1` | 896-byte Thumb stub at `0x20000008` | aux group | [FW] |
| Main DSP ("IF-DSP", IC202) | **Analog Devices ADSP-21478** (SHARC 2147x) | DSP group: boot stream at flash `0x40008`, streamed in by the ARM over RSPI4; `0x80000` block read at run time [INF] | part [EXT: service manual], code [FW] |
| DV DSP (IC602) | **TI TMS320C5517** (C55x); version `1.11` | DSP group, boot table at `0x10000` | [FW], part [EXT: service manual] |
| FPGA | Intel/Altera (Cyclone IV E / 10 LP class), 2.79 Mbit RBF stored bit-reversed | DSP group `0x180000` | vendor [FW], part [INF] |

Version list from Icom's 1.40 notes [EXT]: Main 1.40, Sub 1.01, DSP Program 1.12,
DSP Data 1.00, FPGA 1.01, DV DSP 1.11.
The emulated sub-CPU firmware reports `1.20-E00` (adjust mode shows `S 1.20-E00`), which
does not match "Sub 1.01". What Icom's "Sub" number refers to is open [VM]/[EXT].

## The DSP group (`dsp.image.bin`, 2 MiB decoded)

| Offset | Content | Evidence |
|---|---|---|
| `0x000000` | C55x boot table, sig `0x09AA`, entry byte `0xF256`, 9 PLL/clock register writes (`0x1C00`–`0x1C23`), 5 sections in word range `0x7000`–`0x8000` → ~5 KiB loader stub ("DSP-A") | [FW] |
| `0x010000` | C55x boot table ("DSP-B" = **DV DSP**), entry byte `0xA9AC`, 11 sections, ~170 KiB. Contains UTF-16 `1.11-000` | [FW] |
| `0x040000` | `"30601120"` tag, then the **SHARC boot stream** from `0x40008`: 256-instruction kernel + ADI loader blocks (`tools/sharc_ldr.py`, `EMULATION.md`) | [FW] |
| `0x080000` | `"30001000"` tag (reported as the DSP **data** version) + 776 × (byte offset, 16-bit word count) table; 324 non-empty entries, ~830 KiB, entropy 7.1. Most likely SHARC data loaded through its SPI master at run time | tag [FW], contents [INF] |
| `0x180000` | `"30001010"` tag (FPGA version) + FPGA bitstream | [FW] |

The three 8-character tags are what the version check compares: the SHARC reads them
itself over its SPI master and reports them to the ARM (`EMULATION.md`) [FW]/[VM].

## DSP / FPGA flash and the update path [FW]

- The DSP group (2 MiB) lives in a **separate 16 MiB SPI-NOR on RSPI4** (`0xE800E800`).
  The ARM programs it during SD updates. At boot the ARM streams region 1 into the SHARC
  (`0x2011D3E0`) [FW]. The ARM never parses the C55x boot table (no `0x09AA` constant in
  the ARM image), so the DV DSP presumably boots from this flash itself through its ROM
  loader and the DSP-A stub [INF]. The SHARC also reads this flash with its own SPI master
  (chip select FLAG8) [FW], so the flash is shared between the ARM and the SHARC.
- Flash regions (updater table at `0x202E2218`): `0:[0x000000,0x040000)` DSP-A + DV DSP,
  `1:[0x040000,0x080000)`, `2:[0x080000,0x180000)`, `3:[0x180000,0x200000)` FPGA.
- Header bytes 28..43 hold four 4-byte region versions (`1.41 3.06 3.00 3.00` in E140).
  Region i is reprogrammed only if its version differs from the installed one
  (pre-scan `0x2006C708` sets mask bit i, writer `0x2006AB5C` gates on it), and then only
  the 64 KiB blocks whose read-back differs.
- **Autokey IVs = byte sum of the domain's header version field(s)**: main `hdr[4:12]`
  (E140: `0x9B`), aux `hdr[20:28]`, DSP `hdr[28:44]` (`0x0D`), WLAN `hdr[44:56]`; the
  counter starts at 75 (`0x4B`). The footer is a fixed constant, not a checksum.
- Updater: `0x2006DAB0` (whole `.dat`), helpers `0x20069AAC` (autokey), `0x2009A694`
  (MD5Update), `0x20069CC8` (streaming LZSS), `0x2006B78C` (sub-CPU over SCIF3,
  `"ICOM4075S"` + `0x55` handshake).

## C55x boot table format (as observed)

`u16 0x09AA`, `u32 entry (byte address)`, `u16 n`, `n × (u16 reg, u16 value)`
(`reg 0xFFFF` = delay), then sections `u16 word_count, u32 word_address,
data[word_count]` until a `0x0000` count. All big-endian. Sections are sometimes
preceded by `0x2020` words (C55x NOP padding of the previous section). [FW]

## Control links [FW]

| Link | ARM side | Peer | Format | Handshake |
|---|---|---|---|---|
| Main DSP control | RSPI3, master, PIO | SHARC SPIB, slave, DMA | 4-byte big-endian words; DSP switches half-duplex between receive and status-frame transmit | DSP FLAG2 → ARM P7_6 |
| Main DSP boot | RSPI4 (flash READ at `0x40008`) | SHARC SPI slave boot | the flash streams straight into the DSP while P7_6 is high | P7_7 = DSP up |
| DV DSP control | RSPI0, master, DMA + PIO | C5517 McSPI, slave | packets `[L][type][L-2 data][FF]` both ways | DV GPIO15 → ARM P3_8 |
| Sub-CPU | SCIF3, clock-synchronous | STM32L051 SPI1 slave | status frames `FC off data… FE` | P7_11/P5_3 out, P7_9/P5_4 in |
| Settings EEPROM | RIIC0 (writes) + bit-banged reads on P1_0/P1_1 | 24M02-class, 256 KiB | I2C 0x50..0x53 | — |
| Charger, RTC | RIIC1 | BQ2571x-style 0x6B, RX-8803 0x32 | I2C | RTC on IRQ0 |
| SHARC → codec | (SHARC TWI) | AK4951 at I2C 0x18 [INF] | register writes at init | — |

SCIF5 (clock-synchronous, ≈ 10 kbit/s) was the early candidate for the DV DSP link. It
stays silent during boot, and the DV link turned out to be RSPI0/McSPI [VM]/[FW].

## Audio links (partly known)

- **ARM serial audio:** SSIF0, SSIF3 and SSIF5, all DMA ping-pong with 288-byte halves
  (36 stereo 32-bit frames, 48 kHz) [FW].
  - SSIF0 receive is DMAC ch3 (left → task queue, right → `0x200FFAE0`).
  - SSIF0 transmit is DMAC ch4.
  - ICAP taps SSIF0 here (`ICAP.md`).
  - Which chip is on the far end of each SSIF is **open**.
- **SHARC:** all eight SPORTs run chained DMA. SPORT1/2/6/7 transmit and SPORT0/3/4/5
  receive [VM]. The SPORT4 block interrupt is the audio heartbeat that runs the filter
  chain [FW].
- **DV DSP:** I2S0 left in = discriminator-like 48 kHz stream to the GMSK demodulator;
  I2S0 right out = FM modulation samples; I2S2 is a pass-through each way [FW]. The I2S0
  peer is open (most likely the SHARC [INF]).

## Signal flow (current model)

```
antenna → RF → ADC → FPGA (DDC, IF filter?) ──► SHARC main DSP (filters, demod, AGC, audio)
                                                 │  SPORTs (48 kHz)
                     ┌───────────────────────────┼─────────────────────────┐
                     ▼                           ▼                         ▼
           DV DSP I2S0 (GMSK modem,      ARM SSIF0/3/5 (USB audio,     AK4951 codecs
           AMBE vocoder) [peer INF]      LAN audio, ICAP apps)         (speaker, mic)
                     │
                     └── McSPI ◄──► ARM RSPI0 (D-STAR framing, UI)
ARM RSPI3 ◄──► SHARC SPIB (control, status, versions)
```

The SPORT ↔ SSIF/I2S/codec mapping in this diagram is [INF]; only the control links are
[FW].

- The FPGA→main-DSP baseband is reported as **12 kHz wide at 36 kHz** sample rate
  [EXT: VA7OJ test report, quoted in sq6emm/icom-custom-firmware#6].

## Parts from the service manual [EXT]

Source: IC-705 service manual (S-15707XZ-C1, October 2020), supplied by the owner (not in
the repo). Inside-view labels and schematic part numbers:

| Ref | Function | Part |
|---|---|---|
| IC701 | main CPU | Renesas R7S721001 (RZ/A1H) |
| IC901 | sub CPU | ST STM32L051R8 (Cortex-M0+) |
| IC202 | IF-DSP (main DSP) | Analog Devices ADSP-21478 (SHARC) |
| IC602 | DV DSP ("fixed-point DSP") | TI TMS320C5517 |
| IC521 | FPGA | Intel Cyclone 10 LP 10CL025 |
| IC251, IC301 | 24-bit stereo codecs | AKM AK4951 |
| IC704 | 128 MB flash | — |
| IC553 | 16-Mbit serial flash | — |
| IC751 | real-time clock module | (RX-8803 in the VM) |
| IC753 | tri-axis accelerometer | — |
| IC1101 / IC1131 | WLAN / Bluetooth modules | — |
| IC111 | 8-channel 8-bit D/A converter | — |

The block diagram labels separate "IF-DSP BOOT UP" and "DVDSP BOOT UP" signals.
