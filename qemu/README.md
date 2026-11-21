# IC-705 port of the rz-a1h QEMU machine

Base: https://github.com/jeskko/ic7300-homebrew `qemu-machine/` (MIT, (c) its authors),
commit `84fb65b`. `git apply ic705-port.patch` in that tree (it adds `src/mtu2g.c` and the
build/symlink entries), then run `qemu-machine/setup.sh`.

Run with `RZA1H_BOARD=ic705` (see `../tools/qemu705.py`). Board-specific behaviour:

| Change | File | Why (IC-705 evidence) |
|---|---|---|
| `RZA1H_BOARD=ic705` switch | `rza1h_debug.h` | gates everything below; IC-7300 default unchanged |
| SCIF3/SCIF5 IC-7300 responders off | `scif.c`, `rz_a1h.c` | they poke IC-7300 firmware RAM at fixed addresses |
| SCIF RX injection queue + sub-CPU status pusher (ch3) | `scif.c` | ARM RXI3 handler `0x20120CAC`: `FC off data… FE`, `FF` escape; 54-byte status mirror |
| ADC single conversion (ADST→ADF) + `RZA1H_ADC_VALS` | `adc.c` | `0x201185AC` polls ADCSR ADF; supply class from ADC in `0x2000B770` |
| general MTU2 model `mtu2g.c` | `mtu2g.c`, `rz_a1h.c` | TGI4A starts ADC (`0x20005C48`); TGRC_4 at `+0x228` (`0x20120C20`) |
| generic pin levels `RZA1H_PINS` / QOM `pins`; P3_9 idle high | `gpio.c` | POWER key = P3_9 active-low (`0x2011D6EC`) |
| deep-standby DSFR reset value `RZA1H_DSFR` | `rz_a1h.c` | power-state code reads DSFR bits 13/10/4/3 (`0x2007BB10`) |
| DMAC channels 8..15 (`dmac_hi.c`) | `dmac_hi.c`, `rz_a1h.c` | ch10 bit-bangs 332/204 bytes into `0xFCFE3118`, waited on at `0x2011F254` |
| generic RSPI0..4 (`rspi_gen.c`) | `rspi_gen.c`, `rz_a1h.c` | bring-up polls RSPI0 SPSR; RSPI3/4 also used |

Boot hook (tools/qemu705.py `--force-on`): the power-mode dispatcher `0x2007B7C0` is
entered with a1=3 (standby/charge) because the sub-CPU wake handshake is not modelled
yet; the hook sets a1=1 (full bring-up). Remove once the sub-CPU status frame carries a
proper power-on reason.

| SPI-NOR on RSPI4 (`RZA1H_RSPI4_FLASH`) | `rspi_gen.c` | DSP/FPGA config flash, 16 MiB, `0x2006A218` |
| sub-CPU P7_9 ack follows P7_11 request; P7_9 idle high | `gpio.c` | `0x20120960` handshake |
| sub-CPU version `1.20-E00`/`1.00-000`, live status file `RZA1H_IC705_SUBSTAT_FILE` | `scif.c` | `0x201211B4`, `0x2006C708` |

Status (2026-11-21, earlier): reaches the RTX idle task with the UI drawn (480x272): TX icon,
battery, mode tab, digits, BLANK label, and an error dialog (red X) -- text glyphs
missing, error presumably from the dummy DSP/FPGA.
