#!/usr/bin/env python3
"""Build a flat 16 MiB IC-705 QSPI-flash image for the rz-a1h QEMU machine (-kernel).

flash[0 : 0x600000]          = decoded main-domain body [0:0x600000] (bootstrap, loader,
                                main LZSS) -- byte-equal per sq6emm's flash notes [EXT]
flash[0xC00000 : 0xFC0000]    = body[0x600000 : 0x9C0000] (resource region). [FW] The
                                firmware reads a length-prefixed block at 0x18C00000 whose
                                end is exactly the next block at 0x18FA2588 (0x201049F0);
                                header field 0x3C = 0xC00000 names this base.
flash[0x600000 : 0xBF0000]    = slot B, erased. The loader (0x200045F4) boots slot B at
                                0x18600000 only if 0x18BF0000 holds "SX4075 V0.10-000";
                                that tag is left erased so slot A at 0x18010000 boots.
flash[0xFC0000 : 0x1000000]   = not in the .dat (factory block, "Icom Inc 2019" tag at
                                0x18FFFFF0, checksum over 0x3FC00 bytes, 0x20090FA4): left
                                erased unless --factory FILE is given.
Everything else is erased flash (0xFF).

  build_705_flash.py <705E140.dat> <out.bin>
"""
import sys
sys.path.insert(0, '/home/nemanjan00/icom')
from icom_rza1_decode import derive_key, find_start, autokey_decode

SIZE = 0x1000000

def build(dat):
    key, _ = derive_key(dat)
    start, iv, _ = find_start(dat, key)
    length = int.from_bytes(dat[0x38:0x3C], 'little')          # 0x9C0000
    body, _ = autokey_decode(dat[start:start + length], start, key, iv)
    img = bytearray(b'\xFF' * SIZE)
    img[:0x600000] = body[:0x600000]
    img[0xC00000:0xC00000 + len(body) - 0x600000] = body[0x600000:]
    return bytes(img), len(body)

if __name__ == '__main__':
    img, n = build(open(sys.argv[1], 'rb').read())
    if '--factory' in sys.argv:
        f = open(sys.argv[sys.argv.index('--factory') + 1], 'rb').read()
        img = img[:0xFC0000] + f[:0x40000].ljust(0x40000, b'\xFF')
    open(sys.argv[2], 'wb').write(img)
    print(f'{sys.argv[2]}: {len(img)} bytes, main domain {n:#x} bytes at flash 0; '
          f'reset vector {img[:4].hex()}')
