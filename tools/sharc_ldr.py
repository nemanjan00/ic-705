#!/usr/bin/env python3
"""Decode the IC-705 main-DSP (ADSP-21478 SHARC) boot stream from the DSP flash image.

Encoding (ic705/EMULATION.md "Main DSP"): region 1 starts at flash 0x40000; the stream the
ARM clocks into the DSP starts at 0x40008. Every byte is stored bit-reversed; bytes form
32-bit little-endian words; 48-bit instructions are packed two per three words
(a, b, c) -> (b & 0xFFFF) << 32 | a and c << 16 | b >> 16. The first 384 words are the
256-instruction boot kernel; then ADI loader blocks follow, each a (tag, count, address)
header: 0 FINAL_INIT, 1 ZERO_LDATA, 2 ZERO_L48, 3 INIT_L16, 4 INIT_L32, 5 INIT_L48,
6 INIT_L64.

  sharc_ldr.py dsp.image.bin            # list blocks
  sharc_ldr.py dsp.image.bin --kernel   # dump the kernel as 48-bit hex words

No vendor data is stored here; the image is read at runtime.
"""
import argparse

TAGS = {0: 'FINAL_INIT', 1: 'ZERO_LDATA', 2: 'ZERO_L48', 3: 'INIT_L16', 4: 'INIT_L32',
        5: 'INIT_L48', 6: 'INIT_L64'}
REV = bytes(int(f'{b:08b}'[::-1], 2) for b in range(256))


def stream_words(img, start=0x40008, end=0x80000):
    d = bytes(REV[b] for b in img[start:end])
    return [int.from_bytes(d[i:i + 4], 'little') for i in range(0, len(d) - 3, 4)]


def unpack48(ws):
    out = []
    for i in range(0, len(ws) - 2, 3):
        a, b, c = ws[i], ws[i + 1], ws[i + 2]
        out += [((b & 0xFFFF) << 32) | a, (c << 16) | (b >> 16)]
    return out


def blocks(ws, i=384):
    """Yield (tag, count, addr, payload_words) until FINAL_INIT or an unknown tag."""
    while i < len(ws) - 3:
        tag, cnt, addr = ws[i] & 0xFF, ws[i + 1], ws[i + 2]
        i += 3
        # INIT_L48: the kernel fetches 3 words per pair of instructions, rounding up
        n = {1: 0, 2: 0, 3: (cnt + 1) // 2, 4: cnt, 5: (cnt + 1) // 2 * 3, 6: cnt * 2}.get(tag)
        if tag == 0:
            yield tag, cnt, addr, ws[i:i + 384]
            return
        if n is None:
            raise ValueError(f'unknown loader tag {tag:#x} at word {i - 3}')
        yield tag, cnt, addr, ws[i:i + n]
        i += n


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--kernel', action='store_true')
    a = ap.parse_args()
    ws = stream_words(open(a.image, 'rb').read())
    if a.kernel:
        for n, w in enumerate(unpack48(ws[:384])):
            print(f'{n:3d} {w:012x}')
    else:
        for tag, cnt, addr, pl in blocks(ws):
            print(f'{TAGS.get(tag, "?"):11} count {cnt:#7x} addr {addr:#08x} payload {len(pl)} words')
