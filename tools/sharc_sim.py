#!/usr/bin/env python3
"""ADSP-2147x SHARC core simulator for the IC-705 main DSP (own code).

Semantics follow the SHARC Processor Programming Reference (rev 2.4) and the ADSP-21478
data-sheet memory map (both kept locally in gitignored docs/). Decoding of fields reuses
sharc_dis. Scope: SISD execution of 48-bit ISA and VISA code, fixed- and floating-point
compute (32-bit data; the 40-bit extension is not kept), multifunction ops, DAGs with
circular buffers and bit-reverse, delayed branches, PC/loop/status stacks, DO UNTIL loops,
interrupts through the vector table, IOP registers via pluggable peripheral objects.
Unknown forms raise NotImplementedError.

Memory: one store of 16-bit parcels indexed by short-word (SW) address. Per block b:
normal word (NW) n -> SW 2n (low half) / 2n+1 (high half); long word -> 4 parcels;
48-bit view (instruction / extended precision) a -> 3 parcels at
SW 2*NWbase_b + 3*(a - IWbase_b), lowest parcel = bits 15..0; VISA fetch reads parcels
most-significant first.

  sharc_sim.py dsp.image.bin --steps 200000
"""
import argparse
import struct
import sys

import sharc_dis as D
import sharc_ldr as L

M32 = 0xFFFFFFFF
bits = D.bits
sx = D.sx

# block windows: (IW base, IW end, NW base)   [data sheet table 4, ADSP-21478]
BLOCKS = [(0x8C000, 0x91555, 0x92000), (0xAC000, 0xB1555, 0xB2000),
          (0xC0000, 0xC2AAA, 0xC0000), (0xE0000, 0xE2AAA, 0xE0000)]


def f2u(f):
    return struct.unpack('<I', struct.pack('<f', f))[0]


def u2f(u):
    u &= M32
    if (u >> 23) & 0xFF == 0:                  # denormals flush to zero on SHARC
        u &= 0x80000000
    return struct.unpack('<f', struct.pack('<I', u))[0]


def s32(v):
    v &= M32
    return v - (1 << 32) if v >> 31 else v


class Halt(Exception):
    pass


