#!/usr/bin/env python3
"""Build the DV-DSP 4FSK (DMR) modulator patch into a decoded DSP-group image.

  dv_patch_4fsk.py <stock dsp.image.bin> <patched dsp.image.bin> [level_amp]

Patch (all addresses are DV-DSP byte addresses unless "word"):
  gmsk_mod_bit 0x16B60:
    0x16B6D  amar *(#w0x7500), XAR3      ; level table
    0x16B73  mov *AR3(T0), AR3           ; AR3 = level[dibit]
    0x16B75  nop x27                     ; replaces bit->+/-dev selection + invert
    0x16B95  mov #50h, T1                ; 80 taps
    0x16B9B  amar *(#w0x7400), XAR1      ; RRC table
    0x16BA1  amar *(#w0x7480), XAR3      ; FIR delay line (82 words)
  New boot-table section at word 0x7400 (0x104 words): RRC taps, zeroed delay line,
  4-entry level table (index = dibit, ETSI mapping 01:+3 00:+1 10:-1 11:-3).
Every instruction encoding was checked by decoding it in IDA (TMS32055) and run in
tools/c55_emu.py.  The caller still passes D-STAR *bits* in T0; feeding dibits needs
the host-side change described in ic705/DMR_FEASIBILITY.md.  NOT for flashing as is.
"""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import dv_mod_poc as p

BOOT = 0x10000                     # DV-DSP boot table offset in dsp.image.bin
CODE = [(0x16B6D, 'ec31be007500'), (0x16B73, 'ab6b'), (0x16B75, '20' * 27),
        (0x16B95, '76005058'), (0x16B9B, 'ec319e007400'), (0x16BA1, 'ec31be007480')]

def sections(img):
    W = lambda o: struct.unpack_from('>H', img, o)[0]
    assert W(BOOT) == 0x09AA
    o = BOOT + 6; n = W(o); o += 2 + 4 * n
    secs = []
    while True:
        while W(o) == 0x2020: o += 2
        cnt = W(o)
        if cnt in (0, 0xFFFF): return secs, o
        addr = (W(o + 2) << 16) | W(o + 4); o += 6
        secs.append((addr, cnt, o)); o += 2 * cnt

def file_off(secs, byte_addr):
    for addr, cnt, src in secs:
        if 2 * addr <= byte_addr < 2 * (addr + cnt):
            return src + byte_addr - 2 * addr
    raise KeyError(hex(byte_addr))

def build(stock, amp=0x4800):   # 0x49F7 = worst-case no-clip bound
    img = bytearray(stock)
    secs, term = sections(img)
    for a, hx in CODE:
        b = bytes.fromhex(hx); o = file_off(secs, a)
        img[o:o + len(b)] = b
    words = [0] * 0x104
    for i, v in enumerate(p.q15_table(p.rrc(p.NT, p.SPS, p.ALPHA))):
        words[i] = v & 0xFFFF                          # word 0x7400..0x744F
    for d, L in p.DIBIT_LEVEL.items():
        words[0x100 + d] = round(amp * L / 3) & 0xFFFF   # word 0x7500..0x7503
    sec = struct.pack('>HI', len(words), 0x7400) + b''.join(struct.pack('>H', w) for w in words)
    end = term + len(sec) + 2
    if any(b != 0xFF for b in img[term + 2:end]):
        raise RuntimeError('no free space after the boot table')
    img[term:end] = sec + b'\x00\x00'
    return bytes(img)

if __name__ == '__main__':
    stock = open(sys.argv[1], 'rb').read()
    amp = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x4800
    out = build(stock, amp)
    open(sys.argv[2], 'wb').write(out)
    secs, _ = sections(out)
    print('patched; sections:', [(hex(a), hex(c)) for a, c, _ in secs][-3:])
