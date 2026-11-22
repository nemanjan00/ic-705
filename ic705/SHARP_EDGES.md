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

- **SHARC VISA type 15b has G at bit 37, not 38.** With G read from bit 38 a `PX = PM(3,I12)`
  became `DM(3,I4)`, malloc's heap lookup returned 0 and the firmware called `exit()` —
  which looked like "idle waiting for the host". An IDLE stop is not proof of a wait;
  check how it was reached (here: via `exit` 0x1302FE). [VM]
- **PX is 64 bits: PX2 = 63..32, PX1 = 31..0, and 48-bit memory moves use PX[63:16].**
  A 48-bit PX with a 16-bit PX1 mis-built the firmware's self-generated interrupt-mask
  instructions, so LIRPTL stayed 0 and the DSP looked like it had nothing enabled. [VM]
- **SHARC vectors go by interrupt number, not latch bit** (P14I is IRPTL bit 17 but
  vector 0x64). Using the bit sent P14I to the P6I vector. [VM]
- **Firmware divides by zero on purpose-free paths** (RECIPS 0 -> inf -> NaN -> FIX = all
  ones); a simulator that throws on NaN stops a firmware that runs fine on silicon. [VM]

- **"Which bus is the DSP on" from one handler name is not proof.** RSPI0's consumer was taken
  for the DSP link; its packet format (length-framed) does not match the SHARC's 4-byte
  words, while RSPI3 traffic matched the DSP's own dispatch table byte for byte. Match
  formats on both ends before wiring. [FW]
- **Status words are not proof of who sent them.** The RSPI3 replies (type nibble 1/8/9/F)
  were filed as "FPGA status" until the SHARC firmware was shown to produce exactly those
  frames. Identify the peer from its own firmware before naming a link. [FW]
- **"Unidentified register block" plus "chip has no X" is a trap.** The C5517 block at 0x3500
  was the McSPI; once identified from the data sheet's register table, the "DV DSP has no SPI"
  conclusion fell. Look up unknown I/O blocks before drawing link conclusions. [FW]
- **C55x `xccpart` has a postfix form** (0x9E/0x9F) that gates the *preceding* instruction;
  treating it as a prefix skipped `ret`s, and the DV DSP kept resetting its link. That looked
  like firmware behaviour ("periodic re-init"). Check simulator semantics before writing up
  odd firmware behaviour. [VM]
- **DMA vs. interrupt on the same receive-full event**: in hardware the DMA wins; a model
  that lets the CPU ISR run first steals bytes. [VM]
- **SPI DMA prefetch matters to firmware**: the SHARC firmware judges "frame sent" by the DMA
  count, which on silicon runs ahead of the clock. [INF]
- **A peripheral that raises "RX full" after one byte splits multi-byte words** when the
  real part waits for a trigger level (RSPI SPBFCR.RXTRG). [VM]
- **SPI-flash models must see chip-select edges without traffic** (FLAG8 goes high between
  READs with no byte clocked); resetting only on a byte clocked while deselected merges
  three READs into one. [VM]
- **A slow co-simulated chip needs time sync, not tweaks.** Holding QEMU's reply byte (the
  vCPU waits for it, so the guest clock stops) is the clean pause; QMP `stop` deadlocks
  while the vCPU holds the big lock inside the device callback. [VM]

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