class Sharc:
    def __init__(self):
        self.sw = bytearray(0x200000 * 2)       # parcels for SW 0x000000..0x1FFFFF
        self.R = [0] * 16
        self.S = [0] * 16                        # PEy register file (usable as Uregs in SISD)
        self.Ralt = [0] * 16
        self.I, self.M, self.Lr, self.B = [0] * 16, [0] * 16, [0] * 16, [0] * 16
        self.dag_alt = [[0] * 16 for _ in range(4)]
        self.sreg = {n: 0 for n in D.UREG_G7}
        self.dpi_irptl = 0
        self.sreg['MODE1'] = 0
        self.ustat = {}
        self.PX = 0
        self.MRF = 0
        self.MRB = 0
        self.MSF = 0
        self.MSB = 0
        self.pey = False                         # replaying an instruction for PEy (SIMD)
        self.pc = 0
        self.cur = 0
        self.visa = False
        self.pcstk = []
        self.loopstk = []                        # [start, end, term, count or None, visa]
        self.ststk = []
        self.lcntr = 0
        self.delay = []                          # pending delayed-branch target
        self.iop = {}                            # base -> peripheral
        self.iop_regs = {}
        self.cycles = 0
        self.trace = None
        self.tperiod = self.tcount = 0
        self.flags_in = 0
        self.hle = {}                            # pc -> fast-forward hook

    # ------------------------------------------------------------- memory
    def p_get(self, a):
        return self.sw[2 * a] | self.sw[2 * a + 1] << 8

    def p_set(self, a, v):
        self.sw[2 * a] = v & 0xFF
        self.sw[2 * a + 1] = v >> 8 & 0xFF

    @staticmethod
    def iw_base(a):
        for iwb, iwe, nwb in BLOCKS:
            if iwb <= a < iwe:
                return 2 * nwb + 3 * (a - iwb)
        return None

    def rd48(self, a):
        s = self.iw_base(a)
        if s is None:
            raise NotImplementedError(f'48-bit access outside SRAM {a:#x}')
        return self.p_get(s) | self.p_get(s + 1) << 16 | self.p_get(s + 2) << 32

    def wr48(self, a, v):
        s = self.iw_base(a)
        for k in range(3):
            self.p_set(s + k, v >> (16 * k) & 0xFFFF)

    def ureg_load(self, ur, a):
        """ureg = mem; PX moves all 48 bits when the address is a 48-bit window."""
        if D.UREG_G6[ur & 15] == 'PX' and ur >> 4 == 6 and self.iw_base(a & M32) is not None:
            self.PX = self.rd48(a & M32)
        else:
            self.set_ureg(ur, self.rd(a))

    def ureg_store(self, ur, a):
        if D.UREG_G6[ur & 15] == 'PX' and ur >> 4 == 6 and self.iw_base(a & M32) is not None:
            self.wr48(a & M32, self.PX)
        else:
            self.wr(a, self.get_ureg(ur))

    def rd(self, a, lw=False):
        """Data read (DM/PM): returns 32-bit value; 48-bit windows return the top 32 bits."""
        a &= 0xFFFFFFFF
        if a < 0x40000:
            return self.iop_rd(a)
        if 0x100000 <= a < 0x200000:                         # short word
            return self.p_get(a)
        if 0x80000 <= a < 0x100000:
            if self.iw_base(a) is not None:
                return (self.rd48(a) >> 16) & M32
            return self.p_get(2 * a) | self.p_get(2 * a + 1) << 16
        if 0x40000 <= a < 0x80000:                           # long word: low 32 bits
            n = 2 * a
            return self.p_get(2 * n) | self.p_get(2 * n + 1) << 16
        return 0                                             # external memory: not modelled

    def wr(self, a, v):
        a &= 0xFFFFFFFF
        v &= M32
        if a < 0x40000:
            return self.iop_wr(a, v)
        if 0x100000 <= a < 0x200000:
            return self.p_set(a, v)
        if 0x80000 <= a < 0x100000:
            if self.iw_base(a) is not None:
                old = self.rd48(a)
                return self.wr48(a, (old & 0xFFFF) | v << 16)
            self.p_set(2 * a, v)
            self.p_set(2 * a + 1, v >> 16)
            return
        if 0x40000 <= a < 0x80000:
            n = 2 * a
            self.p_set(2 * n, v)
            self.p_set(2 * n + 1, v >> 16)

    def iop_rd(self, a):
        dev = self.iop.get(a & ~0xFF) or self.iop.get(a)
        return dev.rd(self, a) if dev else self.iop_regs.get(a, 0)

    def iop_wr(self, a, v):
        dev = self.iop.get(a & ~0xFF) or self.iop.get(a)
        if dev:
            dev.wr(self, a, v)
        else:
            self.iop_regs[a] = v

    def fetch(self):
        """Return (instruction word aligned to bits 47..0, length in program units)."""
        if self.visa:
            w = self.p_get(self.pc) << 32 | self.p_get(self.pc + 1) << 16 | self.p_get(self.pc + 2)
            n = D.visa_length(w)
            return w, n
        return self.rd48(self.pc), 1

    # ------------------------------------------------------------- registers
    MODE1_BITS = {'BR8': 0, 'BR0': 1, 'SRCU': 2, 'SRD1H': 3, 'SRD1L': 4, 'SRD2H': 5,
                  'SRD2L': 6, 'SRRFH': 7, 'SRRFL': 10, 'NESTM': 11, 'IRPTEN': 12,
                  'ALUSAT': 13, 'SSE': 14, 'TRUNC': 15, 'RND32': 16, 'CSEL': 17,
                  'PEYEN': 21, 'SIMD': 21, 'BDCST9': 22, 'BDCST1': 23, 'CBUFEN': 24}
    ASTAT = {'AZ': 0, 'AV': 1, 'AN': 2, 'AC': 3, 'AS': 4, 'AI': 5, 'MN': 6, 'MV': 7, 'MU': 8,
             'MI': 9, 'AF': 10, 'SV': 11, 'SZ': 12, 'SS': 13, 'BTF': 18}

    def m1(self, name):
        return self.sreg['MODE1'] >> self.MODE1_BITS[name] & 1

    def af(self, name):
        return self.sreg['ASTATX'] >> self.ASTAT[name] & 1

    def setf(self, name, v):
        b = self.ASTAT[name]
        self.sreg['ASTATX'] = (self.sreg['ASTATX'] & ~(1 << b)) | ((1 if v else 0) << b)

    def set_mode1(self, v):
        old = self.sreg['MODE1']
        self.sreg['MODE1'] = v & M32
        ch = old ^ v
        if ch & (1 << 7 | 1 << 10):             # register file swaps (both halves together)
            if (ch >> 7 & 1) or (ch >> 10 & 1):
                lo = ch >> 10 & 1
                hi = ch >> 7 & 1
                for k in range(16):
                    if (k < 8 and lo) or (k >= 8 and hi):
                        self.R[k], self.Ralt[k] = self.Ralt[k], self.R[k]
        for bitn, rng in ((4, range(0, 4)), (3, range(4, 8)), (6, range(8, 12)), (5, range(12, 16))):
            if ch >> bitn & 1:
                for regs, alt in ((self.I, 0), (self.M, 1), (self.Lr, 2), (self.B, 3)):
                    for k in rng:
                        regs[k], self.dag_alt[alt][k] = self.dag_alt[alt][k], regs[k]

    def get_ureg(self, c):
        g, n = c >> 4 & 7, c & 15
        if g == 0:
            return self.R[n]
        if g == 1:
            return self.I[n]
        if g == 2:
            return self.M[n]
        if g == 3:
            return self.Lr[n]
        if g == 4:
            return self.B[n]
        if g == 5:
            return self.S[n]
        if g == 6:
            name = D.UREG_G6[n]
            if name == 'PC':
                return self.cur                  # address of the executing instruction
            if name == 'PCSTK':
                return self.pcstk[-1] if self.pcstk else 0
            if name == 'PCSTKP':
                return len(self.pcstk)
            if name == 'LCNTR':
                return self.lcntr
            if name == 'CURLCNTR':
                return self.loopstk[-1][3] if self.loopstk and self.loopstk[-1][3] is not None else 0
            if name == 'LADDR':
                return self.loopstk[-1][1] if self.loopstk else 0
            if name == 'PX':
                return self.PX & 0xFFFFFFFFFFFF
            if name == 'PX1':
                return self.PX & 0xFFFF
            if name == 'PX2':
                return self.PX >> 16 & M32
            if name == 'TPERIOD':
                return self.tperiod
            if name == 'TCOUNT':
                return self.tcount
            return 0
        name = D.UREG_G7[n]
        if name == 'FLAGS':
            return self.sreg['FLAGS']
        return self.sreg[name]

    def set_ureg(self, c, v):
        g, n = c >> 4 & 7, c & 15
        if self.pey and g not in (0, 5):
            return
        v &= M32 if not (g == 6 and D.UREG_G6[n] == 'PX') else 0xFFFFFFFFFFFF
        if g == 0:
            self.R[n] = v
        elif g == 1:
            self.I[n] = v
        elif g == 2:
            self.M[n] = v
        elif g == 3:
            self.Lr[n] = v
        elif g == 4:
            self.B[n] = v
            self.I[n] = v                        # writing B also loads I
        elif g == 5:
            self.S[n] = v
        elif g == 6:
            name = D.UREG_G6[n]
            if name == 'PCSTK':
                if self.pcstk:
                    self.pcstk[-1] = v
                else:
                    self.pcstk.append(v)
            elif name == 'LCNTR':
                self.lcntr = v
            elif name == 'PX':
                self.PX = v
            elif name == 'PX1':
                self.PX = (self.PX & ~0xFFFF) | (v & 0xFFFF)
            elif name == 'PX2':
                self.PX = (self.PX & 0xFFFF) | (v & M32) << 16
            elif name == 'TPERIOD':
                self.tperiod = v
            elif name == 'TCOUNT':
                self.tcount = v
        else:
            name = D.UREG_G7[n]
            if name == 'MODE1':
                self.set_mode1(v)
            else:
                self.sreg[name] = v

    # ------------------------------------------------------------- DAG
    def dag(self, i, m, post, update=True, imm=None):
        """Return the effective address for I/M (pre- or post-modify) and update I."""
        mod = imm if imm is not None else s32(self.M[m])
        cur = self.I[i]
        if self.pey:                              # PEy accesses the next word, no I update
            return ((cur + mod) if not post else cur) + 1 & M32
        if not post:
            return (cur + mod) & M32
        new = (cur + mod) & M32
        ln = self.Lr[i]
        if ln and self.m1('CBUFEN') or ln and True:
            base = self.B[i]
            if new >= base + ln:
                new -= ln
            elif new < base:
                new += ln
        if update:
            self.I[i] = new & M32
        return cur

    def circ(self, i, v):
        ln = self.Lr[i]
        if ln:
            base = self.B[i]
            if v >= base + ln:
                v -= ln
            elif v < base:
                v += ln
        return v & M32

    # ------------------------------------------------------------- conditions
    def cond(self, c):
        if c == 31:
            return True
        neg = c >= 16
        k = c & 15
        a = self.af
        if k == 0:
            r = a('AZ')
        elif k == 1:
            r = (a('AN') or a('AZ') == 0 and False) if not a('AF') else a('AN') and not a('AZ')
            r = a('AN') and not a('AZ') if True else r
        elif k == 2:
            r = a('AN') or a('AZ')
        elif k == 3:
            r = a('AC')
        elif k == 4:
            r = a('AV')
        elif k == 5:
            r = a('MV')
        elif k == 6:
            r = a('MN')
        elif k == 7:
            r = a('SV')
        elif k == 8:
            r = a('SZ')
        elif 9 <= k <= 12:
            r = self.flags_in >> (k - 9) & 1
        elif k == 13:
            r = a('BTF')
        elif k == 14:
            r = 0
        else:                                   # 15: NOT LCE as condition
            cur = self.loopstk[-1][3] if self.loopstk else 1
            r = cur != 1
            return r if not neg else not r
        r = bool(r)
        if c == 16 + 1:                          # GE = not LT
            return not (a('AN') and not a('AZ'))
        if c == 16 + 2:                          # GT = not LE
            return not (a('AN') or a('AZ'))
        return (not r) if neg else r

    # ------------------------------------------------------------- ALU / MUL / SHIFT
    def alu_flags(self, res, av=0, ac=0, fl=False):
        self.setf('AZ', (res & M32) == 0)
        self.setf('AN', res >> 31 & 1)
        self.setf('AV', av)
        self.setf('AC', ac)
        self.setf('AF', fl)

    def falu_flags(self, f):
        self.setf('AZ', f == 0.0)
        self.setf('AN', f < 0)
        self.setf('AV', 0)
        self.setf('AC', 0)
        self.setf('AF', 1)

    def sat(self, v):
        if self.m1('ALUSAT'):
            return max(-(1 << 31), min((1 << 31) - 1, v))
        return v

    def add(self, x, y, c=0):
        r = x + y + c
        av = 1 if (s32(x) + s32(y) + c) != s32(r) else 0
        ac = 1 if ((x & M32) + (y & M32) + c) >> 32 else 0
        out = (self.sat(s32(x) + s32(y) + c) if av else r) & M32
        self.alu_flags(out, av, ac)
        return out

    def sub(self, x, y, c=1):
        return self.add(x, (~y) & M32, c)

    def alu(self, op, n, x, y):
        R = self.R
        rx, ry = R[x], R[y]
        F = lambda v: u2f(v)
        if op == 0x01:
            R[n] = self.add(rx, ry)
        elif op == 0x02:
            R[n] = self.sub(rx, ry)
        elif op == 0x05:
            R[n] = self.add(rx, ry, self.af('AC'))
        elif op == 0x06:
            R[n] = self.add(rx, (~ry) & M32, self.af('AC'))
        elif op == 0x09:
            v = (s32(rx) + s32(ry)) >> 1
            R[n] = v & M32
            self.alu_flags(R[n])
        elif op in (0x0A, 0x0B):
            if op == 0x0A:
                a, b = s32(rx), s32(ry)
            else:
                a, b = rx, ry
            self.setf('AZ', a == b)
            self.setf('AN', a < b)
            self.setf('AV', 0)
            self.setf('AC', 0)
        elif op == 0x25:
            R[n] = self.add(rx, 0, self.af('AC'))
        elif op == 0x26:
            R[n] = self.add(rx, M32, self.af('AC'))
        elif op == 0x29:
            R[n] = self.add(rx, 1)
        elif op == 0x2A:
            R[n] = self.add(rx, M32)
        elif op == 0x22:
            R[n] = self.sub(0, rx)
        elif op == 0x30:
            v = abs(s32(rx))
            R[n] = min(v, (1 << 31) - 1) & M32 if self.m1('ALUSAT') else v & M32
            self.alu_flags(R[n], 1 if rx == 0x80000000 else 0)
        elif op == 0x21:
            R[n] = rx
            self.alu_flags(rx)
        elif op in (0x40, 0x41, 0x42, 0x43):
            R[n] = {0x40: rx & ry, 0x41: rx | ry, 0x42: rx ^ ry, 0x43: ~rx & M32}[op]
            self.alu_flags(R[n])
        elif op in (0x61, 0x62):
            v = min(s32(rx), s32(ry)) if op == 0x61 else max(s32(rx), s32(ry))
            R[n] = v & M32
            self.alu_flags(R[n])
        elif op == 0x63:
            lim = abs(s32(ry))
            R[n] = max(-lim, min(lim, s32(rx))) & M32
            self.alu_flags(R[n])
        # ---- floating point
        elif op in (0x81, 0x82, 0x91, 0x92, 0x89):
            a, b = F(rx), F(ry)
            v = {0x81: a + b, 0x82: a - b, 0x91: abs(a + b), 0x92: abs(a - b), 0x89: (a + b) / 2}[op]
            R[n] = self.fstore(v)
        elif op == 0x8A:
            a, b = F(rx), F(ry)
            self.setf('AZ', a == b)
            self.setf('AN', a < b)
            self.setf('AF', 1)
        elif op in (0xA2, 0xB0, 0xA1, 0xA5):
            a = F(rx)
            R[n] = self.fstore({0xA2: -a, 0xB0: abs(a), 0xA1: a, 0xA5: a}[op])
        elif op == 0xBD:
            R[n] = self.fstore(F(rx) * 2.0 ** s32(ry))
        elif op == 0xAD:
            u = rx
            R[n] = ((u & 0x7FFFFF) | 0x800000) << 8 & M32 if u & 0x7F800000 else 0
            self.alu_flags(R[n])
        elif op == 0xC1:
            u = rx
            e = (u >> 23 & 0xFF) - 127
            R[n] = e & M32
            self.alu_flags(R[n])
        elif op in (0xC9, 0xD9, 0xCD, 0xDD):
            a = F(rx)
            if op in (0xD9, 0xDD):
                a *= 2.0 ** s32(ry)
            trunc = op in (0xCD, 0xDD) or self.m1('TRUNC')
            import math
            if a != a:                                        # NaN -> all ones
                v = -1
            elif a in (float('inf'), float('-inf')):
                v = (1 << 31) - 1 if a > 0 else -(1 << 31)
            else:
                v = math.trunc(a) if trunc else int(round(a))
            v = max(-(1 << 31), min((1 << 31) - 1, v))
            R[n] = v & M32
            self.alu_flags(R[n])
        elif op in (0xCA, 0xDA):
            v = float(s32(rx))
            if op == 0xDA:
                v *= 2.0 ** s32(ry)
            R[n] = self.fstore(v)
        elif op == 0xC4:
            a = F(rx)
            R[n] = self.fstore(1.0 / a if a else float('inf'))
        elif op == 0xC5:
            a = F(rx)
            R[n] = self.fstore(1.0 / a ** 0.5 if a > 0 else float('inf'))
        elif op == 0xE0:
            R[n] = (rx & 0x7FFFFFFF) | (ry & 0x80000000)
            self.falu_flags(F(R[n]))
        elif op in (0xE1, 0xE2):
            a, b = F(rx), F(ry)
            R[n] = self.fstore(min(a, b) if op == 0xE1 else max(a, b))
        elif op == 0xE3:
            a, b = F(rx), abs(F(ry))
            R[n] = self.fstore(max(-b, min(b, a)))
        else:
            raise NotImplementedError(f'ALU op {op:#x}')

    def fstore(self, v):
        try:
            u = f2u(v)
        except OverflowError:
            u = 0x7F7FFFFF if v > 0 else 0xFF7FFFFF
        self.falu_flags(u2f(u))
        return u

    def mul(self, op, n, x, y):
        R = self.R
        if op == 0x30:
            v = u2f(R[x]) * u2f(R[y])
            try:
                R[n] = f2u(v)
            except OverflowError:
                R[n] = 0x7F7FFFFF
            return
        hi = op >> 6
        if hi in (1, 2, 3):
            ys, xs, f, rnd = op >> 5 & 1, op >> 4 & 1, op >> 3 & 1, op & 1
            a = s32(R[x]) if xs else R[x]
            b = s32(R[y]) if ys else R[y]
            p = a * b
            if f:
                p <<= 1
            dst = op >> 1 & 3
            if hi == 1:
                acc = 0
            else:
                acc = self.MRF if dst in (0, 2) else self.MRB
                p = acc + p if hi == 2 else acc - p
            p &= (1 << 80) - 1
            if dst == 2:
                self.MRF = p
            elif dst == 3:
                self.MRB = p
            else:
                if f:
                    v = p + (0x80000000 if rnd else 0)
                    R[n] = (v >> 32) & M32
                else:
                    R[n] = p & M32
            self.setf('MN', (p >> 79) & 1)
            self.setf('MV', 0)
            return
        if op >> 4 == 0:                         # SAT
            return
        if op == 0x14:
            self.MRF = 0
            return
        if op == 0x16:
            self.MRB = 0
            return
        if op >> 4 == 1:                         # RND
            dst = op >> 1 & 3
            acc = self.MRF if dst in (0, 2) else self.MRB
            v = (acc + 0x80000000) & ~0xFFFFFFFF & ((1 << 80) - 1)
            if dst == 2:
                self.MRF = v
            elif dst == 3:
                self.MRB = v
            else:
                R[n] = (v >> 32) & M32
            return
        raise NotImplementedError(f'MUL op {op:#x}')

    def shift(self, op, n, x, ydata, imm=False):
        R = self.R
        rx = R[x]
        if op in (0x00, 0x20, 0x04, 0x24, 0x08):
            sh = sx(ydata & 0xFF, 8) if imm else s32(R[ydata])
            if op in (0x00, 0x20):
                v = (rx << sh) & M32 if sh >= 0 else rx >> -sh if sh > -32 else 0
                if sh >= 32:
                    v = 0
            elif op in (0x04, 0x24):
                if sh >= 0:
                    v = (rx << sh) & M32 if sh < 32 else 0
                else:
                    v = (s32(rx) >> min(-sh, 31)) & M32
            else:
                k = sh % 32
                v = ((rx << k) | (rx >> (32 - k))) & M32 if k else rx
            if op in (0x20, 0x24):
                v |= R[n]
            R[n] = v & M32
            self.setf('SZ', R[n] == 0)
            self.setf('SV', 0)
            return
        if op in (0xC0, 0xC4, 0xC8, 0xCC):
            b = (ydata if imm else R[ydata]) & 0x3F
            m = (1 << b) & M32 if b < 32 else 0
            if op == 0xCC:
                self.setf('SZ', (rx & m) == 0)
                return
            R[n] = {0xC0: rx | m, 0xC4: rx & ~m & M32, 0xC8: rx ^ m}[op]
            self.setf('SZ', R[n] == 0)
            return
        if op in (0x40, 0x48, 0x44, 0x4C, 0x64, 0x6C):
            d = ydata if imm else R[ydata]
            pos, ln = d & 0x3F, (d >> 6) & 0x3F
            mask = ((1 << ln) - 1) if ln else 0
            if op in (0x40, 0x48):                # FEXT
                v = (rx >> pos) & mask
                if op == 0x48 and ln and v >> (ln - 1) & 1:
                    v |= (~mask) & M32
            else:                                 # FDEP
                f = rx & mask
                if op in (0x4C, 0x6C) and ln and f >> (ln - 1) & 1:
                    f |= (~mask) & M32
                v = (f << pos) & M32
                if op in (0x64, 0x6C):
                    v = (R[n] & ~((mask << pos) & M32)) | v
            R[n] = v & M32
            self.setf('SZ', R[n] == 0)
            return
        if op in (0x80, 0x84):                    # EXP
            v = s32(rx)
            if v == 0:
                e = -31
            else:
                k = 0
                while k < 31 and ((rx >> (30 - k)) & 1) == (rx >> 31 & 1):
                    k += 1
                e = -k
            R[n] = e & M32
            self.setf('SZ', 0)
            return
        if op in (0x88, 0x8C):                    # LEFTZ / LEFTO
            t = 0 if op == 0x88 else 1
            k = 0
            while k < 32 and (rx >> (31 - k) & 1) == t:
                k += 1
            R[n] = k
            self.setf('SZ', k == 0)
            return
        raise NotImplementedError(f'SHIFT op {op:#x}')

    def compute(self, c):
        if c == 0:
            return
        R = self.R
        if c >> 22:
            if (c >> 20) & 3 in (2, 3):
                fl = (c >> 20) & 1
                s, m, a = bits(c, 19, 16), bits(c, 15, 12), bits(c, 11, 8)
                xm, ym, xa, ya = bits(c, 7, 6), bits(c, 5, 4) + 4, bits(c, 3, 2) + 8, bits(c, 1, 0) + 12
                X, Y, XA, YA = R[xm], R[ym], R[xa], R[ya]
                if fl:
                    pm = f2u(u2f(X) * u2f(Y))
                    sa, ss = self.fstore(u2f(XA) + u2f(YA)), f2u(u2f(XA) - u2f(YA))
                else:
                    pm = ((s32(X) * s32(Y) * 2 + 0x80000000) >> 32) & M32
                    sa, ss = (XA + YA) & M32, (XA - YA) & M32
                    self.alu_flags(sa)
                R[m], R[a], R[s] = pm, sa, ss
                return
            op = bits(c, 21, 16)
            m, a = bits(c, 15, 12), bits(c, 11, 8)
            xm, ym, xa, ya = bits(c, 7, 6), bits(c, 5, 4) + 4, bits(c, 3, 2) + 8, bits(c, 1, 0) + 12
            X, Y, XA, YA = R[xm], R[ym], R[xa], R[ya]
            if op >= 0x18:
                pm = f2u(u2f(X) * u2f(Y))
                fa = {0x18: lambda: self.fstore(u2f(XA) + u2f(YA)),
                      0x19: lambda: self.fstore(u2f(XA) - u2f(YA)),
                      0x1A: lambda: self.fstore(float(s32(XA)) * 2.0 ** s32(YA)),
                      0x1B: lambda: int(u2f(XA) * 2.0 ** s32(YA)) & M32,
                      0x1C: lambda: self.fstore((u2f(XA) + u2f(YA)) / 2),
                      0x1D: lambda: self.fstore(abs(u2f(XA))),
                      0x1E: lambda: self.fstore(max(u2f(XA), u2f(YA))),
                      0x1F: lambda: self.fstore(min(u2f(XA), u2f(YA)))}[op]()
                R[m], R[a] = pm, fa
                return
            prod = s32(X) * s32(Y) * 2
            kind, aop = op >> 2, op & 3
            av = {0: (XA + YA), 1: (XA - YA), 2: (s32(XA) + s32(YA)) >> 1}[aop] & M32
            if kind == 1:
                R[m] = ((prod + 0x80000000) >> 32) & M32
            elif kind == 2:
                self.MRF = (self.MRF + prod) & ((1 << 80) - 1)
            elif kind == 3:
                self.MRF = (self.MRF + prod) & ((1 << 80) - 1)
                R[m] = ((self.MRF + 0x80000000) >> 32) & M32
            elif kind == 4:
                self.MRF = (self.MRF - prod) & ((1 << 80) - 1)
            elif kind == 5:
                self.MRF = (self.MRF - prod) & ((1 << 80) - 1)
                R[m] = ((self.MRF + 0x80000000) >> 32) & M32
            R[a] = av
            self.alu_flags(av)
            return
        cu = bits(c, 21, 20)
        op = bits(c, 19, 12)
        n, x, y = bits(c, 11, 8), bits(c, 7, 4), bits(c, 3, 0)
        if cu == 0:
            if bits(c, 19, 16) in (0x7, 0xF):
                s, a = bits(c, 15, 12), bits(c, 11, 8)
                X, Y = R[x], R[y]
                if bits(c, 19, 16) == 0xF:
                    R[a], R[s] = self.fstore(u2f(X) + u2f(Y)), f2u(u2f(X) - u2f(Y))
                else:
                    R[a], R[s] = (X + Y) & M32, (X - Y) & M32
                    self.alu_flags(R[a])
                return
            return self.alu(op, n, x, y)
        if cu == 1:
            return self.mul(op, n, x, y)
        if cu == 2:
            return self.shift(op, n, x, y)
        if bits(c, 22, 17) == 0b0110000 >> 1:
            pass
        raise NotImplementedError(f'compute {c:#08x}')

    # ------------------------------------------------------------- execution
    def push_pc(self, ret):
        self.pcstk.append((ret, self.visa))

    def jump(self, target, delayed, call=False, ret_after=None):
        """Branch; ISA/VISA follows the target address space (SW addresses are VISA)."""
        if call:
            self.push_pc(ret_after)
        if delayed:
            self.delay = [3, target]           # this step + two delay slots
        else:
            self.goto(target)

    def goto(self, target):
        target &= 0xFFFFFF
        self.visa = target >= 0x100000
        self.pc = target

    def step(self):
        h = self.hle.get(self.pc)
        if h:
            h(self)
        self.take_irq()
        w, n = self.fetch()
        here = self.pc
        self.cur = here
        nxt = here + n if self.visa else here + 1
        if self.trace is not None:
            self.trace.append((here, self.visa))
        self.pc = nxt
        self.exec(w, n, here, nxt)
        self.cycles += 1
        # loop end check (DO UNTIL)
        while self.loopstk and here == self.loopstk[-1][1] and self.pc == nxt:
            st, end, term, cnt, vs = self.loopstk[-1]
            if term == 15:                        # LCE
                cnt -= 1
                self.loopstk[-1][3] = cnt
                done = cnt == 0
            else:
                done = self.cond(term)
            if done:
                self.loopstk.pop()
                if self.pcstk and self.pcstk[-1][0] == ('loop', st):
                    self.pcstk.pop()
            else:
                self.pc = st
                self.visa = vs
            break
        if self.delay:
            self.delay[0] -= 1
            if self.delay[0] == 0:
                t = self.delay[1]
                self.delay = []
                self.goto(t)
        if self.tperiod and self.tcount:
            self.tcount -= 1
            if self.tcount == 0:
                self.tcount = self.tperiod
                self.sreg['IRPTL'] |= 1 << 2 if False else 0

    def take_irq(self):
        if not self.m1('IRPTEN') or self.delay:
            return
        pend = self.sreg['IRPTL'] & self.sreg['IMASK']
        if not pend:
            return
        k = (pend & -pend).bit_length() - 1
        self.sreg['IRPTL'] &= ~(1 << k)
        self.push_pc(self.pc if not self.visa else self.pc)
        self.sreg['IMASKP'] |= 1 << k
        self.irq_ret_visa = self.visa
        self.goto(0x8C000 + 4 * k)

    def pe_swap(self):
        self.R, self.S = self.S, self.R
        x, y = self.sreg['ASTATX'], self.sreg['ASTATY']
        self.sreg['ASTATX'], self.sreg['ASTATY'] = y, x
        x, y = self.sreg['STKYX'], self.sreg['STKYY']
        self.sreg['STKYX'], self.sreg['STKYY'] = y, x
        self.MRF, self.MSF = self.MSF, self.MRF
        self.MRB, self.MSB = self.MSB, self.MRB

    def simd_replay(self, w, n):
        """Does this instruction have a PEy half in SIMD mode (compute / data-register move)?"""
        if self.visa and n == 1:
            return (w >> 44) in (0b1100, 0b1001) and bits(w, 36, 36) == 1 or (w >> 44) == 0b1100
        top3, sub = bits(w, 47, 45), bits(w, 44, 40)
        if top3 in (0b001, 0b011):
            return True
        if top3 == 0b010:
            return True
        if top3 == 0b100 and bits(w, 44, 44) == 0:
            return True
        if top3 == 0 and sub in (0b00001, 0b00010, 0b00100):
            return True
        if top3 == 0 and (sub == 0b01111 or sub >> 2 == 0b100) and bits(w, 38, 36) == 0:
            return True                           # Ureg = imm / Ureg <-> mem with an R register
        return False

    def exec(self, w, n, here, nxt):
        simd = self.sreg['MODE1'] >> 21 & 1 and self.simd_replay(w, n)
        if simd:
            i_before = list(self.I)              # PEy addresses come from the pre-update DAGs
        self.exec1(w, n, here, nxt)
        if simd:
            pc, vs, dl, i_after = self.pc, self.visa, list(self.delay), self.I
            self.I = i_before
            self.pe_swap()
            self.pey = True
            try:
                self.exec1(w, n, here, nxt)
            finally:
                self.pey = False
                self.pe_swap()
                self.I = i_after
                self.pc, self.visa, self.delay = pc, vs, dl

    def exec1(self, w, n, here, nxt):
        if self.visa and n == 1:
            return self.exec_short(w, here, nxt)
        if self.visa and n == 2:
            return self.exec_b(w, here, nxt)
        return self.exec48(w, here, nxt)

    def mem_access(self, g, i, m, d, reg_get, reg_set, post=True, lw=False, imm=None):
        ii = i + 8 if g else i
        mm = m + 8 if g else m
        a = self.dag(ii, mm, post, imm=imm)
        if d:
            self.wr(a, reg_get())
        else:
            reg_set(self.rd(a))

    def exec48(self, w, here, nxt):
        top3 = bits(w, 47, 45)
        sub = bits(w, 44, 40)
        c23 = bits(w, 22, 0)
        cnd = bits(w, 37, 33)
        R = self.R
        if w == 0:
            return
        if top3 == 0b001:                                     # type 1
            dmd, dmi, dmm = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38)
            pmd, dmr = bits(w, 37, 37), bits(w, 36, 33)
            pmi, pmm, pmr = bits(w, 32, 30) + 8, bits(w, 29, 27) + 8, bits(w, 26, 23)
            dv, pv = R[dmr], R[pmr]
            da = self.dag(dmi, dmm, True)
            pa = self.dag(pmi, pmm, True)
            ld_d = None if dmd else self.rd(da)
            ld_p = None if pmd else self.rd(pa)
            self.compute(c23)
            if dmd:
                self.wr(da, dv)
            else:
                R[dmr] = ld_d
            if pmd:
                self.wr(pa, pv)
            else:
                R[pmr] = ld_p
            return
        if top3 == 0b010:                                     # type 3
            if not self.cond(cnd):
                return
            u, i, m = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38)
            g, d, l, ur = bits(w, 32, 32), bits(w, 31, 31), bits(w, 30, 30), bits(w, 29, 23)
            if g:
                i, m = i + 8, m + 8
            val = self.get_ureg(ur) if d else None
            a = self.dag(i, m, bool(u))
            self.compute(c23)
            if d:
                self.wr(a, val)
            else:
                self.ureg_load(ur, a)
            return
        if top3 == 0b011:
            if bits(w, 44, 44) == 0:                          # type 4
                if not self.cond(cnd):
                    return
                i, g, d, u = bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39), bits(w, 38, 38)
                data, dreg = sx(bits(w, 32, 27), 6), bits(w, 26, 23)
                if g:
                    i += 8
                val = R[dreg]
                a = (self.I[i] + data) & M32 if not u else self.I[i]
                if u and not self.pey:
                    self.I[i] = (self.I[i] + data) & M32
                if self.pey:
                    a = (a + 1) & M32
                self.compute(c23)
                if d:
                    self.wr(a, val)
                else:
                    R[dreg] = self.rd(a)
                return
            if bits(w, 43, 43) == 0:                          # type 5
                if not self.cond(cnd):
                    return
                src = (bits(w, 42, 38) << 2) | bits(w, 32, 31)
                dst = bits(w, 30, 23)
                v = self.get_ureg(src)
                self.compute(c23)
                self.set_ureg(dst, v)
                return
            raise NotImplementedError('Dreg <-> CDreg swap')
        if top3 == 0b100:
            if bits(w, 44, 44) == 0:                          # type 6 with move
                if not self.cond(cnd):
                    return
                i, m, g, d = bits(w, 43, 41), bits(w, 40, 38), bits(w, 32, 32), bits(w, 31, 31)
                dreg = bits(w, 26, 23)
                val = R[dreg]
                if g:
                    i, m = i + 8, m + 8
                a = self.dag(i, m, True)
                self.shift_imm(w)
                if d:
                    self.wr(a, val)
                else:
                    R[dreg] = self.rd(a)
                return
            i, m, g = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37)   # type 16
            if g:
                i, m = i + 8, m + 8
            self.wr(self.dag(i, m, True), bits(w, 31, 0))
            return
        if top3 == 0b101:                                     # type 15
            g, i, d, l = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39)
            ur, data = bits(w, 38, 32), sx(bits(w, 31, 0), 32)
            if g:
                i += 8
            a = (self.I[i] + data + (1 if self.pey else 0)) & M32
            if d:
                self.ureg_store(ur, a)
            else:
                self.ureg_load(ur, a)
            return
        if top3 in (0b110, 0b111):                            # type 10
            d, dmi, dmm, dreg = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38), bits(w, 26, 23)
            take = self.cond(cnd)
            if top3 == 0b110:
                tgt = (self.I[bits(w, 32, 30) + 8] + s32(self.M[bits(w, 29, 27) + 8])) & 0xFFFFFF
            else:
                tgt = (here + sx(bits(w, 32, 27), 6)) & 0xFFFFFF
            a = self.dag(dmi, dmm, True)
            if d:
                self.wr(a, R[dreg])
            else:
                R[dreg] = self.rd(a)
            if take:
                self.goto(tgt)
            else:
                self.compute(c23)
            return
        # ---- group 000
        if sub == 0b00001:                                    # type 2
            if self.cond(cnd):
                self.compute(c23)
            return
        if sub == 0b00010:                                    # type 6 without move
            if self.cond(cnd):
                self.shift_imm(w)
            return
        if sub == 0b00100:                                    # type 7
            if not self.cond(cnd):
                return
            g, isrc, m, idis = bits(w, 38, 38), bits(w, 32, 30), bits(w, 29, 27), bits(w, 26, 24)
            if g:
                isrc, m = isrc + 8, m + 8
            self.compute(c23)
            if not self.pey:                     # Id = Is XOR IdIs receives the modified value
                v = (self.I[isrc] + s32(self.M[m])) & M32
                self.I[isrc ^ idis] = self.circ(isrc, v)
            return
        if sub in (0b00110, 0b00111):                         # type 8
            b, j = bits(w, 39, 39), bits(w, 26, 26)
            addr = bits(w, 23, 0)
            if sub == 0b00111:
                addr = (here + sx(addr, 24)) & 0xFFFFFF
            if self.cond(cnd):
                self.jump(addr, j, call=bool(b), ret_after=(here + 3 if j else nxt) if not self.visa
                          else (nxt if not j else None))
                if b and j:
                    self.pcstk[-1] = (self.after_delay(here), self.visa)
            return
        if sub in (0b01000, 0b01001):                         # type 9
            b, j, e = bits(w, 39, 39), bits(w, 26, 26), bits(w, 25, 25)
            take = self.cond(cnd)
            if sub == 0b01000:
                tgt = (self.I[bits(w, 32, 30) + 8] + s32(self.M[bits(w, 29, 27) + 8])) & 0xFFFFFF
            else:
                tgt = (here + sx(bits(w, 32, 27), 6)) & 0xFFFFFF
            if take:
                if not e:
                    self.compute(c23)
                self.jump(tgt, j, call=bool(b), ret_after=nxt)
                if b and j:
                    self.pcstk[-1] = (self.after_delay(here), self.visa)
            elif e:
                self.compute(c23)
            return
        if sub in (0b01010, 0b01011):                         # type 11
            j, e = bits(w, 26, 26), bits(w, 25, 25)
            take = self.cond(cnd)
            if take:
                if not e:
                    self.compute(c23)
                self.do_return(sub == 0b01011, j)
            elif e:
                self.compute(c23)
            return
        if sub in (0b01100, 0b01101):                         # type 12
            cnt = bits(w, 39, 24) if sub == 0b01100 else self.get_ureg(bits(w, 38, 32))
            end = (here + sx(bits(w, 23, 0), 24)) & 0xFFFFFF
            self.lcntr = cnt
            self.loopstk.append([nxt, end, 15, cnt, self.visa])
            return
        if sub == 0b01110:                                    # type 13
            end = (here + sx(bits(w, 23, 0), 24)) & 0xFFFFFF
            self.loopstk.append([nxt, end, cnd, None, self.visa])
            return
        if sub == 0b01111:                                    # type 17
            self.set_ureg(bits(w, 38, 32), bits(w, 31, 0))
            return
        if sub >> 2 == 0b100:                                 # type 14
            g, d, l = bits(w, 41, 41), bits(w, 40, 40), bits(w, 39, 39)
            ur, addr = bits(w, 38, 32), bits(w, 31, 0) + (1 if self.pey else 0)
            if d:
                self.ureg_store(ur, addr)
            else:
                self.ureg_load(ur, addr)
            return
        if sub == 0b10100:                                    # type 18
            bop, sr, data = bits(w, 39, 37), bits(w, 36, 32), bits(w, 31, 0)
            code = 0x70 | sr
            v = self.get_ureg(code)
            if bop == 0:
                self.set_ureg(code, v | data)
            elif bop == 1:
                self.set_ureg(code, v & ~data)
            elif bop == 2:
                self.set_ureg(code, v ^ data)
            elif bop == 4:
                self.setf('BTF', (v & data) == data)
            elif bop == 5:
                self.setf('BTF', v == data)
            return
        if sub == 0b10110:                                    # type 19
            br, g, idis, isrc = bits(w, 39, 39), bits(w, 38, 38), bits(w, 37, 35), bits(w, 34, 32)
            if g:
                isrc += 8
            data = sx(bits(w, 31, 0), 32)
            idst = isrc ^ idis
            v = (self.I[isrc] + data) & M32
            if br:
                self.I[idst] = int(f'{v:032b}'[::-1], 2)
            else:
                self.I[idst] = self.circ(isrc, v)
            return
        if sub == 0b10111:                                    # type 20
            if bits(w, 39, 39):
                self.loopstk.append([0, 0, 15, self.lcntr, self.visa])
            if bits(w, 38, 38) and self.loopstk:
                self.loopstk.pop()
            if bits(w, 37, 37):
                self.ststk.append((self.sreg['ASTATX'], self.sreg['MODE1']))
            if bits(w, 36, 36) and self.ststk:
                a, m = self.ststk.pop()
                self.sreg['ASTATX'] = a
                self.set_mode1(m)
            if bits(w, 35, 35):
                self.pcstk.append((0, self.visa))
            if bits(w, 34, 34) and self.pcstk:
                self.pcstk.pop()
            return
        if sub == 0b00000:
            if bits(w, 39, 39):
                if not (self.sreg['IRPTL'] & self.sreg['IMASK']):
                    self.pc = here                            # IDLE until an interrupt
                    raise Halt('IDLE with nothing pending')
                return
            return
        if bits(w, 47, 40) == 0x18:                           # CJUMP
            self.R[2] = self.I[6]
            self.I[6] = self.I[7]
            self.jump(bits(w, 23, 0), True, call=True, ret_after=None)
            self.pcstk[-1] = (self.after_delay(here), self.visa)
            return
        if bits(w, 47, 40) == 0x19:                           # RFRAME
            self.I[7] = self.I[6]
            self.I[6] = self.rd(self.I[6])
            return
        raise NotImplementedError(f'instruction {w:012x} at {here:#x}')

    def after_delay(self, here):
        """Return address after a delayed call: skip the two delay-slot instructions."""
        if not self.visa:
            return here + 3
        a = here
        for _ in range(3):
            w = self.p_get(a) << 32 | self.p_get(a + 1) << 16 | self.p_get(a + 2)
            a += D.visa_length(w)
        return a

    def do_return(self, rti, delayed):
        if not self.pcstk:
            raise Halt('return with empty PC stack')
        tgt, vs = self.pcstk.pop()
        if rti:
            for k in range(32):
                if self.sreg['IMASKP'] >> (31 - k) & 1:
                    self.sreg['IMASKP'] &= ~(1 << (31 - k))
                    break
        if delayed:
            self.delay = [3, tgt]
        else:
            self.goto(tgt)

    def shift_imm(self, w):
        op = bits(w, 21, 16) << 2
        rn, rx = bits(w, 7, 4), bits(w, 3, 0)
        data8 = bits(w, 15, 8)
        dataex = bits(w, 30, 27) if bits(w, 47, 40) == 0x02 else bits(w, 30, 27)
        if op in (0x40, 0x48, 0x44, 0x4C, 0x64, 0x6C):
            data = data8 | (dataex << 8)
        elif op in (0xC0, 0xC4, 0xC8, 0xCC):
            data = data8
        else:
            data = data8
        self.shift(op, rn, rx, data, imm=True)

    # ---------------------------------------------------- VISA short/b forms
    def exec_short(self, w, here, nxt):
        p0 = w >> 32
        R = self.R
        if p0 >> 12 == 0b1100:                                # 2c short compute
            op, n, x = bits(w, 43, 40), bits(w, 39, 36), bits(w, 35, 32)
            if op == 0:
                R[n] = self.add(R[n], R[x])
            elif op == 1:
                R[n] = self.sub(R[n], R[x])
            elif op == 2:
                R[n] = R[x]
                self.alu_flags(R[n])
            elif op == 3:
                self.alu(0x0A, 0, n, x)
            elif op == 4:
                R[n] = ~R[x] & M32
                self.alu_flags(R[n])
            elif op == 5:
                R[n] = self.add(R[x], 1)
            elif op == 6:
                R[n] = self.add(R[x], M32)
            elif op == 7:
                R[n] = (s32(R[n]) * s32(R[x])) & M32
            elif op == 8:
                R[n] = self.fstore(u2f(R[n]) + u2f(R[x]))
            elif op == 9:
                R[n] = self.fstore(u2f(R[n]) - u2f(R[x]))
            elif op == 10:
                R[n] = self.fstore(float(s32(R[x])))
            elif op == 11:
                self.alu(0x8A, 0, n, x)
            elif op in (12, 13, 14):
                R[n] = {12: R[n] & R[x], 13: R[n] | R[x], 14: R[n] ^ R[x]}[op]
                self.alu_flags(R[n])
            else:
                R[n] = f2u(u2f(R[n]) * u2f(R[x]))
            return
        if p0 >> 12 == 0b1001:                                # 3c
            dmi, dmm, d, dreg = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37), bits(w, 35, 32)
            a = self.dag(dmi, dmm, True)
            if d:
                self.wr(a, R[dreg])
            else:
                R[dreg] = self.rd(a)
            return
        if p0 >> 8 in (0x0A, 0x0B):                           # 11c
            if self.cond(bits(w, 37, 33)):
                self.do_return(p0 >> 8 == 0x0B, bits(w, 38, 38))
            return
        if p0 == 0x1901:
            self.I[7] = self.I[6]
            self.I[6] = self.rd(self.I[6])
            return
        if bits(w, 39, 39):
            if not (self.sreg['IRPTL'] & self.sreg['IMASK']):
                self.pc = here                                # IDLE until an interrupt
                raise Halt('IDLE with nothing pending')
        return                                                # 21c NOP

    def exec_b(self, w, here, nxt):
        p0 = w >> 32
        top8 = p0 >> 8
        if top8 == 0x01:                                      # 2b
            self.compute(bits(w, 38, 16))
            return
        if top8 == 0x0F:                                      # 17b
            self.set_ureg(bits(w, 38, 32), sx(bits(w, 31, 16), 16) & M32)
            return
        if p0 >> 12 == 0b1001:
            sel = bits(w, 36, 34)
            if sel == 1:                                      # 16b
                i, m, g = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37)
                if g:
                    i, m = i + 8, m + 8
                self.wr(self.dag(i, m, True), sx(bits(w, 31, 16), 16) & M32)
                return
            i, d, l, g = bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39), bits(w, 37, 37)
            ur, data = bits(w, 29, 23), sx(bits(w, 22, 16), 7)
            if g:
                i += 8
            a = (self.I[i] + data) & M32
            if d:
                self.ureg_store(ur, a)
            else:
                self.ureg_load(ur, a)
            return
        # b-forms of 1/3/4/5/7/9: same layout as 48-bit without COMPUTE
        return self.exec48(w & ~0x7FFFFF, here, nxt)


