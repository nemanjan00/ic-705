# icom-re

Clean-room research on the Icom IC-705 firmware: component map, the D-STAR
digital-voice chain, and a DMR feasibility study.

Start with [`ic705/DMR_FEASIBILITY.md`](ic705/DMR_FEASIBILITY.md) (verdict) and
[`ic705/ARCHITECTURE.md`](ic705/ARCHITECTURE.md). Live state: [`ic705/STATE.md`](ic705/STATE.md).

No Icom firmware, decoded image or disassembly is in this repository. Supply your
own `705E140.dat` from Icom and decode it with
https://github.com/sq6emm/ic705-ic9700-ic905-bring-your-own-firmware to reproduce.

Tools: `tools/table_scan.py` (scale-invariant table search), `tools/arm_emu.py`
(Unicorn function-level emulator for the ARM main image).
