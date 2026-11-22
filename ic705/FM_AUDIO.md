# FM audio chain in the main DSP (SHARC)

Static reading of the SHARC boot stream (`tools/sharc_ldr.py`, `tools/sharc_dis.py`,
VISA code at `0x124354+`). It answers the question: can the analog FM path be made
flat (no de-emphasis/pre-emphasis, tone filters or limiter) by patching the DSP?
Addresses are SHARC word addresses. Nothing vendor-owned is copied here.

## Filter engine [FW]

- Filters are cascaded biquads described by a 4-word descriptor:
  `[coef_ptr, gain, n_sections, flags]`.
- `0x128A63 init(obj=R4, desc=R8)` copies the coefficients into the object.
  `0x128931 reset(obj)` clears it. `0x12880B run(obj=R4, in=R8, out=R12)` filters
  4 samples at a time.
- Per section the coefficients are `c0..c3` and the state is two delay words:
  `w = x + c0*w[n-2] + c1*w[n-1]`, `y = w + c2*w[n-2] + c3*w[n-1]`
  (b0 = 1, signs folded in). The input is first multiplied by the descriptor gain.
- **All coefficients zero ⇒ the filter is a plain gain.** This is the data-only way
  to flatten a stage.
- The coefficients are 32-bit floats in the initialised data (INIT_L32 blocks), so a
  patch changes table words only, with no code edits.

Responses below assume the audio stages run at 48 kHz [INF]. At 48 kHz each table has
the shape expected of a voice FM chain. At 12 kHz they make no sense (e.g. a 1 kHz
voice low-pass). TX tones are explicitly upsampled ×4 before they join the audio
(see below).

## RX: discriminator → audio [FW], responses [INF]

Function around `0x12BA0x`–`0x12BD7x` (input I/Q block in R4, audio out R8):

1. An 8-tap FIR on the I/Q pairs, then a **phase-difference discriminator** that
   wraps to ±π (constants π, 2π, π/2). Running state is at `0xB4D04`/`0xB4D05`.
2. Filter `obj 0xB4B03`, desc `0xB4BD4`, 8 sections. **Voice band + de-emphasis**:
   peak near 300 Hz, about −6 dB/octave to 3 kHz, then a steep cut (−54 dB at 3.5 kHz).
3. One of two **CTCSS-reject high-pass** filters, selected by bits 17/18 of
   `DM(0xB2C02)`:
   - `obj 0xB4B0D` / desc `0xB4BE8`: about 300 Hz, notch near 250 Hz.
   - `obj 0xB4B17` / desc `0xB4BFC`: about 200 Hz.
4. Gain ×20.03, written to the output block.
5. An optional extra high-pass (`obj 0xB4C70`, desc `0xB4C4C`, deep notch ≤ 150 Hz)
   when `DM(0xB4910)` ≠ 0.

## TX: audio → modulator [FW], responses [INF]

The chain function is at `0x12EB51` and runs in this order:

| Step | Routine | What it does |
|---|---|---|
| 1 | `0x12E5CF`, `0x12E01B` | Input conditioning (not traced) |
| 2 | `0x12E610` | High-pass about 70 Hz (`obj 0xB8A56`, desc `0xB8CEA`) |
| 3 | `0x12E7ED` | **Pre-emphasis + 3.3 kHz low-pass** (`obj 0xB8A60`, desc `0xB8CFA`): +6 dB/oct from 300 Hz to 3 kHz, −25 dB at 6 kHz. Applied **only when the mode field (bits 16–23 of `DM(0xB2C0C)`) is 0xB or 0xC**; otherwise the samples are copied unchanged |
| 4 | `0x12E835` | **Look-ahead peak limiter**: max of `|x|` over a 256-sample window, gain toward 0.85. Applied for modes 0xA/0xB/0xC (or when bit 4 of the field is clear); otherwise a straight copy |
| 5 | `0x12EA91` | Adds three tone/aux sources (`0x12F41D`, `0x12F607`, `0x12F64F`, probably CTCSS/DTMF/tone [INF]). They are made at 12 kHz and upsampled ×4 through a 5 kHz low-pass (`obj 0xB8A6A`) **after** the voice filters |
| 6 | pointer at `DM(0xB8CFE)` | Then `0x12E4AC`, `0x12E6DB`, `0x12EAF7`: modulator side, not traced |

Which modes 0xA/0xB/0xC are (AM, FM, FM-N, or DV?) is not mapped [INF].

## What a flat patch would look like [INF]

- **RX:**
  - Zero the coefficients of desc `0xB4BD4` (8 sections) and both CTCSS high-pass
    tables. Set their gains to keep the level, about the old passband gain (−14 dB
    for the 8-section filter).
  - The output then is the raw discriminator ×20. It includes noise and the 4FSK
    content to well above 3 kHz.
  - Data-only.
- **TX:**
  - Zero the 70 Hz high-pass and the pre-emphasis tables. The firmware's own bypass
    branch in step 3 also shows that skipping pre-emphasis is a supported path.
  - The limiter needs a small code patch: the mode compares at `0x12E876`/`0x12E8DB`
    must fall through to the copy.
  - Without the limiter, deviation is set only by the input level. Safe for data at a
    calibrated level; it can over-deviate on voice.
- **Better:** gate both behind a spare setting bit, so normal FM voice stays as it is.
  That means code changes, because the tables are shared by every use of that mode.

## Still open

- Confirm the 48 kHz rate in the simulator: feed a known I/Q tone into the RX
  function and a tone into the TX chain in `sharc_sim.py`, and check gains.
- Squelch path: does it read the discriminator before or after these filters?
- Where the RX output block goes (which SPORT, and whether it is the ARM's SSIF0) and
  what the ARM/USB side does to it afterwards.
- The modulator's deviation scale (R3 in `DMR_FEASIBILITY.md`), which a flat TX path
  still needs.
- Re-sealing: the SHARC boot stream must be re-encoded and its version tag changed
  so the updater rewrites it. This is the same method as the DV DSP re-seal.
- Whether a radio with a non-booting SHARC can still be reflashed. Not known; must
  be answered before any [HW] test.
