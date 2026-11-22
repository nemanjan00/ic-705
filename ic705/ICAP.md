# ICAP audio taps (IC-705 E 1.40, release 20261119v14)

ICAP is the SD-card app runtime in SQ6EMM's custom firmware (upstream
`sq6emm/icom-custom-firmware`). This note records where its audio API connects to the
stock radio, to judge whether DMR can be done as an ICAP app. Source: static reading of
the v14 output image decoded with `icom_rza1_decode.py` and diffed against stock, plus the
app binaries in the release bundle. Nothing vendor-owned is copied here.

## App API [FW]

- Apps (`*.APP`, magic `ICAP`, 0x40-byte header, ARM code, relocated at load) get a
  pointer to a function table at `0x2037ECD0`. Word 3 is the table size; the loader sets
  it to 0x90 for v3 apps (0x48 for older ones).
- `+0x54` `audio_read(int16 *buf, int n)`: copies mono int16 samples from an 8192-sample
  ring at on-chip RAM `0x2082C000`. Returns the count, or -7 after an overrun.
- `+0x58` `audio_rate()`: returns the constant **8000**. Apps refuse to run below 8000 Hz.
- TX: the runtime's write call (`0x20374314`) scales samples by the `txlevel` setting
  (0..0x7FFF, Q15) into a 512-sample ring. The cold module `ICAPCOLD.BIN` is mapped at
  `0x207F0000+` and holds more of the TX/UI logic.

## RX path [FW]

1. DMAC ch3 runs ping-pong on **SSIF0 receive** (`SSIFRDR` 0xE820B01C, DMARS source 0xE2),
   288-byte halves (36 stereo frames of 32 bits) at `0x20400800`. At 36 frames per
   0.75 ms this is 48 kHz.
2. Its end ISR (`0x200FFD54`) splits the frames: the **left** channel (top 16 bits) goes to
   an 8-slot queue at `0x204014A2`, and the right goes to `0x200FFAE0`.
3. The stock task at `0x2010FEE8` pops 36-sample blocks. ICAP hooks its call at
   `0x2010FF8C` (trampoline `0x20373A20`) and feeds each block to `0x2037D8B4`.
4. That routine decimates 48 kHz to 8 kHz with a 96-tap FIR (coefficients at
   `0x2037EBB0`) into the `audio_read` ring. While the radio transmits (flag `0x203806F4`
   bit 7) it marks an overrun instead.

So apps hear SSIF0-left, band-limited to below 4 kHz. That SSIF0's peer is the SHARC is
[INF]; the SSIF-to-chip mapping is not traced yet (`ARCHITECTURE.md`).
The SHARC's FM receive chain applies de-emphasis, a 3 kHz voice low-pass and a CTCSS
high-pass (`FM_AUDIO.md`); whether SSIF0 carries that output is still open. The README's "turn NR off for APRS" note suggests the tap is
after NR [EXT].

## TX path [FW]

- The stock routine `0x20100234` pulls 36-sample blocks from `0x201F5A00` into `0x20401E9C`
  and hands them to `0x200FF6AC`, the serial-audio transmit side (destination SSIF not yet
  traced). `0x201F5A00` is
  the PC-to-radio (USB "MOD") audio source [INF from context].
- ICAP installs `0x20373B00` in its place, through a function pointer kept at
  `0x203742F8`. That code upsamples the 8 kHz app ring to 48 kHz with a 6-phase, 6-tap
  polyphase FIR (taps at `0x2037EADC`). When ICAP is not keying, it falls through to the
  stock source.

ICAP TX therefore takes the same entry point as USB modulation audio and inherits
whatever the DSP's TX chain does to that input.

## Consequences for DMR [INF]

- 8 kHz in both directions gives only about 1.67 samples per DMR symbol (4800 baud) and
  under 4 kHz of bandwidth. 4FSK at ±1944 Hz deviation needs a flat response down to near
  DC, and the FM audio chain's high-pass and de-emphasis remove it.
- The hook points already carry 48 kHz audio (before the decimator, after the
  interpolator). A 48 kHz API is a small runtime change. The real blocker is the
  SHARC's FM filtering: de-emphasis/pre-emphasis, the CTCSS high-pass and the limiter.
  It is mapped in `FM_AUDIO.md` and can be flattened with a table patch, plus a small
  code patch for the limiter [INF].
