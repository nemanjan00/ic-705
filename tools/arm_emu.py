#!/usr/bin/env python3
"""Function-level emulator for the IC-705 main ARM image (Unicorn).

Loads the decoded main-CPU image at its link address, maps on-chip RAM and the
RZ/A1 peripheral/external-bus windows, logs every MMIO access, and lets you call a
single firmware function with arguments.  Bring your own decoded image
(icom_rza1_decode.py -> main.bin); nothing from Icom is stored here.

    from arm_emu import Arm
    a = Arm('/path/to/main.bin')
    r0 = a.call(0x201B8868, 0, a.put(b'123456789'), 9)
"""
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, \
    UC_HOOK_MEM_UNMAPPED, UcError
from unicorn.arm_const import *

LOAD = 0x20005000
RAM = (0x20000000, 0x00A00000)          # RZ/A1H on-chip RAM (10 MiB)
MMIO = {                                 # name: (base, size)
    'ext_bus_cs0-5': (0x00000000, 0x18000000),
    'spibsc_xip':    (0x18000000, 0x08000000),
    'periph_e8':     (0xE8000000, 0x00300000),
    'periph_fc':     (0xFCFE0000, 0x00020000),
    'bsc_spibsc':    (0x3FE00000, 0x00200000),
}
STOP = 0x1FFFFFF0                         # return trampoline (inside ext-bus window)
HEAP = 0x20900000                         # scratch area for call arguments

class Arm:
    def __init__(self, image, log_mmio=True):
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(*RAM)
        for b, s in MMIO.values():
            uc.mem_map(b, s)
        img = open(image, 'rb').read()
        uc.mem_write(LOAD, img)
        self.mmio_log = []
        self.heap = HEAP
        if log_mmio:
            for b, s in MMIO.values():
                uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._mmio, begin=b, end=b + s - 1)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)

    def _mmio(self, uc, access, addr, size, value, _):
        if addr & ~0xF == STOP & ~0xF:
            return
        self.mmio_log.append(('W' if access == 17 else 'R', addr, size, value))

    def _unmapped(self, uc, access, addr, size, value, _):
        print(f'unmapped access {access} @0x{addr:08x} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}')
        return False

    def put(self, data):
        a = self.heap
        self.uc.mem_write(a, data)
        self.heap = (a + len(data) + 15) & ~15
        return a

    def read(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    def call(self, fn, *args, thumb=False, max_insn=5_000_000):
        uc = self.uc
        regs = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]
        for r, v in zip(regs, args[:4]):
            uc.reg_write(r, v & 0xFFFFFFFF)
        sp = 0x20800000
        for v in reversed(args[4:]):
            sp -= 4; uc.mem_write(sp, struct.pack('<I', v & 0xFFFFFFFF))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, STOP)
        uc.emu_start(fn | (1 if thumb else 0), STOP, count=max_insn)
        return uc.reg_read(UC_ARM_REG_R0)

if __name__ == '__main__':
    import sys, zlib
    a = Arm(sys.argv[1])
    data = b'123456789'
    got = a.call(0x201B8868, 0, a.put(data), len(data))
    print(f'firmware crc32 = 0x{got:08x}  zlib = 0x{zlib.crc32(data):08x}  ',
          'OK' if got == zlib.crc32(data) else 'MISMATCH')
