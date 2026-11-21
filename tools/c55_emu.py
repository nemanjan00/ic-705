#!/usr/bin/env python3
"""Tiny TMS320C55x interpreter for a handful of DV-DSP routines.

It executes an instruction stream exported from IDA (address, size, bytes, mnemonic
text; see tools/ida_export_c55.py) against a word-addressed memory image, and
high-level-emulates TI DSPLIB `fir` (called by the GMSK modulator).  Only the
instruction forms used by the routines we run are implemented; anything else raises
NotImplementedError so a silent mis-emulation can't happen.

Memory: C55x data space is 16-bit words; IDA shows data symbols at byte address =
2 * word address.  Program addresses are byte addresses.

Vendor code/data never enters this file: you supply the decoded image (`dspB.mem`)
and the exported instruction JSON.
"""
import json
import re
import struct

M16 = 0xFFFF


def s16(v):
    v &= M16
    return v - 0x10000 if v & 0x8000 else v


class C55:
    def __init__(self, mem_bytes, insns, hle=None):
        n = max(len(mem_bytes) // 2, 0x20000)
        self.mem = [0] * n
        for i in range(len(mem_bytes) // 2):
            self.mem[i] = struct.unpack_from('>H', mem_bytes, 2 * i)[0]
        self.code = {i['ea']: i for i in insns}
        self.r = {k: 0 for k in ('XAR0 XAR1 XAR2 XAR3 XAR4 XAR5 XAR6 XAR7 T0 T1 T2 T3 '
                                  'XSP XSSP TC1 TC2').split()}
        self.hle = hle or {}
        self.rpt = 0
        self.skip_next = False
        self.trace = []

    # ---- helpers -------------------------------------------------------
    def rd(self, w):
        return self.mem[w & 0x7FFFFF]

    def wr(self, w, v):
        self.mem[w & 0x7FFFFF] = v & M16

    def ar(self, n):
        return self.r[f'XAR{n}'] & 0x7FFFFF

    def set_ar(self, n, v, x=False):
        if x:
            self.r[f'XAR{n}'] = v & 0x7FFFFF
        else:   # writing ARn keeps the ARnH page bits
            self.r[f'XAR{n}'] = (self.r[f'XAR{n}'] & 0x7F0000) | (v & M16)

    def reg(self, name):
        if re.fullmatch(r'AR\d', name):
            return self.r[f'XAR{name[2]}'] & M16
        if re.fullmatch(r'T\d', name):
            return self.r[name] & M16
        raise NotImplementedError(name)

    def set_reg(self, name, v):
        if re.fullmatch(r'AR\d', name):
            self.set_ar(int(name[2]), v)
        elif re.fullmatch(r'T\d', name):
            self.r[name] = v & M16
        else:
            raise NotImplementedError(name)

    @staticmethod
    def num(s):
        s = s.lstrip('#')
        neg = s.startswith('-')
        s = s.lstrip('-')
        v = int(s[:-1], 16) if s.endswith('h') else int(s, 0)
        return -v if neg else v

    def sym(self, s):
        """'#word_1FCD2' / '#gmsk_pulse' (resolved by caller map) -> word address."""
        s = s.lstrip('#')
        m = re.fullmatch(r'(?:word|byte|dword|unk)_([0-9A-F]+)', s)
        if m:
            return int(m.group(1), 16) // 2
        if s in self.symbols:
            return self.symbols[s] // 2
        return self.num(s) // 2

    symbols = {}

    def smem(self, op, write=None):
        """Evaluate a Smem operand; returns value (read) or performs write."""
        op = op.strip()
        m = re.fullmatch(r'\*SP\(#([^)]+)\)', op)
        if m:
            k = self._spoff(m.group(1))
            a = (self.r['XSP'] + k) & 0x7FFFFF
        elif re.fullmatch(r'\*AR(\d)\+', op):
            n = int(op[3]); a = self.ar(n); self.set_ar(n, a + 1)
        elif re.fullmatch(r'\*AR(\d)', op):
            a = self.ar(int(op[3]))
        elif re.fullmatch(r'\*AR(\d)\(T0\)', op):
            a = (self.ar(int(op[3])) & 0x7F0000) | ((self.ar(int(op[3])) + self.reg('T0')) & M16)
        elif re.fullmatch(r'\*\(#[\w]+\)', op):
            a = self.sym(op[2:-1])
        else:
            raise NotImplementedError(f'Smem {op}')
        if write is None:
            return self.rd(a)
        self.wr(a, write)

    def _spoff(self, off):
        # IDA stack-var notation "#16h+var_4" is in BYTES (0x16-4 = 0x12 bytes = 9
        # words); a bare "#9h" is already in words.
        if 'var_' not in off and 'arg_' not in off:
            return self.num(off)
        tot = 0
        for part in off.split('+'):
            part = part.strip()
            m = re.fullmatch(r'(?:var|arg)_([0-9A-F]+)', part)
            if m:
                v = int(m.group(1), 16)
                tot += -v if part.startswith('var') else v
            else:
                tot += self.num(part)
        return tot // 2

    # ---- execution ------------------------------------------------------
    def call(self, fn, max_steps=100000):
        ret_marker = 0xFFFFFE
        stack = [ret_marker]
        pc = fn
        for _ in range(max_steps):
            if pc == ret_marker:
                return
            if pc in self.hle:
                self.hle[pc](self)
                pc = stack.pop()
                continue
            ins = self.code.get(pc)
            if ins is None:
                raise NotImplementedError(f'no instruction exported at {pc:#x}')
            npc = pc + ins['size']
            t = ins['text']
            self.trace.append((pc, t))
            res = self.step(t, npc)
            if res == 'ret':
                pc = stack.pop()
            elif isinstance(res, tuple) and res[0] == 'call':
                stack.append(npc); pc = res[1]
            elif isinstance(res, int):
                pc = res
            else:
                pc = npc
        raise RuntimeError('step limit')

    def step(self, t, npc):
        par = t.startswith('||')
        t = t.lstrip('| ').strip()
        mn, _, ops = t.partition(' ')
        ops = [o.strip() for o in re.split(r',(?![^(]*\))', ops)] if ops else []
        if self.skip_next and not par:
            self.skip_next = False
            return None
        if self.rpt and not par:
            n, self.rpt = self.rpt, 0
            for _ in range(n + 1):
                self.step(t, npc)
            return None
        if mn == 'aadd' and ops[1] == 'SP':
            self.r['XSP'] = (self.r['XSP'] + self.num(ops[0])) & 0x7FFFFF
        elif mn == 'amar':
            dst = ops[1]
            n = int(dst[-1])
            src = ops[0]
            if re.fullmatch(r'\*AR\d', src):
                v = self.ar(int(src[3]))
            elif src.startswith('*SP('):
                v = (self.r['XSP'] + self._spoff(src[5:-1])) & 0x7FFFFF
            elif src.startswith('*(#'):
                v = self.sym(src[2:-1])
            else:
                raise NotImplementedError(t)
            self.set_ar(n, v, x=True)
        elif mn == 'rpt':
            self.rpt = self.num(ops[0])
        elif mn == 'mov':
            a, b = ops
            if a.startswith('#') and re.fullmatch(r'(AR|T)\d', b):
                self.set_reg(b, self.num(a))
            elif a.startswith('#'):
                self.smem(b, write=self.num(a))
            elif re.fullmatch(r'(AR|T)\d', a) and re.fullmatch(r'(AR|T)\d', b):
                self.set_reg(b, self.reg(a))
            elif re.fullmatch(r'(AR|T)\d', a):
                self.smem(b, write=self.reg(a))
            elif re.fullmatch(r'(AR|T)\d', b):
                self.set_reg(b, self.smem(a))
            else:
                raise NotImplementedError(t)
        elif mn == 'neg' and len(ops) == 2:
            self.set_reg(ops[1], -s16(self.reg(ops[0])))
        elif mn == 'neg':
            self.set_reg(ops[0], -s16(self.reg(ops[0])))
        elif mn == 'bcc':
            m = re.fullmatch(r'(T0|AR\d) == #(\w+)', ops[1])
            if not m:
                raise NotImplementedError(t)
            if self.reg(m.group(1)) == self.num(m.group(2)):
                return int(re.search(r'([0-9A-F]+)$', ops[0]).group(1), 16)
        elif mn == 'b':
            return int(re.search(r'([0-9A-F]+)$', ops[0]).group(1), 16)
        elif mn == 'cmpu':
            m = re.fullmatch(r'(\w+) == (\w+)', ops[0])
            self.r[ops[1]] = int(self.reg(m.group(1)) == self.reg(m.group(2)))
        elif mn == 'xccpart':
            # Modelled as "execute the NEXT instruction only if cond". The firmware
            # also uses a second encoding (0x9E..) whose placement differs; with the
            # invert flag at its init value both readings give identical results
            # for gmsk_mod_bit, and the 4FSK patch removes these instructions.
            c = ops[0]
            if c in ('TC1', 'TC2'):
                ok = bool(self.r[c])
            else:
                m = re.fullmatch(r'(T\d|AR\d) == #(\w+)', c)
                if not m:
                    raise NotImplementedError(t)
                ok = self.reg(m.group(1)) == self.num(m.group(2))
            self.skip_next = not ok
        elif mn == 'nop':
            pass
        elif mn == 'call':
            tgt = re.search(r'([0-9A-F]{4,6})$', ops[0])
            tgt = int(tgt.group(1), 16) if tgt else self.symbols[ops[0]]
            return ('call', tgt)
        elif mn == 'ret':
            return 'ret'
        else:
            raise NotImplementedError(t)
        return None


def hle_dsplib_fir(state):
    """TI DSPLIB fir(x=XAR0, h=XAR1, r=XAR2, dbuffer=XAR3, nx=T0, nh=T1).

    r[j] = round(sum_k h[k] * x[j-k]) in Q15 (FRCT on, round-to-nearest), with the
    filter history kept per dbuffer.  History is modelled in Python, keyed by the
    dbuffer address, rather than reproducing DSPLIB's circular-buffer bookkeeping.
    """
    def hle(cpu):
        x, h, r, d = cpu.ar(0), cpu.ar(1), cpu.ar(2), cpu.ar(3)
        nx, nh = cpu.reg('T0'), cpu.reg('T1')
        hist = state.setdefault(d, [0] * nh)
        taps = [s16(cpu.rd(h + k)) for k in range(nh)]
        for j in range(nx):
            hist = [s16(cpu.rd(x + j))] + hist[:-1]
            acc = sum(a * b for a, b in zip(taps, hist)) << 1
            y = (acc + 0x8000) >> 16
            y = max(-0x8000, min(0x7FFF, y))
            cpu.wr(r + j, y)
        state[d] = hist
        cpu.r['T0'] = 0
    return hle


def load(mem_path, insn_json, **kw):
    return C55(open(mem_path, 'rb').read(), json.load(open(insn_json)), **kw)
