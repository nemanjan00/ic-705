#!/usr/bin/env python3
"""Build a flat 16 MiB IC-705 QSPI-flash image for the rz-a1h QEMU machine (-kernel).

flash[0 : 0x9C0000] = decoded main-domain body (bootstrap @0, main LZSS @0x10000,
constant/resource region after it). sq6emm's flash notes confirm byte equality for
[0:0x600000] [EXT]; the placement of the rest is assumed identical [INF].
Remainder (beyond the main domain) is erased flash (0xFF).

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
    img[:len(body)] = body
    return bytes(img), len(body)

if __name__ == '__main__':
    img, n = build(open(sys.argv[1], 'rb').read())
    open(sys.argv[2], 'wb').write(img)
    print(f'{sys.argv[2]}: {len(img)} bytes, main domain {n:#x} bytes at flash 0; '
          f'reset vector {img[:4].hex()}')
