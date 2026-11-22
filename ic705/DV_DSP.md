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
| vectors 9 and 11 | `0x181F9` | **McSPI** interrupt (host link): RX0_FULL / TX0_EMPTY |
| XMT2/RCV2 = I2S2 | `0x17419` / `0x17455` | plain 2-word pass-through each way |
| all others | `0x18729` | default |

I/O blocks used: `0x1C00` system/PLL/GPIO, `0x2800` I2S0, `0x2A00` I2S2, `0x3500` **McSPI**
(C5517 data sheet table 6-16), `0x7000` SAR ADC. [FW]

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

## I2S0 stream roles [FW unless tagged]

| Direction | Channel | Ring (byte addr) | Consumer / producer | Meaning |
|---|---|---|---|---|
| RX | L | `0xA2C` ×1024 | `0x15C9D`, per sample, phase counter `word_41B2` cycling 0–9 | **FM discriminator, 48 kHz, 10 samples/symbol** → GMSK demod |
| RX | R | `0x222C` ×1024 | `0x158AA`, blocks of 6 → `0x14B7A` | 48 → 8 kHz audio → vocoder encoder (mic) [INF] |
| TX | R | `0x22C` ×1024 | GMSK modulator | FM modulation samples |
| TX | L | ring of `0xBA4` entries | — | decoded voice to speaker [INF] |

The I2S0 peer therefore supplies a raw discriminator and takes raw modulation — most
likely the main DSP [INF].

## Vocoder [FW]

D-STAR AMBE 3600x2400 codebooks (PRBA24/PRBA58/HOCb5–8, Q11) sit in this image at
`dsp.image.bin+0x1A256…0x1B456`. No AMBE+2 codebooks. See `DMR_FEASIBILITY.md` R1.

## Host link: McSPI slave on the ARM's RSPI0 [FW]

- DSP-B sets up the McSPI as a **slave** (MODULCTRL = 4), with channel 0 using 8-bit words
  (CH0CONF `0x0001_03C3`). IRQENABLE = TX0_EMPTY + RX0_FULL, and idle TX = `0xFF`
  (`0x18539..0x1858F`). The master is the ARM's RSPI0.
- ISR `0x181F9`:
  - On RX0_FULL it stores CH0RX into an 8 K ring at `0x3B640` (write index `0x3B638`,
    read index `0x3B63A`).
  - On TX0_EMPTY it sends the next byte of a 256-byte ring at `0x3F640` (write `0x3B63C`,
    read `0x3B63E`) while `0x3F844` = 1, else `0xFF`.
  - When the TX ring empties it clears GPIO15 (IODATAOUT1 `0x1C0A` bit 15). **GPIO15 is
    the "data ready" line to ARM P3_8.**
- Packets go both ways as `[L][type][L-2 data][FF]`.
  - Parser: `0x7CFB` (length 2..0xA5, `0xFF` = idle) and `0x7C92` (body).
  - Replies are queued by `0x6D5A` from pending bits `0x6A5A`/`0x6A5C` and built by
    `0x180F9`/`0x180D6`.
  - At boot the DSP sends `16 00 "1.11-000" "1.41-000" 01 01 01 01 FF` (DV version + region
    version). The ARM stores it at `0x203E1B67`.
- GPIO16 (IOINDATA2 bit 0) high with both rings empty sends the DSP to sleep (`0x17F6A`).
- TX voice frames enter a 64-entry queue of 7-word records (byte `0x5672`, write index
  `word_59F2`) via `0x12819`. The DSP drives GPIO `0x1C0B` bit 1 high when fewer than 10
  records remain (flow-control request) and low when one arrives. [FW]
- `0x7969` dispatches on a 16-bit value from the host-comms struct at byte `0x65FC`
  (+0xF2/+0xF3). Values seen: `0x0272 0x0300 0x0305 0x0414-0x0416 0x0440 0x04C1
  0x0701 0x0801 0x0812 0x0907 0x0C31 0x1070 0x1077 0x1230 0x2903 0x2974 0x2976 0xA517`.
  Their meaning is unknown and they are not yet tied to packet types. [FW values, INF
  meaning]
- Earlier this note named SCIF5 (clock-synchronous) as the candidate ARM side. That is
  superseded: SCIF5 stays silent during boot, and the link is RSPI0 ↔ McSPI, working in
  emulation [VM].

## Open

- Packet types beyond the version frame (commands, voice frames, D-STAR data), and the RSPI0
  bit rate.
- The I2S0 peer (SHARC or FPGA) and the gain after I2S0-R (R3).