# ------------------------------------------------------------------ peripherals
class Spi:
    """ADSP-214xx SPI / SPIB (IOP 0x1000.. / 0x2800..): SPICTL +0, SPIFLG +1, SPISTAT +2
    (SPIF bit 0, TXS bit 3, RXS bit 5), TXSPI +3, RXSPI +4, SPIBAUD +5, RXSPI_SHADOW +6.
    Transfers complete at once; the attached device (callable byte -> byte) answers, default
    0xFF. Every transfer is logged in .log as (tx, rx)."""
    def __init__(self, base, device=None):
        self.base, self.device = base, device
        self.r = {}
        self.rx = None
        self.log = []

    def rd(self, sim, a):
        o = a - self.base
        if o == 2:
            return 0x01 | (0x20 if self.rx is not None else 0)
        if o in (4, 6):
            v = self.rx if self.rx is not None else 0
            if o == 4:
                self.rx = None
            return v
        return self.r.get(o, 0)

    def wr(self, sim, a, v):
        o = a - self.base
        self.r[o] = v
        if o == 3:                                   # TXSPI: one transfer
            rxv = self.device(v) if self.device else 0xFF
            self.rx = rxv
            self.log.append((v, rxv))


class Twi:
    """ADSP-214xx TWI master (IOP 0x4400..): TWIMCTL +0x14 (MEN bit 0, MDIR bit 2, DCNT
    bits 13..6), TWIMADDR +0x1C, TWIIRPTL +0x20 (RW1C: MCOM bit 4, MERR 5, TXINT 6, RXINT 7),
    TWIIMASK +0x24, TWIFIFOSTAT +0x2C, TXTWI8 +0x80, RXTWI8 +0x88. Bytes move at once; the
    slave is a dict addr -> callable(kind, byte) (kind 'w' write / 'r' read) or absent, in
    which case writes are accepted and reads return 0. TWI raises DPI_IRPTL bit DPI_BIT and,
    when DPI_IMASK_RE has it, core P14I (IRPTL bit 17). Transfers are logged in .log."""
    DPI_BIT = 6          # [INF] the firmware unmasks only this DPI bit while using the TWI

    def __init__(self, base=0x4400, slaves=None):
        self.base, self.slaves = base, slaves or {}
        self.r = {}
        self.txq, self.rxq = [], []
        self.left = 0
        self.log = []

    def irq(self, sim, bits):
        self.r[0x20] = self.r.get(0x20, 0) | bits
        if self.r[0x20] & self.r.get(0x24, 0):
            sim.dpi_irptl |= 1 << self.DPI_BIT
            if sim.iop_regs.get(0x1C35, 0) >> self.DPI_BIT & 1:
                sim.sreg['IRPTL'] |= 1 << 17

    def slave(self, kind, v=0):
        a = self.r.get(0x1C, 0) & 0x7F
        f = self.slaves.get(a)
        out = f(kind, v) if f else 0
        self.log.append((a, kind, v if kind == 'w' else out))
        return out & 0xFF

    def pump(self, sim):
        """Move bytes while a master transfer is active."""
        ctl = self.r.get(0x14, 0)
        if not ctl & 1:
            return
        if ctl & 4:                                   # master receive
            while self.left and len(self.rxq) < 2:
                self.rxq.append(self.slave('r'))
                self.left -= 1
            if self.rxq:
                self.irq(sim, 0x80)
        else:
            while self.left and self.txq:
                self.slave('w', self.txq.pop(0))
                self.left -= 1
            if self.left:
                self.irq(sim, 0x40)
        if not self.left and not (ctl & 4 and self.rxq):
            self.r[0x14] = ctl & ~1
            self.irq(sim, 0x10)

    def rd(self, sim, a):
        o = a - self.base
        if o == 0x2C:
            lvl = lambda q: (0, 1, 3)[min(len(q), 2)]
            return lvl(self.txq) | lvl(self.rxq) << 2
        if o in (0x88, 0x8C):
            v = self.rxq.pop(0) if self.rxq else 0
            self.pump(sim)
            return v
        return self.r.get(o, 0)

    def wr(self, sim, a, v):
        o = a - self.base
        if o == 0x20:
            self.r[0x20] = self.r.get(0x20, 0) & ~v
            if not self.r[0x20] & self.r.get(0x24, 0):
                sim.dpi_irptl &= ~(1 << self.DPI_BIT)
            return
        if o in (0x80, 0x84):
            self.txq += [v & 0xFF] if o == 0x80 else [v & 0xFF, v >> 8 & 0xFF]
            self.pump(sim)
            return
        self.r[o] = v
        if o == 0x14 and v & 1:
            n = v >> 6 & 0xFF
            self.left = 1 << 30 if n == 0xFF else n
            self.log.append((self.r.get(0x1C, 0) & 0x7F, 'S', 'rd' if v & 4 else 'wr', n))
            self.pump(sim)
        elif o == 0x14 and v & 0x10 and self.left:              # STOP: end the transfer now
            self.left = 0
            self.r[0x14] = v | 1
            self.pump(sim)


