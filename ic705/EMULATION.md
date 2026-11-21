# IC-705 emulation

Everything here is **[VM]**: it shows what the firmware code does in isolation, not
what the radio does.

## Layers

| Layer | Status | Tool |
|---|---|---|
| ARM function-level (call one firmware function, log MMIO) | **working** | `tools/arm_emu.py` (Unicorn 2.1.4) |
| ARM full-system (boot RTX, UI) | not started | QEMU RZ/A1 machine from jeskko/ic7300-homebrew [EXT] is the starting point; needs VDC5, GIC, OSTM, SCIF models |
| DV DSP (C55x) | not started | no ready-made C55x emulator; a focused interpreter for the instructions used by `gmsk_mod_bit`/`fir` would let us test a 4FSK table swap |
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
