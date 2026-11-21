# icom-re — IC-705 internals & DMR feasibility

Clean-room research notes on the Icom IC-705 firmware: what each chip does, how
the digital-voice path works, and whether DMR (or another 4FSK mode) can be added.

## Conventions

- **Layout: convention A** (per-target folder). Tracked findings live in `ic705/`.
  `docs/`, `out/`, `fw/` are gitignored and hold vendor material / decoded images.
- **Nothing from Icom is committed**: no `.dat`, no decoded image, no disassembly,
  no IDA database. Facts are written in our own words; addresses, sizes, constants
  and hashes are fine.
- **Read `ic705/STATE.md` first** (resume point), then `ic705/SHARP_EDGES.md`.

## Evidence tags

- **[FW]** read directly from the firmware bytes / disassembly (static, exact).
- **[HW]** measured on a real radio.
- **[VM]** observed in emulation only — not a device fact.
- **[EXT]** from an outside source (Icom notes, test reports, other projects).
- **[INF]** inferred / unverified.

## Inputs (re-fetch per machine, never commit)

- Official firmware: `705E140.dat` (MD5 `2c9c1d7f3fc80ef173e01d841cd1248a`),
  from https://www.icomjapan.com/support/firmware_driver/4422/
- Container decoder: https://github.com/sq6emm/ic705-ic9700-ic905-bring-your-own-firmware
  (`ic705_decode.py`), local copy under `../tools-re/`.
- Decoded components on this host: `../out/705E140_full/` (gitignored, outside repo).

## Tools

- IDA 9.4 via the ida-mcp plugin. C55x processor short name is **`TMS32055`**.
- rizin `-a tms320 -e asm.cpu=c55x` works for flow but garbles absolute operands.
- Emulation: see `ic705/EMULATION.md`.