class Dpi:
    """DPI_IRPTL (IOP 0x1C32): read-only latch of DPI peripheral interrupts, cleared on read."""
    def rd(self, sim, a):
        v = sim.dpi_irptl
        sim.dpi_irptl = 0
        return v

    def wr(self, sim, a, v):
        pass


# ------------------------------------------------------------------ fast-forward hooks
def ff_delay_loop(sim):
    """0x1275CD: `for (i = 0; i < 0x5DC0; i++);` busy delay on a stack local (DM(I6-2)).
    Jump the counter to its end value; the loop then exits on its own compare."""
    a = (sim.I[6] - 2) & M32
    if s32(sim.rd(a)) < 0x5DC0:
        sim.wr(a, 0x5DC0)


HLE = {0x1275EA: ff_delay_loop}


# ------------------------------------------------------------------ loading / main
def load_image(sim, img):
    ws = L.stream_words(img)
    for tag, cnt, addr, pl in L.blocks(ws):
        if tag == 1:
            for k in range(cnt):
                sim.wr(addr + k, 0)
        elif tag == 2:
            for k in range(cnt):
                sim.wr48(addr + k, 0)
        elif tag == 3:
            par = []
            for w in pl:
                par += [w & 0xFFFF, w >> 16]
            for k in range(cnt):
                sim.p_set(addr + k, par[k])
        elif tag == 4:
            for k in range(cnt):
                sim.wr(addr + k, pl[k])
        elif tag == 5:
            for k, w48 in enumerate(L.unpack48(pl)[:cnt]):
                sim.wr48(addr + k, w48)
        elif tag == 6:
            for k in range(cnt):
                sim.wr(addr + 2 * k, pl[2 * k])
                sim.wr(addr + 2 * k + 1, pl[2 * k + 1])
        elif tag == 0:
            for k, w48 in enumerate(L.unpack48(pl)[:256]):
                sim.wr48(0x8C000 + k, w48)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--steps', type=int, default=100000)
    ap.add_argument('--trace', type=int, default=0)
    a = ap.parse_args()
    sim = Sharc()
    load_image(sim, open(a.image, 'rb').read())
    sim.goto(0x8C004)
    sim.hle.update(HLE)
    for base in (0x1000, 0x2800):
        sim.iop[base] = Spi(base)
    sim.iop[0x4400] = Twi()
    sim.iop[0x1C32] = Dpi()
    if a.trace:
        sim.trace = []
    try:
        for _ in range(a.steps):
            sim.step()
        print('ran', a.steps, 'steps; pc', hex(sim.pc), 'visa' if sim.visa else 'isa')
    except (NotImplementedError, Halt, KeyError, IndexError) as e:
        print('stopped at', hex(sim.pc), 'after', sim.cycles, 'steps:', repr(e))
    if a.trace:
        for pc, vs in sim.trace[-a.trace:]:
            w = sim.p_get(pc) << 32 | sim.p_get(pc + 1) << 16 | sim.p_get(pc + 2) if vs else sim.rd48(pc)
            s = D.decode_visa(w, pc)[2] if vs else D.text(w, pc)
            print(f'  {pc:06x} {"V" if vs else "I"} {s}')
    print('IOP writes:', {hex(k): hex(v) for k, v in list(sim.iop_regs.items())[:40]})
