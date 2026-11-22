# IC-705 port of the rz-a1h QEMU machine

Base: https://github.com/jeskko/ic7300-homebrew `qemu-machine/` (MIT, (c) its authors),
commit `84fb65b`. `git apply ic705-port.patch` in that tree (it adds `src/mtu2g.c`, `dmac_hi.c`, `rspi_gen.c`,
`stm32l0.c/.h` and the build/symlink entries), then run `qemu-machine/setup.sh`.

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

Boot hook (tools/qemu705.py `--force-on`): only for runs *without* the sub-CPU firmware.
It sets a1=1 (full bring-up) at the power-mode dispatcher `0x2007B7C0`. With
`RZA1H_SUBCPU_FW` set, the real POWER-key path works and no hook is needed.

| SPI-NOR on RSPI4 (`RZA1H_RSPI4_FLASH`) | `rspi_gen.c` | DSP/FPGA config flash, 16 MiB, `0x2006A218` |
| sub-CPU P7_9 ack follows P7_11 request; P7_9 idle high | `gpio.c` | `0x20120960` handshake |
| sub-CPU version `1.20-E00`/`1.00-000`, live status file `RZA1H_IC705_SUBSTAT_FILE` | `scif.c` | `0x201211B4`, `0x2006C708` |
| STM32L0 sub-CPU SoC (`stm32l0.c`), wired when `RZA1H_SUBCPU_FW=<aux2.application.bin>` | `stm32l0.c`, `rz_a1h.c`, `scif.c`, `gpio.c` | sub-CPU firmware drives SPI1 slave on SCIF3 + handshake lines (see `../ic705/EMULATION.md`) |
| deep-standby cancel on POWER (`RZA1H_DSWAKE_BIT`, default 13; use 10) | `rz_a1h.c` | `0x20078278` enters deep standby; loader `0x20004840` handles DSFR bit 10 |
| GPIO output/pin hooks, `rza1h_gpio_set_input` | `gpio.c` | handshake lines P7_11/P5_3 out, P7_9/P5_4 in |
| SCIF TX hook + sync-mode RX clocking | `scif.c` | SCIF3 is the SPI master clocking the sub-CPU |
| M24M02 EEPROM on RIIC0 + bit-banged read slave (`RZA1H_IC705_EEPROM`) | `m24m02.c`, `rz_a1h.c`, `gpio.c` | writes via RIIC0, reads bit-banged on P1_0/P1_1 (`0x2005E2DC`) |
| BQ2571x charger on RIIC1 0x6B | `bq2571x.c` | `0x20019B28`, `0x2001A174` |
| TSC2046 touch on port 3 (`touch` property) | `rz_a1h.c`, `dmac.c` | `0x2008D830`, `0x2008E524` |
| MTU2 phase counting for the dial (`dial` property) | `mtu2g.c` | TMDR_1 = 0x04 |
| RSPI RX trigger level (SPBFCR.RXTRG), TX-empty IRQ | `rspi_gen.c` | RSPI3 receive ISR `0x2011913C` expects whole words |
| RSPI socket devices: `RZA1H_RSPI<n>_SOCK`, `RZA1H_RSPI<n>_SOCK_PIN=<port>_<bit>` | `rspi_gen.c` | RSPI3 = main DSP (`../tools/dsp_bridge.py`, pin 7_6); RSPI0 = DV DSP (`../tools/dv_bridge.py`, pin 3_8) |
| RSPI0 SPDR DMA (RX deferred until TX clocked; SPRI suppressed while RX DMA armed) | `dmac.c`, `rspi_gen.c` | DV link `0x201FFF08` |
| DMAC CHSTAT.END = bit 5 (plus TC bit 6) | `dmac.c`, `dmac_hi.c` | firmware busy-waits on bit 5 |

Status (2026-11-21, earlier): reaches the RTX idle task with the UI drawn (480x272): TX icon,
battery, mode tab, digits, BLANK label, and an error dialog (red X) -- text glyphs
missing, error presumably from the dummy DSP/FPGA.

Status (2026-11-22): real POWER-key power-on with the real sub-CPU firmware reaches the main
screen. With both DSP bridges (`../tools/run705_gui.py --dsp`) there is no version popup
(see `../ic705/EMULATION.md`). The FPGA is a dummy, and the audio ports (SSIF) carry no
samples.
