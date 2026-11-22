#!/usr/bin/env python3
"""TMS320C55x system simulator for the IC-705 DV DSP (whole-firmware, not single routines).

Instruction decoding is taken from IDA (TMS32055 module): tools export a table of
{ea, size, mnem, operand text + numeric fields} for every code head (see
ic705/EMULATION.md "DV DSP simulator"). This file implements the *semantics*: CPU state,
operand addressing (direct, indirect with post/pre modification, circular, stack-relative,
MMR, I/O space), 40-bit accumulators with SXMD/M40/SATD/FRCT, repeat single / block
repeat, conditional execution, parallel pairs, call/return on the dual stacks, and
maskable interrupts. Peripherals are pluggable Python objects mapped into I/O space.

Unknown instruction forms raise NotImplementedError, so a gap is never silently
mis-executed. Vendor code and data are not part of this file: the memory image and the
decode table are loaded at runtime from the user's own decoded firmware.

Conventions: program addresses are byte addresses (as IDA shows them); data space is
16-bit words, byte address = 2 * word address, words big-endian in the image.
"""
import json
import re
import struct
import sys
from array import array

M16, M40 = 0xFFFF, (1 << 40) - 1
WMEM = 0x80000          # data words modelled (C5535-class: < 0x28000 used)


def s16(v):
    v &= M16
    return v - 0x10000 if v & 0x8000 else v


def s40(v):
    v &= M40
    return v - (1 << 40) if v >> 39 else v


def num(t):
    """IDA number text: 0Fh / 1Eh / -2800h / 12."""
    t = t.strip()
    neg = t.startswith('-')
    if neg:
        t = t[1:]
    v = int(t[:-1], 16) if t.lower().endswith('h') else int(t, 0)
    return -v if neg else v


# status register bit positions (TMS320C55x CPU reference)
ST0 = {'ACOV2': 15, 'ACOV3': 14, 'TC1': 13, 'TC2': 12, 'CARRY': 11, 'ACOV0': 10, 'ACOV1': 9}
ST1 = {'BRAF': 15, 'CPL': 14, 'XF': 13, 'HM': 12, 'INTM': 11, 'M40': 10, 'SATD': 9,
       'SXMD': 8, 'C16': 7, 'FRCT': 6, 'C54CM': 5}
ST2 = {'ARMS': 15, 'DBGM': 12, 'EALLOW': 11, 'RDM': 10, 'CDPLC': 8,
       **{f'AR{i}LC': i for i in range(8)}}
ST3 = {'CAFRZ': 15, 'CAEN': 14, 'CACLR': 13, 'HINT': 12, 'CBERR': 7, 'MPNMC': 6, 'SATA': 5,
       'CLKOFF': 2, 'SMUL': 1, 'SST': 0}
BITREG = {**{k: 'ST0' for k in ST0}, **{k: 'ST1' for k in ST1}, **{k: 'ST2' for k in ST2},
          **{k: 'ST3' for k in ST3}}
BITPOS = {**ST0, **ST1, **ST2, **ST3}

# memory-mapped register word addresses
MMR = {'IER0': 0x00, 'IFR0': 0x01, 'ST0_55': 0x02, 'ST1_55': 0x03, 'ST3_55': 0x04,
       'ST0': 0x06, 'ST1': 0x07, 'AC0L': 0x08, 'AC0H': 0x09, 'AC0G': 0x0A, 'AC1L': 0x0B,
       'AC1H': 0x0C, 'AC1G': 0x0D, 'T3': 0x0E, 'TRN0': 0x0F, 'AR0': 0x10, 'AR1': 0x11,
       'AR2': 0x12, 'AR3': 0x13, 'AR4': 0x14, 'AR5': 0x15, 'AR6': 0x16, 'AR7': 0x17,
       'SP': 0x18, 'BK03': 0x19, 'BRC0': 0x1A, 'RSA0L': 0x1B, 'REA0L': 0x1C, 'PMST': 0x1D,
       'XPC': 0x1E, 'T0': 0x20, 'T1': 0x21, 'T2': 0x22, 'AC2L': 0x24, 'AC2H': 0x25,
       'AC2G': 0x26, 'CDP': 0x27, 'AC3L': 0x28, 'AC3H': 0x29, 'AC3G': 0x2A, 'DPH': 0x2B,
       'DP': 0x2E, 'PDP': 0x2F, 'BK47': 0x30, 'BKC': 0x31, 'BSA01': 0x32, 'BSA23': 0x33,
       'BSA45': 0x34, 'BSA67': 0x35, 'BSAC': 0x36, 'TRN1': 0x38, 'BRC1': 0x39, 'BRS1': 0x3A,
       'CSR': 0x3B, 'RSA0H': 0x3C, 'RSA1H': 0x3E, 'RSA1L': 0x3F, 'REA0H': 0x3D,
       'REA1H': 0x40, 'REA1L': 0x41, 'RPTC': 0x42, 'IER1': 0x43, 'IFR1': 0x44,
       'DBIER0': 0x45, 'DBIER1': 0x46, 'IVPD': 0x49, 'IVPH': 0x4A, 'ST2_55': 0x4B,
       'SSP': 0x4C, 'SP_': 0x4D, 'SPH': 0x4E, 'CDPH': 0x4F}
MMR_NAME = {v: k for k, v in MMR.items()}

COND_RE = re.compile(r'^(.+?)\s*(==|!=|<=|>=|<|>)\s*(.+)$')


class Halt(Exception):
    pass


