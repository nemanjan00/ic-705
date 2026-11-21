# IC-705 — sharp edges (read BEFORE working)

Symptom → cause → fix. Append the moment one bites.

## Tools

- **IDA refuses the C55x module** ("processor type is not included in the installed
  version") → wrong short name → use `TMS32055` (names are listed in
  `~/.idapro/proccache.lst`).
- **rizin C55x shows `*(0x6D656D)` / `*(0x00026D)` for absolute operands** → plugin
  prints a placeholder ("mem") instead of the k16/k23 value → use IDA, or decode the
  instruction bytes by hand (`EC31 9E00 FE69` = `amar *(#0x00FE69), xar1`).
- **zsh doesn't word-split `$var`** in `for r in "a b"; set -- $r` loops → use
  Python or `${=r}`.
- **IDA data symbols in the C55x DB are byte addresses** (`word_1FCD2` = word
  `0xFE69`); boot-table section addresses are word addresses.

## Firmware

- **"DSP-B" looked like the main DSP — it is the DV DSP.** Icom's version list
  ("DV DSP: 1.11") matches the `1.11-000` string inside it. Check vendor version
  lists before naming a component. [EXT+FW]
- **Smooth tables give false positives** in `table_scan.py` (any ramp fits W0/L/Dg);
  only codebook tables (PRBA/HOC) count as evidence.
- **The `0x80000` DSP-group blob is not LZSS and not the container autokey**; LZSS
  "succeeds" on random data (it always emits N bytes), so judge by output entropy,
  not by absence of errors.
- **mbelib naming is swapped-looking:** `ambe3600x2400_const.h` (D-STAR) holds tables
  called `AmbePlus*`; `ambe3600x2450_const.h` (AMBE+2/DMR) holds `Ambe*`. Label by
  file, not by symbol name.
