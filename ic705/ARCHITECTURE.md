# IC-705 — system architecture (firmware 1.40 E)

## Chips and images

| Block | Identification | Image in `.dat` | Evidence |
|---|---|---|---|
| Main CPU | Renesas RZ/A1H, Cortex-A9 (ARMv7-A), XIP from 16 MiB QSPI (IS25LP128) at `0x18000000`; main image runs from RAM `0x20005000` | main domain, LZSS | [FW] [EXT: sq6emm docs] |
| RTOS | Keil RTX (CMSIS-RTOS v4, Cortex-A port): SVC handler at `0x20005690` dispatches `SVC 0` with target in R12; run/new TCB compare at `0x20381E90` | — | [FW] |
| Display | RZ/A1 VDC5 + LVDS; OpenVG/EGL at `0xE8100000`; libpng, libjpeg-turbo, FreeType, Iwata fonts | — | [FW] |
| SD card | FAT through a driver logging `GRP_FS` | — | [FW] |
| WLAN | TI SimpleLink CC31xx ("NWP" abort strings, `/sys/BTL_sflash.ptc`) | WLAN group | [FW] |
| Sub CPU (`aux2`) | Cortex-M, 64 KiB at `0x08000000`, RCC `0x40021000`, GPIO `0x5000xxxx` → STM32G0/L0 class | aux group | [FW] chip family [INF] |
| `aux1` | 896-byte Thumb stub at `0x20000008` | aux group | [FW] |
| Main DSP ("IF-DSP", IC202) | **Analog Devices ADSP-21478** (SHARC 2147x) | DSP group, program stream at flash `0x40008`, data at `0x80000`; booted by the ARM over RSPI4 | [EXT: service manual parts list] |
| DV DSP (IC602) | **TI TMS320C5517** (C55x); version `1.11` | DSP group, boot table at `0x10000` | [FW], part [EXT: service manual] |
| FPGA | Intel/Altera (Cyclone IV E / 10 LP class), 2.79 Mbit RBF stored bit-reversed | DSP group `0x180000` | vendor [FW], part [INF] |

Version list from Icom's 1.40 notes [EXT]: Main 1.40, Sub 1.01, DSP Program 1.12,
DSP Data 1.00, FPGA 1.01, DV DSP 1.11.

## The DSP group (`dsp.image.bin`, 2 MiB decoded)

| Offset | Content | Evidence |
|---|---|---|
| `0x000000` | C55x boot table, sig `0x09AA`, entry byte `0xF256`, 9 PLL/clock register writes (`0x1C00`–`0x1C23`), 5 sections in word range `0x7000`–`0x8000` → ~5 KiB loader stub ("DSP-A") | [FW] |
| `0x010000` | C55x boot table ("DSP-B" = **DV DSP**), entry byte `0xA9AC`, 11 sections, ~170 KiB. Contains UTF-16 `1.11-000` | [FW] |
| `0x040000` | `"30601120"` header, ~141 KiB, entropy ~6.6, no C55x signature | [FW], meaning [INF] |
| `0x080000` | `"30001000"` header + 776 × (byte offset, 16-bit word count) table; 324 non-empty entries, ~830 KiB, entropy 7.1 | [FW], meaning [INF] |
| `0x180000` | `"30001010"` header + FPGA bitstream | [FW] |

## DSP / FPGA flash and the update path [FW]

- The DSP group (2 MiB) lives in a **separate 16 MiB SPI-NOR on RSPI4** (`0xE800E800`).
  The ARM programs it during SD updates. At runtime the ARM never parses the C55x boot
  table (no `0x09AA` constant in the ARM image), so the DSPs presumably boot from this
  flash [INF].
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

## Signal flow (current model)

```
antenna → RF → ADC → FPGA (DDC, IF filter?, FM discr.?) ──┐
                                                          ▼
                                         main DSP (filters, demod, AGC, audio)
                                                          │ I2S, 48 kHz [FW on DV side]
                                                          ▼
                                       DV DSP (GMSK modem, AMBE vocoder)
                                                          │
                                         ARM (D-STAR framing, UI, ICAP apps)
```

- The FPGA→main-DSP baseband is reported as **12 kHz wide at 36 kHz** sample rate
  [EXT: VA7OJ test report, quoted in sq6emm/icom-custom-firmware#6].
- Who sits on the other end of the DV DSP's I2S0 (FPGA or main DSP) is **open** [INF].

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
