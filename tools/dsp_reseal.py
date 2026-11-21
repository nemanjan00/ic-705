#!/usr/bin/env python3
"""Decode / re-seal the DSP group of an IC-705 .dat (clean-room, from IC705_DAT_FORMAT facts).

Layout (all offsets derived from the header, LE32 fields):
  0x64                 main domain, f38 bytes, MD5 after it
  then aux1+aux2       f44 + f4c bytes, MD5 after them
  then DSP             f54 bytes (LZSS of an f58-byte image), MD5 after it
  then WLAN            f5c bytes, MD5, 2-byte footer
Each tail domain uses the autokey  plain = stored ^ (prev_plain + counter), counter
starting at 0x4B and incrementing mod 256; the first plaintext byte is an LZSS flag
byte 0xFF, which fixes the initial feedback.  MD5 is over the *stored* bytes.

  dsp_reseal.py decode  in.dat  dsp.image.bin
  dsp_reseal.py reseal  in.dat  dsp.image.bin  out.dat  [--region0-version 1.42]

Per-domain IV (initial autokey feedback) = byte sum (mod 256) of that domain's
version field(s) in the header [FW: ARM updater pre-scan 0x2006C708]:
  main  = sum(hdr[4:12])      aux = sum(hdr[20:28])
  DSP   = sum(hdr[28:44])     (four 4-byte region versions)   WLAN = sum(hdr[44:56])
The updater only reprograms DSP flash region i (0:0x000000 DSP-A+DV DSP, 1:0x040000,
2:0x080000, 3:0x180000 FPGA) when header version i differs from the installed one, so
a patched region must carry a new version string -> new IV.

The new stream is stored at its exact length (as Icom's files are: the stream
consumes the whole DSP field), header field 0x54 is updated, and the WLAN group and
footer move with it. The size law len = 0x64+f38+f44+f4c+f54+f5c+66 is re-checked.
"""
import hashlib
import struct
import sys

sys.path.insert(0, '/home/nemanjan00/icom')
from icom_rza1_decode import lzss_decompress        # noqa: E402  (sq6emm, MIT)
from ic_rza1_repack import lzss_compress            # noqa: E402

CTR0 = 0x4B


def layout(d):
    f = {o: struct.unpack_from('<I', d, o)[0] for o in range(0x38, 0x64, 4)}
    main_md5 = 0x64 + f[0x38]
    aux = main_md5 + 16
    aux_md5 = aux + f[0x44] + f[0x4C]
    dsp = aux_md5 + 16
    dsp_md5 = dsp + f[0x54]
    return {'dsp': dsp, 'dsp_len': f[0x54], 'dsp_out': f[0x58], 'dsp_md5': dsp_md5, 'fields': f}


def autokey_dec(stored, iv):
    out = bytearray(); prev = iv
    for i, c in enumerate(stored):
        prev = c ^ ((prev + CTR0 + i) & 0xFF); out.append(prev)
    return bytes(out)


def autokey_enc(plain, iv):
    out = bytearray(); prev = iv
    for i, p in enumerate(plain):
        out.append(p ^ ((prev + CTR0 + i) & 0xFF)); prev = p
    return bytes(out)


def iv_of(stored):
    """IV recovered from the stored stream (first plaintext byte is flag 0xFF)."""
    return ((stored[0] ^ 0xFF) - CTR0) & 0xFF


def dsp_iv_from_header(d):
    return sum(d[28:44]) & 0xFF


def decode(d):
    L = layout(d)
    st = d[L['dsp']:L['dsp'] + L['dsp_len']]
    if hashlib.md5(st).digest() != d[L['dsp_md5']:L['dsp_md5'] + 16]:
        raise ValueError('DSP MD5 mismatch')
    iv = iv_of(st)
    plain = autokey_dec(st, iv)
    img, used = lzss_decompress(plain, L['dsp_out'])
    return img, iv, used, L


def reseal(d, new_img, region0_version=None):
    img, iv, used, L = decode(d)
    if iv != dsp_iv_from_header(d):
        raise RuntimeError('header-derived IV does not match the stream')
    if region0_version is not None:
        v = region0_version.encode()
        if len(v) != 4:
            raise ValueError('region version must be 4 ASCII bytes, e.g. 1.42')
        d = d[:28] + v + d[32:]
        iv = dsp_iv_from_header(d)
    if len(new_img) != L['dsp_out']:
        raise ValueError('image size must stay %#x' % L['dsp_out'])
    comp = lzss_compress(new_img)
    if comp[0] != 0xFF:
        # first flag byte must be 0xFF (8 literals) for the IV rule; true for any image
        # whose first 8 bytes don't repeat, as with the C55x boot signature
        raise ValueError('first LZSS flag byte is not 0xFF')
    st = autokey_enc(comp, iv)
    out = bytearray(d[:L['dsp']] + st + hashlib.md5(st).digest() + d[L['dsp_md5'] + 16:])
    struct.pack_into('<I', out, 0x54, len(st))
    f = layout(out)['fields']
    if len(out) != 0x64 + f[0x38] + f[0x44] + f[0x4C] + f[0x54] + f[0x5C] + 66:
        raise RuntimeError('size law violated')
    # self-check: decode what we wrote
    img2, *_ = decode(bytes(out))
    if img2 != new_img:
        raise RuntimeError('re-sealed DSP does not decode back to the input image')
    return bytes(out), len(comp)


if __name__ == '__main__':
    cmd = sys.argv[1]
    d = open(sys.argv[2], 'rb').read()
    if cmd == 'decode':
        img, iv, used, L = decode(d)
        open(sys.argv[3], 'wb').write(img)
        print(f'DSP @0x{L["dsp"]:x} stored {L["dsp_len"]} -> {len(img)} bytes, iv=0x{iv:02x}, used {used}')
    elif cmd == 'reseal':
        new = open(sys.argv[3], 'rb').read()
        ver = sys.argv[sys.argv.index('--region0-version') + 1] if '--region0-version' in sys.argv else None
        out, n = reseal(d, new, ver)
        open(sys.argv[4], 'wb').write(out)
        print(f'wrote {sys.argv[4]}: DSP stream {n} bytes (slot {layout(d)["dsp_len"]}), MD5 resealed')
