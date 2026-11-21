#!/usr/bin/env python3
"""Run the IC-705's own SD-card DSP-flash updater (ARM) in emulation on a .dat.

Executes, as real firmware code in Unicorn:
  MD5Init 0x20099B68 -> component writer 0x2006AB5C(fh, 0, f54, md5ctx, 0) -> MD5Final 0x2009A760
i.e. read-from-SD, MD5Update of stored bytes, autokey de-obfuscation (state at
0x203803D8: prev = IV, ctr = 75), streaming LZSS, region gating (mask byte ctx+28,
region table 0x202E2218), per-64 KiB read-compare / sector erase / page program.

Modelled (HLE) only:
  f_read 0x201F29F8   -> serves the .dat from the DSP-group offset
  0x200641D4          -> FatFs result passthrough
  SPI transactions 0x20069F30 / 0x2006A0CC on RSPI4 -> a 16 MiB SPI-NOR model
    (06 WREN, 05 RDSR, 03 READ, D8 64K erase, 02 page program)
The pre-scan's results (IV and region mask) are set from the documented rules
(IV = sum of header DSP version bytes; bit i = header version i != installed).
"""
import hashlib, struct, sys, collections
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from arm_emu import Arm

MAIN = '/home/nemanjan00/icom/out/705E140/main.bin'
AK_STATE, CTX, ABORT = 0x203803D8, 0x203802BC, 0x2038074C
MD5CTX, FH = 0x20850000, 0x20860000

class SpiNor:
    def __init__(self, image, size=0x1000000):
        self.m = bytearray(b'\xFF' * size); self.m[:len(image)] = image
        self.wel = False; self.log = collections.Counter(); self.erased = []; self.programmed = set()
    def xfer(self, tx, rx_len=0, tx2=b''):
        cmd = tx[0]; self.log[cmd] += 1
        a = (tx[1] << 16 | tx[2] << 8 | tx[3]) if len(tx) >= 4 else None
        if cmd == 0x06: self.wel = True; return b''
        if cmd == 0x04: self.wel = False; return b''
        if cmd == 0x05: return bytes([(0x02 if self.wel else 0)] * rx_len)
        if cmd == 0x03: return bytes(self.m[a:a + rx_len])
        if cmd == 0xD8:
            assert self.wel, 'erase without WREN'
            s = a & ~0xFFFF; self.m[s:s + 0x10000] = b'\xFF' * 0x10000; self.erased.append(s); self.wel = False; return b''
        if cmd == 0x02:
            assert self.wel, 'program without WREN'
            for i, b in enumerate(tx2):
                p = (a & ~0xFF) | ((a + i) & 0xFF); self.m[p] &= b
            self.programmed.add(a & ~0xFFFF); self.wel = False; return b''
        raise NotImplementedError(f'SPI cmd {cmd:#04x}')

def run(dat_path, installed_flash, installed_versions):
    d = open(dat_path, 'rb').read()
    f = {o: struct.unpack_from('<I', d, o)[0] for o in range(0x38, 0x64, 4)}
    dsp_off = 0x64 + f[0x38] + 16 + f[0x44] + f[0x4C] + 16
    expected_md5 = d[dsp_off + f[0x54]: dsp_off + f[0x54] + 16]
    hdr_vers = [d[28 + 4 * i: 32 + 4 * i] for i in range(4)]
    iv = sum(d[28:44]) & 0xFF
    mask = sum(1 << i for i in range(4) if hdr_vers[i] != installed_versions[i])

    a = Arm(MAIN, log_mmio=False)
    nor = SpiNor(installed_flash)
    pos = [dsp_off]
    def f_read(emu, fh, buf, n, pbr):
        chunk = d[pos[0]:pos[0] + n]; pos[0] += len(chunk)
        emu.uc.mem_write(buf, chunk); emu.w32(pbr, len(chunk)); return 0
    def spi_rx(emu, tx, ntx, rx, nrx):        # 0x20069F30(tx, ntx, rx, nrx)
        out = nor.xfer(emu.read(tx, ntx), nrx)
        if nrx: emu.uc.mem_write(rx, out)
        return 0
    def spi_tx(emu, tx, ntx, tx2, ntx2):      # 0x2006A0CC(tx, ntx, tx2, ntx2)
        nor.xfer(emu.read(tx, ntx), 0, emu.read(tx2, ntx2) if ntx2 else b''); return 0
    a.hle(0x201F29F8, f_read)
    a.hle(0x200641D4, lambda emu, r0, *_: r0)
    a.hle(0x20069F30, spi_rx)
    a.hle(0x2006A0CC, spi_tx)

    a.uc.mem_write(AK_STATE, bytes([iv, 75]))          # what the updater does: *v23 = v23[4]; v23[1] = 75
    a.uc.mem_write(ABORT, b'\x00')
    a.w32(CTX + 68 * 4, 0x206C0DC0)                   # v0[68] = dword_2006E280 (64 KiB work buffer)
    a.uc.mem_write(CTX + 28, bytes([mask]))
    a.call(0x20099B68, MD5CTX)                        # MD5Init
    # sub_2006AB5C(fh, 0, f54, md5ctx, a5=0): 5th arg on the stack
    rc = a.call(0x2006AB5C, FH, 0, f[0x54], MD5CTX, 0, max_insn=2_000_000_000)
    a.call(0x2009A760, MD5CTX)                        # MD5Final -> digest at ctx+88
    got = a.read(MD5CTX + 88, 16)
    return dict(rc=rc if rc < 0x80000000 else rc - 2**32, md5_ok=got == expected_md5, md5=got.hex(),
                iv=iv, mask=mask, hdr_vers=[v.decode() for v in hdr_vers], nor=nor,
                read_upto=pos[0] - dsp_off, stream_len=f[0x54])

if __name__ == '__main__':
    import time
    O = '/home/nemanjan00/icom/out'
    stock = open(f'{O}/705E140_full/dsp.image.bin', 'rb').read()
    patched = open(f'{O}/dsp_4fsk.bin', 'rb').read()
    inst = [b'1.41', b'3.06', b'3.00', b'3.00']
    cases = [('A stock .dat over stock flash', f'{O}/../fw/705E140.dat', stock),
             ('B patched v1.42 over stock flash', f'{O}/705E140_dv4fsk_v142_EXPERIMENTAL_DO_NOT_FLASH.dat', stock),
             ('C patched, version left 1.41', f'{O}/705E140_dv4fsk_EXPERIMENTAL_DO_NOT_FLASH.dat', stock)]
    for name, path, flash in cases:
        t = time.time(); r = run(path, flash, inst); nor = r['nor']
        res = bytes(nor.m[:0x200000])
        print(f'{name}: rc={r["rc"]} md5_ok={r["md5_ok"]} iv=0x{r["iv"]:02x} mask=0b{r["mask"]:04b} '
              f'read {r["read_upto"]}/{r["stream_len"]} B, SPI cmds { {hex(k): v for k, v in nor.log.items()} }, '
              f'erased {[hex(x) for x in nor.erased]}, flash==stock:{res == stock} flash==patched:{res == patched} '
              f'({time.time()-t:.0f}s)')
