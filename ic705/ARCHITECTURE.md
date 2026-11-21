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
| Main DSP | "DSP Program 1.12 / DSP Data 1.00" — chip **not yet identified** | DSP group, blobs at `0x40000` / `0x80000` [INF] | [EXT: Icom version list] |
| DV DSP | TI C55x (C5505/C5515/C5535 class); version `1.11` | DSP group, boot table at `0x10000` | [FW] |
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
