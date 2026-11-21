# IC-705 DV DSP (C55x, image "DSP-B", version 1.11)

Addresses: **byte** addresses for code (C55x program space), **word** addresses for
data unless written "byte". IDA shows data symbols at byte address = 2 × word.

## Interrupt vectors [FW]

Vector table at byte `0x100` (word `0x80`), 32 × 8 bytes; bytes 1–3 of each slot are
the 24-bit handler address.

| Vector (C5505 naming) | Handler | Role |
|---|---|---|
| RESET | `0xA9AC` | entry |
| INT0 | `0x186E8` | sets a flag word |
| UART | `0x186C5` | masks itself, trivial |
| PROG0 = I2S0 TX | `0x17487` | sends L = `word 0x20D8`, R = `word 0x20D7` (byte syms `0x41B0`/`0x41AE`); drains the FM TX ring into R |
| PROG1 = I2S0 RX | `0x17714` | L hi16 → ring at byte `0xA2C` (1024 words), R hi16 → ring at byte `0x222C` (1024 words); each gated by its own enable |
| PROG2/3 = I2S1 | `0x181F9` | also touches the unknown `0x35xx` I/O block |
| XMT2/RCV2 = I2S2 | `0x17419` / `0x17455` | plain 2-word pass-through each way |
| all others | `0x18729` | default |

I/O blocks used: `0x1C00` system/PLL, `0x2800` I2S0, `0x2A00` I2S2, `0x3500` unknown,
`0x7000` SAR ADC (matches C5505/C5515/C5535). [FW]

## D-STAR GMSK transmit [FW]

```
dstar_tx_bit_pump  0x1575F
  gmsk_mod_bit     0x16B60   bit → ±dev (dev word at byte 0x65D8, init 0x6000)
    fir            0xAAC8    32 taps, 10 outputs per bit, table at byte 0x1FCD2
  each output + tx_fm_offset (byte 0x41A0)  → tx_fm_ring (byte 0x22C, 1024 words)
isr I2S0 TX        0x17487   ring → I2S0 right channel
```

- `gmsk_mod_init` (`0x16BAF`) sets dev = `0x6000`, a parameter `0x2AF8`, and
  samples/bit = `10`.
- **Pulse table**: 32 × s16 at file offset `dsp.image.bin+0x19DF2`. Least-squares fit
  to the GMSK frequency pulse (Gaussian ⊗ 1-bit rect): **BT = 0.50, 10 samples/bit**,
  RMS error 29/32767. → 4800 bit/s × 10 = **48 kHz** sample rate.
- Steady-state FIR gain for a constant bit = 1.066 (sum of every 10th tap / 32768,
  identical for all 10 phases) → peak sample ≈ 1.066 × dev ≈ 26 200 (0.80 FS).
- "Primed" flag is set once the ring holds `0x39C` samples.

## D-STAR receive hints [FW]

- 21-tap windowed-sinc at byte `0x1FAD4` (file `+0x19BF4`), zero crossings every
  10 samples → Nyquist/matched filter for 4800 Bd at 48 kHz.
- Strings `DIRECT  DIRECT` (simplex RPT1/RPT2 defaults) and `0x55` sync patterns.
- Two more windowed-sinc lowpass tables nearby (file `+0x19752`, period ≈ 8).

## Open

- Where the vocoder lives (here, or in the `0x40000` blob) — see `DMR_FEASIBILITY.md`.
- What the I2S0 RX streams are (discriminator vs audio), and who the I2S0 peer is.
- The `0x35xx` I/O block.