class C55Sim:
    def __init__(self, image, insns, entry):
        self.mem = array('H', [0]) * WMEM
        for i in range(min(len(image) // 2, WMEM)):
            self.mem[i] = struct.unpack_from('>H', image, 2 * i)[0]
        self.code = {r['ea']: r for r in insns}
        for r in insns:
            r['base'] = r['mn'][3:] if r['mn'].startswith('|| ') else r['mn']
            r['par'] = r['mn'].startswith('|| ')
        # IDA reports some dual (":: MOV") forms one byte short and leaves the trailing
        # byte undecoded: absorb a single gap byte before the next decoded instruction
        for r in insns:
            e = r['ea'] + r['size']
            if e not in self.code and (e + 1) in self.code and len(r['ops']) == 5:
                r['size'] += 1
        self.pc = entry
        self.ac = [0] * 4
        self.t = [0] * 4
        self.xar = [0] * 8
        self.reg = {k: 0 for k in ('XSP', 'XSSP', 'XCDP', 'BRC0', 'BRC1', 'BRS1', 'CSR',
                                   'RSA0', 'REA0', 'RSA1', 'REA1', 'RPTC', 'TRN0', 'TRN1',
                                   'BK03', 'BK47', 'BKC', 'BSA01', 'BSA23', 'BSA45', 'BSA67',
                                   'BSAC', 'IER0', 'IER1', 'IFR0', 'IFR1', 'DBIER0', 'DBIER1',
                                   'IVPD', 'IVPH', 'DP', 'PDP', 'ST0', 'ST1', 'ST2', 'ST3',
                                   'XDP')}
        self.reg['ST1'] = (1 << ST1['INTM']) | (1 << ST1['SXMD'])  # reset: interrupts off
        self.reg['ST3'] = 0
        self.reg['IVPD'] = self.reg['IVPH'] = 0xFFFF
        self.ports = {}            # base -> object with rd(addr)/wr(addr, v)
        self.io_last = {}
        self.idle = False
        self.port_log = None
        self.rpt_left = 0
        self.rpt_ea = None
        self.blk = []              # active block repeats: [start, end_ea, level]
        self.cycles = 0
        self.hle = {}
        self.trace = None

    # ------------------------------------------------------------ status bits
    def bit(self, name):
        return (self.reg[BITREG[name]] >> BITPOS[name]) & 1

    def setbit(self, name, v):
        r, p = BITREG[name], BITPOS[name]
        self.reg[r] = (self.reg[r] & ~(1 << p)) | ((1 if v else 0) << p)

    # ------------------------------------------------------------ memory
    def rd(self, w):
        w &= 0x7FFFFF
        if w < 0x60:
            return self.mmr_rd(w)
        return self.mem[w] if w < WMEM else 0

    def wr(self, w, v):
        w &= 0x7FFFFF
        if w < 0x60:
            return self.mmr_wr(w, v & M16)
        if w < WMEM:
            self.mem[w] = v & M16

    def mmr_rd(self, w):
        n = MMR_NAME.get(w)
        if n is None:
            return self.mem[w]
        return self.get_named(n)

    def mmr_wr(self, w, v):
        n = MMR_NAME.get(w)
        if n is None:
            self.mem[w] = v
        else:
            self.set_named(n, v)

    def get_named(self, n):
        if n in ('ST0_55', 'ST0'):
            return self.reg['ST0'] | (self.reg['DP'] >> 7 & 0x1FF if n == 'ST0' else 0)
        if n in ('ST1_55', 'ST1'):
            return self.reg['ST1']
        if n == 'ST2_55':
            return self.reg['ST2']
        if n == 'ST3_55':
            return self.reg['ST3']
        m = re.fullmatch(r'AC(\d)([LHG])', n)
        if m:
            a = self.ac[int(m.group(1))] & M40
            return {'L': a & M16, 'H': a >> 16 & M16, 'G': a >> 32 & 0xFF}[m.group(2)]
        if re.fullmatch(r'AR\d', n):
            return self.xar[int(n[2])] & M16
        if re.fullmatch(r'T\d', n):
            return self.t[int(n[1])] & M16
        if n in ('SP', 'SP_'):
            return self.reg['XSP'] & M16
        if n == 'SSP':
            return self.reg['XSSP'] & M16
        if n == 'SPH':
            return self.reg['XSP'] >> 16 & 0x7F
        if n == 'CDP':
            return self.reg['XCDP'] & M16
        if n == 'CDPH':
            return self.reg['XCDP'] >> 16 & 0x7F
        if n in ('RSA0L', 'REA0L', 'RSA1L', 'REA1L'):
            return self.reg[n[:-1]] & M16
        if n in ('RSA0H', 'REA0H', 'RSA1H', 'REA1H'):
            return self.reg[n[:-1]] >> 16 & 0xFF
        return self.reg.get(n, 0) & M16

    def set_named(self, n, v):
        v &= M16
        if n in ('ST0_55', 'ST0'):
            self.reg['ST0'] = v & 0xFE00 if n == 'ST0_55' else v
            return
        if n in ('ST1_55', 'ST1'):
            self.reg['ST1'] = v
            return
        if n == 'ST2_55':
            self.reg['ST2'] = v
            return
        if n == 'ST3_55':
            self.reg['ST3'] = v
            return
        m = re.fullmatch(r'AC(\d)([LHG])', n)
        if m:
            i = int(m.group(1))
            a = self.ac[i] & M40
            sh = {'L': 0, 'H': 16, 'G': 32}[m.group(2)]
            mask = 0xFF if sh == 32 else M16
            a = (a & ~(mask << sh)) | ((v & mask) << sh)
            self.ac[i] = a & M40
            return
        if re.fullmatch(r'AR\d', n):
            i = int(n[2])
            self.xar[i] = (self.xar[i] & 0x7F0000) | v
            return
        if re.fullmatch(r'T\d', n):
            self.t[int(n[1])] = v
            return
        if n in ('SP', 'SP_'):
            self.reg['XSP'] = (self.reg['XSP'] & 0x7F0000) | v
            return
        if n == 'SSP':
            self.reg['XSSP'] = (self.reg['XSSP'] & 0x7F0000) | v
            return
        if n == 'SPH':
            self.reg['XSP'] = (self.reg['XSP'] & M16) | (v & 0x7F) << 16
            self.reg['XSSP'] = (self.reg['XSSP'] & M16) | (v & 0x7F) << 16
            return
        if n == 'CDP':
            self.reg['XCDP'] = (self.reg['XCDP'] & 0x7F0000) | v
            return
        if n == 'CDPH':
            self.reg['XCDP'] = (self.reg['XCDP'] & M16) | (v & 0x7F) << 16
            return
        if n in ('IFR0', 'IFR1'):          # write 1 to clear
            self.reg[n] &= ~v & M16
            return
        if n in ('RSA0L', 'REA0L', 'RSA1L', 'REA1L'):
            k = n[:-1]
            self.reg[k] = (self.reg[k] & 0xFF0000) | v
            return
        if n in ('RSA0H', 'REA0H', 'RSA1H', 'REA1H'):
            k = n[:-1]
            self.reg[k] = (self.reg[k] & M16) | (v & 0xFF) << 16
            return
        self.reg[n] = v

    # ------------------------------------------------------------ I/O space
    def io_rd(self, a):
        dev = self.ports.get(a & 0xFF00) or self.ports.get(a)
        v = dev.rd(a) if dev else 0
        if self.port_log is not None:
            self.port_log.append(('r', a, v, self.pc))
        return v & M16

    def io_wr(self, a, v):
        self.io_last[a] = v & M16
        dev = self.ports.get(a & 0xFF00) or self.ports.get(a)
        if dev:
            dev.wr(a, v & M16)
        if self.port_log is not None:
            self.port_log.append(('w', a, v & M16, self.pc))

    # ------------------------------------------------------------ addressing
    def circ_ok(self, n):
        return n < 8 and self.bit(f'AR{n}LC')

    def ar_update(self, n, new):
        """Post/pre modification of ARn (16-bit, circular if ARnLC)."""
        if self.circ_ok(n):
            bk = self.reg['BK03'] if n < 4 else self.reg['BK47']
            bsa = self.reg[('BSA01', 'BSA01', 'BSA23', 'BSA23', 'BSA45', 'BSA45',
                            'BSA67', 'BSA67')[n]]
            bk = bk or 1
            new = new % bk
            self.xar[n] = (self.xar[n] & 0x7F0000) | (new & M16)
            return
        self.xar[n] = (self.xar[n] & 0x7F0000) | (new & M16)

    def ar_addr(self, n, off=0):
        """Effective word address of *ARn(+off), honouring circular mode."""
        if n == 'CDP':
            return (self.reg['XCDP'] + off) & 0x7FFFFF
        if self.circ_ok(n):
            bk = (self.reg['BK03'] if n < 4 else self.reg['BK47']) or 1
            bsa = self.reg[('BSA01', 'BSA01', 'BSA23', 'BSA23', 'BSA45', 'BSA45',
                            'BSA67', 'BSA67')[n]]
            lo = (self.xar[n] & M16)
            return (self.xar[n] & 0x7F0000) + ((bsa + (lo + off) % bk) & M16)
        return (self.xar[n] + off) & 0x7FFFFF

    def parse(self, op, ins):
        """Return a parsed operand tuple (cached per instruction)."""
        text, typ, val, addr = op[0], op[1], op[2], op[3]
        t = text.strip()
        m = re.fullmatch(r'(.*\S)\s*<<\s*(#?[-\w]+)', t)
        if m and not t.startswith('#'):
            inner = self.parse([m.group(1), typ, val, addr] + op[4:], ins)
            sh = m.group(2)
            return ('shift', inner, ('imm', num(sh[1:])) if sh.startswith('#')
                    else self.parse([sh, 1, 0, 0], ins))
        m = re.fullmatch(r'(.*\S)\s*>>\s*#([-\w]+)', t)
        if m and not t.startswith('#'):
            inner = self.parse([m.group(1), typ, val, addr] + op[4:], ins)
            return ('shift', inner, ('imm', -num(m.group(2))))
        if t.startswith('#'):
            body = t[1:]
            m = re.fullmatch(r'(-?[0-9A-Fa-f]+h?)\s*<<\s*#(\d+)', body)
            if m:
                return ('imm', num(m.group(1)) << int(m.group(2)))
            try:
                return ('imm', num(body))
            except ValueError:
                return ('imm', val if typ == 5 else (addr or val))
        for wrap in ('dbl', 'uns', 'hi', 'lo', 'low_byte', 'high_byte', 'rnd', 'saturate',
                     'dual', 'pair'):
            if t.startswith(wrap + '(') and t.endswith(')'):
                return (wrap, self.parse([t[len(wrap) + 1:-1], typ, val, addr] + op[4:], ins))
        m = re.fullmatch(r'mmap\(@(\w+)\)', t)
        if m:
            return ('mmr', m.group(1))
        m = re.fullmatch(r'port\(#(.+)\)', t)
        if m:
            try:
                return ('port', num(m.group(1)))
            except ValueError:
                return ('port', addr or val)
        m = re.fullmatch(r'AC([0-3])', t)
        if m:
            return ('ac', int(m.group(1)))
        m = re.fullmatch(r'T([0-3])', t)
        if m:
            return ('t', int(m.group(1)))
        m = re.fullmatch(r'X?AR([0-7])', t)
        if m:
            return ('xar' if t.startswith('X') else 'ar', int(m.group(1)))
        if t in ('SP', 'XSP', 'SSP', 'XSSP', 'CDP', 'XCDP', 'DP', 'XDP', 'BRC0', 'BRC1',
                 'CSR', 'TRN0', 'TRN1', 'RPTC', 'BSA01', 'BSA23', 'BSA45', 'BSA67', 'BSAC',
                 'BK03', 'BK47', 'BKC', 'PDP', 'RETA', 'IVPD', 'IVPH'):
            return ('named', t)
        if t in BITPOS:
            return ('bit', t)
        m = re.fullmatch(r'@(\w+)', t)
        if m:
            try:
                return ('dpmem', num(m.group(1)))
            except ValueError:
                return ('dpmem', addr)
        # memory operands
        m = re.fullmatch(r'\*\(#(.+)\)', t)
        if m:
            try:
                return ('mem', 'abs', num(m.group(1)))
            except ValueError:
                return ('mem', 'abs', addr)
        m = re.fullmatch(r'\*SP\(#(.+)\)', t)
        if m:
            try:
                k = num(m.group(1))
            except ValueError:
                k = val
            return ('mem', 'sp', k)
        m = re.fullmatch(r'\*(\+|-)?(AR([0-7])|CDP)(\+|-)?(?:\((short\()?#?([^)]+?)\)?\))?', t)
        if m:
            pre, reg, n, post, short, off = m.groups()
            n = int(n) if n is not None else 'CDP'
            if off is not None:
                tm = re.fullmatch(r'T([0-3])', off)
                if tm:
                    return ('mem', 'ar', n, pre, post, ('t', int(tm.group(1))))
                try:
                    o = num(off)
                except ValueError:
                    o = val
                return ('mem', 'ar', n, pre, post, ('imm', o))
            return ('mem', 'ar', n, pre, post, None)
        m = re.fullmatch(r'\*\((AR([0-7])|CDP)([+-])(T[0-3]|AR0)\)', t)
        if m:
            reg, n, sign, idx = m.groups()
            n = int(n) if n is not None else 'CDP'
            ix = ('t', int(idx[1])) if idx.startswith('T') else ('ar', 0)
            return ('mem', 'armod', n, sign, ix)
        m = re.fullmatch(r'T([0-3])=(.+)', t)
        if m:
            return ('tload', int(m.group(1)), self.parse([m.group(2), typ, val, addr], ins))
        m = COND_RE.match(t)
        if m:
            return ('cmp', self.parse([m.group(1), 1, val, addr], ins), m.group(2),
                    self.parse([m.group(3), typ, val, addr], ins))
        if t.startswith('!') and t[1:] in BITPOS:
            return ('notbit', t[1:])
        m = re.fullmatch(r'overflow\(AC([0-3])\)', t)
        if m:
            return ('ovf', int(m.group(1)))
        if '|' in t or '&' in t:
            return ('logic', t)
        if typ in (7, 6):                                 # code reference
            return ('code', addr)
        if re.fullmatch(r'(loc|locret|sub|nullsub)_[0-9A-F]+|\w+', t) and addr:
            return ('code', addr)
        raise NotImplementedError(f'operand {t!r} in {ins["text"]!r} @{ins["ea"]:#x}')

    def ops(self, ins):
        p = ins.get('_p')
        if p is None:
            p = ins['_p'] = [self.parse(o, ins) for o in ins['ops']]
        return p

    def mem_ea(self, o, commit=True):
        """Word address of a memory operand; applies pointer modification if commit."""
        kind = o[1]
        if kind == 'abs':
            return o[2] & 0x7FFFFF
        if kind == 'sp':
            return (self.reg['XSP'] + o[2]) & 0x7FFFFF
        if kind == 'ar':
            _, _, n, pre, post, off = o
            step = 1
            if off is not None:
                d = off[1] if off[0] == 'imm' else s16(self.t[off[1]])
                if pre == '+':          # *+ARn(#k): pre-add, keep
                    cur = self.get_arlo(n)
                    if commit:
                        self.set_arlo(n, cur + d)
                    return self.ar_addr(n)
                return self.ar_addr(n, d)
            if pre in ('+', '-'):
                if commit:
                    self.set_arlo(n, self.get_arlo(n) + (step if pre == '+' else -step))
                return self.ar_addr(n)
            ea = self.ar_addr(n)
            if post and commit:
                self.set_arlo(n, self.get_arlo(n) + (step if post == '+' else -step))
            return ea
        if kind == 'armod':
            _, _, n, sign, ix = o
            d = s16(self.t[ix[1]]) if ix[0] == 't' else s16(self.xar[0])
            ea = self.ar_addr(n)
            if commit:
                self.set_arlo(n, self.get_arlo(n) + (d if sign == '+' else -d))
            return ea
        raise NotImplementedError(f'mem kind {kind}')

    def get_arlo(self, n):
        if n == 'CDP':
            return self.reg['XCDP'] & M16
        return self.xar[n] & M16

    def set_arlo(self, n, v):
        if n == 'CDP':
            self.reg['XCDP'] = (self.reg['XCDP'] & 0x7F0000) | (v & M16)
        elif self.circ_ok(n):
            bk = (self.reg['BK03'] if n < 4 else self.reg['BK47']) or 1
            self.xar[n] = (self.xar[n] & 0x7F0000) | ((v % bk) & M16)
        else:
            self.xar[n] = (self.xar[n] & 0x7F0000) | (v & M16)

    # ------------------------------------------------------------ operand read/write
    def is_acc(self, o):
        return o[0] == 'ac'

    def read(self, o, wide=False):
        """Read an operand. 16-bit sources return raw 16-bit unless wide (sign-extend)."""
        k = o[0]
        if k == 'imm':
            return o[1]
        if k == 'ac':
            return self.ac[o[1]]
        if k == 't':
            return self.t[o[1]] & M16
        if k == 'ar':
            return self.xar[o[1]] & M16
        if k == 'xar':
            return self.xar[o[1]]
        if k == 'named':
            n = o[1]
            if n in ('XSP', 'XSSP', 'XCDP', 'XDP'):
                return self.reg[n]
            return self.get_named(n)
        if k == 'mmr':
            return self.get_named(o[1])
        if k == 'mem':
            return self.rd(self.mem_ea(o))
        if k == 'dpmem':
            return self.rd(self.reg['DP'] + o[1])
        if k == 'port':
            return self.io_rd(o[1])
        if k == 'dbl':
            inner = o[1]
            if inner[0] == 'ac':
                return self.ac[inner[1]] & 0xFFFFFFFF
            if inner[0] == 'mmr':
                n = inner[1]
                return (self.get_named(n) << 16) | self.get_named(n[:-1] + 'L')
            ea = self.mem_ea_dbl(inner)
            return (self.rd(ea) << 16) | self.rd(ea + 1)
        if k == 'uns':
            return self.read(o[1]) & M16
        if k == 'hi':
            v = self.read(o[1])
            return (v >> 16) & M16
        if k == 'low_byte':
            return self.read(o[1]) & 0xFF
        if k == 'high_byte':
            return self.read(o[1]) >> 8 & 0xFF
        if k == 'rnd':
            return self.read(o[1])
        if k == 'saturate':
            return self.read(o[1])
        if k == 'shift':
            v = self.read_ext(o[1])
            s = self.shamt(o[2])
            return v << s if s >= 0 else v >> -s
        if k == 'bit':
            return self.bit(o[1])
        if k == 'tload':
            v = self.read(o[2])
            self.post_t.append((o[1], v))
            return v
        raise NotImplementedError(f'read {o}')

    def mem_ea_dbl(self, o):
        """Address of a 32-bit memory access; *ARn+ steps by 2."""
        if o[0] != 'mem':
            raise NotImplementedError(f'dbl {o}')
        if o[1] == 'ar' and o[5] is None and (o[3] or o[4]):
            _, _, n, pre, post, _ = o
            if pre:
                self.set_arlo(n, self.get_arlo(n) + (2 if pre == '+' else -2))
                return self.ar_addr(n)
            ea = self.ar_addr(n)
            self.set_arlo(n, self.get_arlo(n) + (2 if post == '+' else -2))
            return ea
        return self.mem_ea(o)

    def shamt(self, o):
        if o[0] == 'imm':
            return o[1]
        v = s16(self.read(o))
        return max(-32, min(31, v))

    def read_ext(self, o):
        """Read as a signed value suitable for accumulator arithmetic."""
        k = o[0]
        if k == 'ac':
            return s40(self.ac[o[1]])
        if k == 'imm':
            return o[1]
        if k == 'uns':
            return self.read(o[1]) & M16
        if k == 'dbl':
            v = self.read(o) & 0xFFFFFFFF
            return v - (1 << 32) if v >> 31 and self.bit('SXMD') else v
        if k == 'hi':
            return s16(self.read(o))
        if k == 'shift':
            return self.read(o)
        if k in ('low_byte', 'high_byte'):
            return self.read(o)
        v = self.read(o)
        if k == 'xar':
            return v
        return s16(v) if self.bit('SXMD') else v & M16

    def write(self, o, v):
        k = o[0]
        if k == 'ac':
            self.ac[o[1]] = v & M40
        elif k == 't':
            self.t[o[1]] = v & M16
        elif k == 'ar':
            self.xar[o[1]] = (self.xar[o[1]] & 0x7F0000) | (v & M16)
        elif k == 'xar':
            self.xar[o[1]] = v & 0x7FFFFF
        elif k == 'named':
            n = o[1]
            if n in ('XSP', 'XSSP', 'XCDP', 'XDP'):
                self.reg[n] = v & 0x7FFFFF
            else:
                self.set_named(n, v)
        elif k == 'mmr':
            self.set_named(o[1], v)
        elif k == 'mem':
            self.wr(self.mem_ea(o), v)
        elif k == 'dpmem':
            self.wr(self.reg['DP'] + o[1], v)
        elif k == 'port':
            self.io_wr(o[1], v)
        elif k == 'dbl':
            inner = o[1]
            if inner[0] == 'ac':
                self.ac[inner[1]] = self.sx32(v)
            elif inner[0] == 'mmr':
                n = inner[1]
                self.set_named(n, v >> 16)
                self.set_named(n[:-1] + 'L', v)
            else:
                ea = self.mem_ea_dbl(inner)
                self.wr(ea, v >> 16)
                self.wr(ea + 1, v)
        elif k == 'hi':
            inner = o[1]
            if inner[0] == 'ac':
                a = self.ac[inner[1]]
                self.ac[inner[1]] = (a & ~(M16 << 16)) | ((v & M16) << 16)
            else:
                raise NotImplementedError(f'write {o}')
        elif k == 'bit':
            self.setbit(o[1], v)
        elif k == 'low_byte':
            self.write(o[1], v & 0xFF)
        else:
            raise NotImplementedError(f'write {o}')

    def sx32(self, v):
        v &= 0xFFFFFFFF
        if v >> 31 and self.bit('SXMD'):
            v -= 1 << 32
        return v & M40

    def acc_store(self, i, v):
        """Write an arithmetic result to ACi with M40/SATD overflow handling."""
        lim = 39 if self.bit('M40') else 31
        hi, lo = (1 << lim) - 1, -(1 << lim)
        if v > hi or v < lo:
            self.setbit(('ACOV0', 'ACOV1', 'ACOV2', 'ACOV3')[i], 1)
            if self.bit('SATD'):
                v = hi if v > hi else lo
        self.ac[i] = v & M40

    # ------------------------------------------------------------ conditions
    def cond(self, o):
        k = o[0]
        if k == 'bit':
            return self.bit(o[1]) == 1
        if k == 'notbit':
            return self.bit(o[1]) == 0
        if k == 'cmp':
            a, op, b = o[1], o[2], o[3]
            if a[0] == 'ac':
                x = s40(self.ac[a[1]]) if self.bit('M40') else s40(self.ac[a[1]])
                y = b[1] if b[0] == 'imm' else s40(self.ac[b[1]]) if b[0] == 'ac' else self.read_ext(b)
            elif a[0] in ('ar', 't'):
                x = s16(self.read(a))
                y = b[1] if b[0] == 'imm' else s16(self.read(b)) if b[0] in ('ar', 't') else self.read_ext(b)
            else:
                x = s16(self.read(a))
                y = b[1] if b[0] == 'imm' else s16(self.read(b))
            return {'==': x == y, '!=': x != y, '<': x < y, '<=': x <= y,
                    '>': x > y, '>=': x >= y}[op]
        if k == 'ovf':
            i = o[1]
            f = ('ACOV0', 'ACOV1', 'ACOV2', 'ACOV3')[i]
            r = self.bit(f)
            self.setbit(f, 0)
            return r == 1
        if k == 'logic':
            t = o[1].replace(' ', '')
            env = {n: self.bit(n) for n in ('TC1', 'TC2', 'CARRY')}
            expr = re.sub(r'!(\w+)', r'(not \1)', t).replace('&', ' and ').replace('|', ' or ')
            return bool(eval(expr, {}, env))
        raise NotImplementedError(f'cond {o}')

    # ------------------------------------------------------------ stack
    def push_sp(self, v):
        self.reg['XSP'] = (self.reg['XSP'] & 0x7F0000) | ((self.reg['XSP'] - 1) & M16)
        self.wr(self.reg['XSP'], v)

    def pop_sp(self):
        v = self.rd(self.reg['XSP'])
        self.reg['XSP'] = (self.reg['XSP'] & 0x7F0000) | ((self.reg['XSP'] + 1) & M16)
        return v

    def push_ssp(self, v):
        self.reg['XSSP'] = (self.reg['XSSP'] & 0x7F0000) | ((self.reg['XSSP'] - 1) & M16)
        self.wr(self.reg['XSSP'], v)

    def pop_ssp(self):
        v = self.rd(self.reg['XSSP'])
        self.reg['XSSP'] = (self.reg['XSSP'] & 0x7F0000) | ((self.reg['XSSP'] + 1) & M16)
        return v

    def do_call(self, target, ret):
        self.push_sp(ret & M16)
        self.push_ssp(ret >> 16 & 0xFF)
        self.pc = target

    def do_ret(self):
        hi = self.pop_ssp()
        lo = self.pop_sp()
        self.pc = ((hi & 0xFF) << 16) | lo

    # ------------------------------------------------------------ interrupts
    def raise_irq(self, n):
        if n < 16:
            self.reg['IFR0'] |= 1 << n
        else:
            self.reg['IFR1'] |= 1 << (n - 16)

    def pending_irq(self):
        return bool((self.reg['IFR0'] & self.reg['IER0']) or (self.reg['IFR1'] & self.reg['IER1']))

    def take_irq(self):
        if self.bit('INTM'):
            return False
        p0 = self.reg['IFR0'] & self.reg['IER0']
        p1 = self.reg['IFR1'] & self.reg['IER1']
        if not (p0 or p1):
            return False
        n = (p0 & -p0).bit_length() - 1 if p0 else 16 + (p1 & -p1).bit_length() - 1
        if n < 16:
            self.reg['IFR0'] &= ~(1 << n)
        else:
            self.reg['IFR1'] &= ~(1 << (n - 16))
        # context: ST0/ST1 + return address (fast-return layout simplified)
        self.push_sp(self.reg['ST1'])
        self.push_sp(self.reg['ST0'])
        self.do_call(((self.reg['IVPD'] << 8) + n * 8) & 0xFFFFFF, self.pc)
        self.setbit('INTM', 1)
        return True

    # ------------------------------------------------------------ execution
    def run(self, max_steps):
        for _ in range(max_steps):
            self.step()

    def step(self):
        if self.rpt_left == 0 and not self.skip_pending():
            self.take_irq()
        ea = self.pc
        h = self.hle.get(ea)
        if h:
            h(self)
            return
        ins = self.code.get(ea)
        if ins is None:
            raise NotImplementedError(f'no decoded instruction at {ea:#x}')
        nxt = ea + ins['size']
        # parallel partner(s) follow immediately in IDA's listing
        group = [ins]
        while True:
            p = self.code.get(nxt)
            if p is None or not p['par']:
                break
            group.append(p)
            nxt += p['size']
        self.pc = nxt
        if self.trace is not None:
            self.trace.append(ea)
        self.post_t = []
        for g in group:
            self.exec1(g)
        for n, v in self.post_t:          # "Tn = Smem" copies land after the operation
            self.t[n] = v & M16
        self.cycles += 1
        # single repeat: re-run this instruction group
        if self.rpt_ea == ea and self.rpt_left > 0:
            self.rpt_left -= 1
            if self.rpt_left > 0 and self.pc == nxt:
                self.pc = ea
            else:
                self.rpt_ea = None
        # block repeat
        if self.blk and self.pc == nxt:
            st, end, lvl = self.blk[-1]
            if ea == end or (ea < end < nxt):
                cnt = 'BRC0' if lvl == 0 else 'BRC1'
                if self.reg[cnt] > 0:
                    self.reg[cnt] -= 1
                    self.pc = st
                else:
                    self.blk.pop()

    def skip_pending(self):
        return False

    def exec1(self, ins):
        base = ins['base']
        fn = getattr(self, 'op_' + base.replace('.', '_'), None)
        if fn is None:
            raise NotImplementedError(f'mnemonic {base!r}: {ins["text"]} @{ins["ea"]:#x}')
        fn(ins, self.ops(ins))

    # conditional execution: xcc/xccpart skip the next instruction group when false
    def op_xcc(self, ins, o):
        if not self.cond(o[0]):
            self.skip_next_group()

    op_xccpart = op_xcc

    def skip_next_group(self):
        ea = self.pc
        ins = self.code.get(ea)
        if ins is None:
            return
        nxt = ea + ins['size']
        while True:
            p = self.code.get(nxt)
            if p is None or not p['par']:
                break
            nxt += p['size']
        self.pc = nxt

    # -------------------------------------------------------- moves
    def op_mov(self, ins, o):
        if len(o) == 2:
            src, dst = o
            if dst[0] == 'ac':
                if src[0] == 'ac':
                    self.ac[dst[1]] = self.ac[src[1]]
                elif src[0] == 'dbl':
                    self.ac[dst[1]] = self.sx32(self.read(src))
                elif src[0] == 'imm':
                    self.ac[dst[1]] = src[1] & M40
                elif src[0] == 'shift':
                    self.acc_store(dst[1], self.read(src))
                elif src[0] == 'xar':
                    self.ac[dst[1]] = self.xar[src[1]]
                else:
                    self.ac[dst[1]] = self.read_ext(src) & M40
                return
            if src[0] == 'ac' and dst[0] in ('xar',) or dst[0] == 'named' and dst[1].startswith('X') and src[0] == 'ac':
                self.write(dst, self.ac[src[1]] & 0x7FFFFF)
                return
            if src[0] == 'ac':
                self.write(dst, self.ac[src[1]] & M16)
                return
            if src[0] == 'rnd' or src[0] == 'saturate':
                inner = src[1]
                while inner[0] in ('rnd', 'saturate'):
                    inner = inner[1]
                if inner[0] == 'hi':
                    a = s40(self.read(inner[1]) if inner[1][0] != 'ac' else self.ac[inner[1][1]])
                    if src[0] == 'rnd' or (src[1][0] == 'rnd'):
                        a += 0x8000
                    if 'saturate' in repr(src):
                        a = max(-(1 << 31), min((1 << 31) - 1, a))
                    self.write(dst, (a >> 16) & M16)
                    return
            v = self.read(src)
            if dst[0] in ('xar',) or (dst[0] == 'named' and dst[1] in ('XSP', 'XSSP', 'XCDP', 'XDP')):
                self.write(dst, v & 0x7FFFFF)
            else:
                self.write(dst, v)
            return
        if len(o) == 3 and o[2][0] == 'ac':      # mov a, b -> pair to ACx (hi/lo)
            raise NotImplementedError(ins['text'])
        raise NotImplementedError(ins['text'])

    def op_amar(self, ins, o):
        if len(o) == 1:
            self.mem_ea(o[0])                 # modify pointer only
            return
        src, dst = o
        if src[0] == 'mem':
            ea = self.mem_ea(src)
        else:
            ea = self.read(src)
        if dst[0] == 'xar':
            self.xar[dst[1]] = ea & 0x7FFFFF
        elif dst[0] == 'named':
            self.reg[dst[1]] = ea & 0x7FFFFF
        else:
            self.write(dst, ea)

    def op_amov(self, ins, o):
        self.op_amar(ins, o)

    def op_psh(self, ins, o):
        for x in o:
            if x[0] == 'dbl':
                v = self.read(x) & 0xFFFFFFFF
                self.push_sp(v & M16)
                self.push_sp(v >> 16)
            else:
                v = self.read(x)
                self.push_sp(v & M16)

    def op_pop(self, ins, o):
        for x in o:
            if x[0] == 'dbl':
                hi = self.pop_sp()
                lo = self.pop_sp()
                self.write(x, (hi << 16) | lo)
            else:
                self.write(x, self.pop_sp())

    def op_pshboth(self, ins, o):
        v = self.read(o[0])
        self.push_sp(v & M16)
        self.push_ssp(v >> 16 & M16)

    def op_popboth(self, ins, o):
        hi = self.pop_ssp()
        lo = self.pop_sp()
        self.write(o[0], ((hi & 0x7F) << 16) | lo)

    def op_aadd(self, ins, o):
        src, dst = o
        if dst[0] == 'named' and dst[1] in ('SP', 'XSP'):
            d = s16(src[1]) if src[0] == 'imm' else s16(self.read(src))
            self.reg['XSP'] = (self.reg['XSP'] & 0x7F0000) | ((self.reg['XSP'] + d) & M16)
            return
        d = src[1] if src[0] == 'imm' else s16(self.read(src))
        if dst[0] in ('ar', 'xar'):
            self.set_arlo(dst[1], self.get_arlo(dst[1]) + d)
        else:
            self.write(dst, self.read(dst) + d)

    def op_asub(self, ins, o):
        src, dst = o
        d = src[1] if src[0] == 'imm' else s16(self.read(src))
        if dst[0] == 'named' and dst[1] in ('SP', 'XSP'):
            self.reg['XSP'] = (self.reg['XSP'] & 0x7F0000) | ((self.reg['XSP'] - d) & M16)
        elif dst[0] in ('ar', 'xar'):
            self.set_arlo(dst[1], self.get_arlo(dst[1]) - d)
        else:
            self.write(dst, self.read(dst) - d)

    def op_nop(self, ins, o):
        pass

    op_nop_16 = op_nop
    op_mar = op_amar

    def op_idle(self, ins, o):
        # ICR (I/O 0x0001) selects the domains IDLE gates; bit 0 = CPU domain
        icr = self.io_last.get(0x0001, 0)
        if icr & 1:
            self.idle = True            # wait for an enabled interrupt
            self.pc = ins['ea']
            if not self.pending_irq():
                raise Halt('cpu idle, no interrupt pending')

    # -------------------------------------------------------- arithmetic
    def _arith(self, ins, o, f, setc=None):
        if len(o) == 2:
            src, dst = o
            a = dst
        else:
            src, a, dst = o[0], o[1], o[2]
            if dst[0] != 'ac' and a[0] == 'ac':
                pass
        if dst[0] == 'ac':
            x = self.read_ext(a if len(o) == 3 else dst)
            y = self.read_ext(src)
            r = f(x, y)
            if setc is not None:
                self.setbit('CARRY', setc(x, y, r))
            self.acc_store(dst[1], r)
        else:
            x = s16(self.read(a if len(o) == 3 else dst)) if (a if len(o) == 3 else dst)[0] != 'ac' else s40(self.ac[(a if len(o) == 3 else dst)[1]])
            y = src[1] if src[0] == 'imm' else (s40(self.ac[src[1]]) if src[0] == 'ac' else s16(self.read(src)))
            self.write(dst, f(x, y))

    def op_add(self, ins, o):
        self._arith(ins, o, lambda x, y: x + y,
                    lambda x, y, r: 1 if ((x & 0xFFFFFFFF) + (y & 0xFFFFFFFF)) >> 32 else 0)

    def op_sub(self, ins, o):
        # sub src, dst: dst = dst - src ; sub a, b, dst: dst = b - a
        if len(o) == 3:
            src, a, dst = o
            if dst[0] == 'ac':
                x = self.read_ext(a)
                y = self.read_ext(src)
                self.setbit('CARRY', 1 if (x & 0xFFFFFFFF) >= (y & 0xFFFFFFFF) else 0)
                self.acc_store(dst[1], x - y)
            else:
                x = s16(self.read(a)) if a[0] != 'ac' else s40(self.ac[a[1]])
                y = src[1] if src[0] == 'imm' else (s40(self.ac[src[1]]) if src[0] == 'ac' else s16(self.read(src)))
                self.write(dst, x - y)
            return
        self._arith(ins, o, lambda x, y: x - y,
                    lambda x, y, r: 1 if (x & 0xFFFFFFFF) >= (y & 0xFFFFFFFF) else 0)

    def _logic(self, ins, o, f):
        if len(o) == 2:
            src, dst = o
            a = dst
        else:
            src, a, dst = o
        if dst[0] == 'ac':
            x = self.read(a) if a[0] != 'ac' else self.ac[a[1]]
            y = self.read(src) & (M40 if src[0] in ('ac', 'shift', 'imm') else M16)
            self.ac[dst[1]] = f(x & M40, y & M40) & M40
        else:
            self.write(dst, f(self.read(a) & M16, self.read(src) & M16) & M16)

    def op_and(self, ins, o):
        self._logic(ins, o, lambda x, y: x & y)

    def op_or(self, ins, o):
        self._logic(ins, o, lambda x, y: x | y)

    def op_xor(self, ins, o):
        self._logic(ins, o, lambda x, y: x ^ y)

    def op_not(self, ins, o):
        src, dst = (o[0], o[1]) if len(o) == 2 else (o[0], o[0])
        if dst[0] == 'ac':
            self.ac[dst[1]] = ~self.read(src) & M40
        else:
            self.write(dst, ~self.read(src) & M16)

    def op_neg(self, ins, o):
        src, dst = (o[0], o[1]) if len(o) == 2 else (o[0], o[0])
        if dst[0] == 'ac':
            self.acc_store(dst[1], -self.read_ext(src))
        else:
            self.write(dst, -s16(self.read(src)))

    def op_abs(self, ins, o):
        src, dst = (o[0], o[1]) if len(o) == 2 else (o[0], o[0])
        if dst[0] == 'ac':
            self.acc_store(dst[1], abs(self.read_ext(src)))
        else:
            self.write(dst, abs(s16(self.read(src))))

    def op_max(self, ins, o):
        src, dst = o
        if dst[0] == 'ac':
            self.ac[dst[1]] = max(s40(self.ac[dst[1]]), self.read_ext(src)) & M40
        else:
            self.write(dst, max(s16(self.read(dst)), s16(self.read(src))))

    def op_min(self, ins, o):
        src, dst = o
        if dst[0] == 'ac':
            self.ac[dst[1]] = min(s40(self.ac[dst[1]]), self.read_ext(src)) & M40
        else:
            self.write(dst, min(s16(self.read(dst)), s16(self.read(src))))

    def _cmp(self, ins, o, unsigned=False):
        c, tc = o[0], o[1]
        a, op, b = c[1], c[2], c[3]
        if unsigned:
            x = self.read(a) & (M40 if a[0] == 'ac' else M16)
            y = (b[1] & M16) if b[0] == 'imm' else self.read(b) & (M40 if b[0] == 'ac' else M16)
            r = {'==': x == y, '!=': x != y, '<': x < y, '<=': x <= y, '>': x > y,
                 '>=': x >= y}[op]
        else:
            r = self.cond(c)
        self.write(tc, 1 if r else 0)

    def op_cmp(self, ins, o):
        self._cmp(ins, o)

    def op_cmpu(self, ins, o):
        self._cmp(ins, o, True)

    def op_btst(self, ins, o):
        b, src, tc = o
        n = b[1] if b[0] == 'imm' else self.read(b) & 0xF
        self.write(tc, (self.read(src) >> n) & 1)

    def _bitop(self, ins, o, v):
        if len(o) == 1:
            self.write(o[0], v)          # status bit
            return
        b, dst = o
        n = b[1] if b[0] == 'imm' else self.read(b) & 0x1F
        if dst[0] == 'ac':
            a = self.ac[dst[1]]
            self.ac[dst[1]] = (a | (1 << n)) if v else (a & ~(1 << n))
        else:
            # address read-modify-write without double pointer modification
            if dst[0] == 'mem':
                ea = self.mem_ea(dst)
                x = self.rd(ea)
                self.wr(ea, (x | (1 << n)) if v else (x & ~(1 << n)))
            else:
                x = self.read(dst)
                self.write(dst, (x | (1 << n)) if v else (x & ~(1 << n)))

    def op_bset(self, ins, o):
        self._bitop(ins, o, 1)

    def op_bclr(self, ins, o):
        self._bitop(ins, o, 0)

    def op_bnot(self, ins, o):
        b, dst = o
        n = b[1]
        self.write(dst, self.read(dst) ^ (1 << n))

    def op_bfxtr(self, ins, o):
        mask, src, dst = o
        m, v, r, j = mask[1], self.read(src), 0, 0
        for i in range(16):
            if m >> i & 1:
                r |= (v >> i & 1) << j
                j += 1
        self.write(dst, r)

    def op_bfxpa(self, ins, o):
        mask, src, dst = o
        m, v, r, j = mask[1], self.read(src), 0, 0
        for i in range(16):
            if m >> i & 1:
                r |= (v >> j & 1) << i
                j += 1
        self.write(dst, r)

    # -------------------------------------------------------- shifts
    def op_sfts(self, ins, o):
        # sfts src, #k, dst | sfts dst, #k | sfts dst, Tn
        if len(o) == 2:
            dst, sh = o
            src = dst
        else:
            src, sh, dst = o
        s = self.shamt(sh)
        if dst[0] == 'ac':
            v = self.read_ext(src)
            r = v << s if s >= 0 else v >> -s
            self.acc_store(dst[1], r)
        else:
            v = s16(self.read(src))
            r = v << s if s >= 0 else v >> -s
            self.write(dst, r)

    def op_sftl(self, ins, o):
        if len(o) == 2:
            dst, sh = o
            src = dst
        else:
            src, sh, dst = o
        s = self.shamt(sh)
        if dst[0] == 'ac':
            w = 40 if self.bit('M40') else 32
            v = self.read(src) & ((1 << w) - 1) if src[0] == 'ac' else self.read(src) & M16
            r = (v << s) if s >= 0 else (v >> -s)
            a = self.ac[dst[1]]
            self.ac[dst[1]] = ((a & ~((1 << w) - 1)) | (r & ((1 << w) - 1))) & M40 if w == 32 else r & M40
        else:
            v = self.read(src) & M16
            self.write(dst, (v << s) if s >= 0 else (v >> -s))

    def op_sftcc(self, ins, o):
        raise NotImplementedError(ins['text'])

    def op_exp(self, ins, o):
        src, dst = o
        a = s40(self.read(src))
        if a == 0:
            n = 0
        else:
            n = 0
            while n < 39 and ((a >> (38 - n)) & 1) == ((a >> 39) & 1):
                n += 1
            n -= 8
        self.write(dst, n)

    def op_mant(self, ins, o):
        # "mant ACx, ACy :: NEXP ACx, Tx" is split by IDA as mant + || exp
        src, dst = o
        a = s40(self.read(src))
        if a == 0:
            self.ac[dst[1]] = 0
            return
        n = 0
        while n < 39 and ((a >> (38 - n)) & 1) == ((a >> 39) & 1):
            n += 1
        self.ac[dst[1]] = (a << (n - 8) if n >= 8 else a >> (8 - n)) & M40

    def op_round(self, ins, o):
        src, dst = (o[0], o[1]) if len(o) == 2 else (o[0], o[0])
        self.acc_store(dst[1], (self.read_ext(src) + 0x8000) & ~0xFFFF)

    def op_sat(self, ins, o):
        src, dst = (o[0], o[1]) if len(o) == 2 else (o[0], o[0])
        v = max(-(1 << 31), min((1 << 31) - 1, self.read_ext(src)))
        self.ac[dst[1]] = v & M40

    def op_subc(self, ins, o):
        src, a, dst = o if len(o) == 3 else (o[0], o[1], o[1])
        x = self.read_ext(a)
        y = self.read_ext(src) << 15
        d = x - y
        self.ac[dst[1]] = ((d << 1) + 1 if d >= 0 else x << 1) & M40

    # -------------------------------------------------------- multiply
    def _prod(self, x, y, uns_x=False, uns_y=False):
        x = x & M16 if uns_x else s16(x)
        y = y & M16 if uns_y else s16(y)
        p = x * y
        if self.bit('FRCT'):
            p <<= 1
            if self.bit('SMUL') and p == 1 << 31:
                p = (1 << 31) - 1
        return p

    def _mopnd(self, o):
        """Multiplier operand: 16-bit signed; ACx contributes bits 32..16; uns() unsigned."""
        if o[0] == 'ac':
            return (self.ac[o[1]] >> 16) & M16, False
        if o[0] == 'uns':
            return self.read(o[1]) & M16, True
        if o[0] == 'imm':
            return o[1] & M16, False
        if o[0] == 'tload':
            return self.read(o) & M16, False
        return self.read(o) & M16, False

    def op_mpy(self, ins, o):
        # mpy src, dst (dst*=src) | mpy a, b, dst
        if len(o) == 2:
            src, dst = o
            a = dst
        else:
            src, a, dst = o
        (x, ux), (y, uy) = self._mopnd(src), self._mopnd(a)
        self.acc_store(dst[1], self._prod(x, y, ux, uy))

    op_mpym = op_mpy
    op_mpyk = op_mpy
    op_mpymu = op_mpy
    op_sqrm = None

    def op_mpyr(self, ins, o):
        self.op_mpy(ins, o)
        d = self._acc_dst(o)
        self.ac[d] = (self.ac[d] + 0x8000) & ~0xFFFF & M40

    op_mpymr = op_mpyr

    def _acc_dst(self, o):
        core = o[:-2] if len(o) == 5 else o
        return [x for x in core if x[0] == 'ac'][-1][1]

    def _par_store(self, o):
        """Implied ':: MOV HI(ACx << T2), Smem' folded into MAC/MAS by IDA (old ACx)."""
        src, dst = o[-2], o[-1]
        v = self.read(src) if src[0] != 'hi' else (self.read(src[1]) >> 16) & M16
        self.write(dst, v & M16)
        return o[:-2]

    def op_mac(self, ins, o):
        # mac a, b, acc_in, acc_out | mac src, dst(acc, uses hi as multiplicand) ...
        if len(o) == 5:
            o = self._par_store(o)
        if len(o) == 4:
            a, b, acc, dst = o
            (x, ux), (y, uy) = self._mopnd(a), self._mopnd(b)
            self.acc_store(dst[1], s40(self.ac[acc[1]]) + self._prod(x, y, ux, uy))
        elif len(o) == 3:
            a, b, dst = o
            (x, ux), (y, uy) = self._mopnd(a), self._mopnd(b)
            self.acc_store(dst[1], s40(self.ac[dst[1]]) + self._prod(x, y, ux, uy))
        else:
            raise NotImplementedError(ins['text'])

    op_macm = op_mac
    op_mack = op_mac

    def op_macr(self, ins, o):
        self.op_macmr(ins, o)

    def op_macmr(self, ins, o):
        self.op_mac(ins, o)
        d = self._acc_dst(o)
        self.ac[d] = (self.ac[d] + 0x8000) & ~0xFFFF & M40

    def op_mas(self, ins, o):
        if len(o) == 5:
            o = self._par_store(o)
        if len(o) == 4:
            a, b, acc, dst = o
            (x, ux), (y, uy) = self._mopnd(a), self._mopnd(b)
            self.acc_store(dst[1], s40(self.ac[acc[1]]) - self._prod(x, y, ux, uy))
        elif len(o) == 3:
            a, b, dst = o
            (x, ux), (y, uy) = self._mopnd(a), self._mopnd(b)
            self.acc_store(dst[1], s40(self.ac[dst[1]]) - self._prod(x, y, ux, uy))
        else:
            raise NotImplementedError(ins['text'])

    op_masm = op_mas

    def op_masmr(self, ins, o):
        self.op_mas(ins, o)
        d = self._acc_dst(o)
        self.ac[d] = (self.ac[d] + 0x8000) & ~0xFFFF & M40

    op_masr = op_masmr


    def op_sqrm(self, ins, o):
        src, dst = o
        x, u = self._mopnd(src)
        self.acc_store(dst[1], self._prod(x, x, u, u))

    # -------------------------------------------------------- program flow
    def _target(self, o):
        if o[0] in ('code', 'imm'):
            return o[1]
        if o[0] == 'ac':
            return self.ac[o[1]] & 0xFFFFFF
        raise NotImplementedError(f'target {o}')

    def op_b(self, ins, o):
        self.pc = self._target(o[0])

    def op_bcc(self, ins, o):
        if self.cond(o[1]):
            self.pc = self._target(o[0])

    def op_call(self, ins, o):
        self.do_call(self._target(o[0]), self.pc)

    def op_callcc(self, ins, o):
        if self.cond(o[1]):
            self.do_call(self._target(o[0]), self.pc)

    def op_ret(self, ins, o):
        self.do_ret()

    def op_retcc(self, ins, o):
        if self.cond(o[0]):
            self.do_ret()

    def op_reti(self, ins, o):
        self.do_ret()
        self.reg['ST0'] = self.pop_sp()
        self.reg['ST1'] = self.pop_sp()

    def op_intr(self, ins, o):
        n = o[0][1]
        self.push_sp(self.reg['ST1'])
        self.push_sp(self.reg['ST0'])
        self.do_call(((self.reg['IVPD'] << 8) + n * 8) & 0xFFFFFF, self.pc)
        self.setbit('INTM', 1)

    def op_rpt(self, ins, o):
        n = self.read(o[0]) if o[0][0] != 'imm' else o[0][1]
        self.rpt_left = (n & M16) + 1
        self.rpt_ea = self.pc

    def op_rptadd(self, ins, o):
        raise NotImplementedError(ins['text'])

    def op_rptb(self, ins, o):
        end = self._target(o[0])
        lvl = len(self.blk)
        if lvl > 1:
            raise NotImplementedError('block repeat nesting > 2')
        if lvl == 1:
            self.reg['BRC1'] = self.reg['BRS1']
        self.blk.append([self.pc, end, lvl])

    op_rptblocal = op_rptb

    def op_rptcc(self, ins, o):
        raise NotImplementedError(ins['text'])


# ---------------------------------------------------------------- peripherals
class RegFile:
    """Plain read/write register block (default for unmodelled peripherals)."""
    def __init__(self, init=None):
        self.r = dict(init or {})

    def rd(self, a):
        return self.r.get(a, 0)

    def wr(self, a, v):
        self.r[a] = v


class SarAdc(RegFile):
    """C5535-class SAR ADC at I/O 0x7012..0x701A: SARCTRL bit15 starts a conversion on
    channel bits 14:12; SARDATA bit15 = busy (set for a few reads), 9:0 = result."""
    def __init__(self, values=None):
        super().__init__()
        self.values = values or {}
        self.busy = 0
        self.ch = 0

    def wr(self, a, v):
        self.r[a] = v
        if a == 0x7012 and v & 0x8000:
            self.ch = v >> 12 & 7
            self.busy = 3

    def rd(self, a):
        if a == 0x7014:
            if self.busy:
                self.busy -= 1
                return 0x8000 | self.ch << 12
            return self.ch << 12 | (self.values.get(self.ch, 0x200) & 0x3FF)
        return self.r.get(a, 0)


class SysRegs(RegFile):
    """System control block at I/O 0x1C00: PRCR (0x1C05) reset bits self-clear."""
    def rd(self, a):
        if a == 0x1C05:
            return 0
        return self.r.get(a, 0)


class Blk35(RegFile):
    """I/O 0x3510..0x353C (unidentified; DSP-A and DSP-B init it the same way): after
    0x3510 is written the firmware waits for 0x3514 bit 0 -> report ready at once."""
    def rd(self, a):
        if a == 0x3514:
            return self.r.get(a, 0) | 1
        return self.r.get(a, 0)


def default_board(sim):
    """Map the DV DSP's I/O blocks: SAR ADC, generic register files elsewhere."""
    sim.ports[0x7000] = SarAdc()
    sim.ports[0x1C00] = SysRegs()
    sim.ports[0x3500] = Blk35()
    for base in (0x0000, 0x0C00, 0x0D00, 0x0E00, 0x0F00, 0x1800, 0x1900, 0x1C00, 0x2800,
                 0x2900, 0x2A00, 0x2B00, 0x3000, 0x3500):
        sim.ports.setdefault(base, RegFile())


def load_dspb(image_path, insns_path):
    img = open(image_path, 'rb').read()
    insns = json.load(open(insns_path))
    return img, insns


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', default='/home/nemanjan00/icom/out/705E140_full/dspB.mem')
    ap.add_argument('--insns', default='/home/nemanjan00/icom/out/705E140_full/dspB_insns.json')
    ap.add_argument('--entry', type=lambda s: int(s, 0), default=0xA9AC)
    ap.add_argument('--steps', type=int, default=200000)
    a = ap.parse_args()
    img, ins = load_dspb(a.image, a.insns)
    sim = C55Sim(img, ins, a.entry)
    default_board(sim)
    sim.port_log = []
    try:
        sim.run(a.steps)
        print('ran', a.steps, 'steps; pc', hex(sim.pc))
    except (NotImplementedError, Halt) as e:
        print('stopped at', hex(sim.pc), 'after', sim.cycles, 'steps:', e)
    seen = {}
    for d, p, v, pc in sim.port_log:
        seen.setdefault((d, p), (v, pc))
    for (d, p), (v, pc) in sorted(seen.items(), key=lambda x: x[0][1]):
        print(f'  {d} port {p:#06x} = {v:#06x} (first at {pc:#x})')
