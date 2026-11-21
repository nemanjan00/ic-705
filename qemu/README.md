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
