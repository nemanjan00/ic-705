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

## Container / re-seal

- **Padding a shorter DSP stream to the old slot size breaks sq6emm's decoder**
  ("Unexpected unconsumed bytes in dsp"). Icom's tail streams fill their field exactly.
  Store the new stream at its exact length, update header `0x54`, shift the WLAN group,
  and re-check the size law. [FW]

## Emulation / DSP numerics

- **RRC 4FSK clips at amp 0x5000** for some dibit sequences: the worst-case polyphase
  gain is 1.73, so the no-clip bound is amp ≤ `0x49F7`. A clean random test passing is
  not proof; compute the bound. [VM]
- **No C55x assembler available** (TI's public CGT URL returns "Not found"; binutils
  has only C54x). Assemble by example from encodings already in the image, and verify
  every new instruction by letting IDA decode it before running it.
- **DMR sync detection alone doesn't prove 4-level correctness**: sync words use only
  ±3 symbols. Use dibit-exact recovery as the evidence.

## Updater / delivery

- **A patched DSP region with an unchanged version string is silently not flashed.**
  The updater reprograms DSP region i only if header version i (`hdr[28+4i:32+4i]`)
  differs from the installed one. Bump it. [FW+VM]
- **Bumping a version changes the IV** (IV = byte sum of the domain's version fields). A
  stream sealed with the old IV still passes the MD5 (over stored bytes) but decodes to
  garbage, and the updater then overwrites the DSP boot stub. Always re-encrypt after
  touching the header (`dsp_reseal.py --region0-version` does). [VM case D]

- **ISA identification by opcode signatures can give false negatives** [FW]: the IC-705
  main-DSP stream is bit-reversed per byte and packs 48-bit SHARC instructions 2-per-3
  32-bit words. Five architectures tested "negative" (SHARC included) until the part number
  came from the service manual and the packing was worked out. Check bit order, word size
  and packing before trusting a negative.
