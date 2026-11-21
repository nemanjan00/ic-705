# Methodology — IC-705 firmware

One loop: hypothesis → cheapest experiment that can disprove it → update docs.
Facts live in `ic705/`; pitfalls in `ic705/SHARP_EDGES.md`; state in `ic705/STATE.md`.

> Clean-room rule: no vendor binary, decoded image, disassembly or IDA database is
> committed. Facts are in our own words.

## 1. Container → components
Decode `705E140.dat` with sq6emm's tools (autokey XOR + Okumura LZSS, unkeyed MD5 per
domain). Identify each component by architecture fingerprints (ARM vectors, C55x
boot-table signature `0x09AA`, Cortex-M vector table, FPGA sync after bit reversal).
**Cross-check names against the vendor's version list** before trusting them.

## 2. Find the interesting code by its constants
Signal-processing code is easiest to find through its tables. Fit known shapes
(GMSK pulse, windowed sinc, codec codebooks) scale-invariantly; then xref the table
address in IDA to land on the routine. Always carry a positive control (a table that
must be there) so a negative result means something.

## 3. Follow the data
From a known routine, follow its ring buffers to the ISR that drains them, and from
the vector table to the peripheral. Name everything in the IDA DB as you go.

## 4. Emulate small, with a positive control
Run single firmware functions in Unicorn with logged MMIO. Prove the harness on a
function with a known answer (zlib CRC32) before trusting any other trace.

## Pitfalls that generalize
- A component's role inferred from code alone can be wrong; vendor version strings
  settle it cheaply.
- Decompressors "succeed" on random data; judge by output statistics.
- Smooth lookup tables match anything monotonic; use high-entropy tables as evidence.
