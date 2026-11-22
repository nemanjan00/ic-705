#!/usr/bin/env python3
"""ADSP-214xx SHARC 48-bit instruction decoder / disassembler (own code).

Field layouts follow the opcode tables of the SHARC Processor Programming Reference
(ADSP-2136x/2137x/214xx, rev 2.4, chapters 10 and 12; not included in this repo).
decode(word) returns a dict describing the instruction; text(word) renders assembly-like
text. Only the 48-bit ISA forms are handled (the IC-705 boot stream loads 48-bit code).

  sharc_dis.py dsp.image.bin --kernel     # disassemble the boot kernel
"""
import argparse

COND = ['EQ', 'LT', 'LE', 'AC', 'AV', 'MV', 'MS', 'SV', 'SZ', 'FLAG0_IN', 'FLAG1_IN',
        'FLAG2_IN', 'FLAG3_IN', 'TF', 'BM', 'NOT LCE',
        'NE', 'GE', 'GT', 'NOT AC', 'NOT AV', 'NOT MV', 'NOT MS', 'NOT SV', 'NOT SZ',
        'NOT FLAG0_IN', 'NOT FLAG1_IN', 'NOT FLAG2_IN', 'NOT FLAG3_IN', 'NOT TF', 'NOT BM',
        'TRUE']
TERM = COND[:15] + ['LCE'] + COND[16:31] + ['FOREVER']

UREG_G6 = ['FADDR', 'DADDR', '?6.2', 'PC', 'PCSTK', 'PCSTKP', 'LADDR', 'CURLCNTR', 'LCNTR',
           'EMUCLK', 'EMUCLK2', 'PX', 'PX1', 'PX2', 'TPERIOD', 'TCOUNT']
UREG_G7 = ['USTAT1', 'USTAT2', 'MODE1', 'MMASK', 'MODE2', 'FLAGS', 'ASTATX', 'ASTATY',
           'STKYX', 'STKYY', 'IRPTL', 'IMASK', 'IMASKP', 'LIRPTL', 'USTAT3', 'USTAT4']
SREG = UREG_G7


def ureg(c):
    g, n = c >> 4 & 7, c & 15
    return [f'R{n}', f'I{n}', f'M{n}', f'L{n}', f'B{n}', f'S{n}', UREG_G6[n], UREG_G7[n]][g]


def bits(w, hi, lo):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


def sx(v, n):
    return v - (1 << n) if v >> (n - 1) & 1 else v


# ------------------------------------------------------------------ compute field
ALU_FIX = {0x01: 'R{n} = R{x} + R{y}', 0x02: 'R{n} = R{x} - R{y}', 0x05: 'R{n} = R{x} + R{y} + CI',
           0x06: 'R{n} = R{x} - R{y} + CI - 1', 0x09: 'R{n} = (R{x} + R{y})/2',
           0x0A: 'COMP(R{x}, R{y})', 0x0B: 'COMPU(R{x}, R{y})', 0x25: 'R{n} = R{x} + CI',
           0x26: 'R{n} = R{x} + CI - 1', 0x29: 'R{n} = R{x} + 1', 0x2A: 'R{n} = R{x} - 1',
           0x22: 'R{n} = -R{x}', 0x30: 'R{n} = ABS R{x}', 0x21: 'R{n} = PASS R{x}',
           0x40: 'R{n} = R{x} AND R{y}', 0x41: 'R{n} = R{x} OR R{y}', 0x42: 'R{n} = R{x} XOR R{y}',
           0x43: 'R{n} = NOT R{x}', 0x61: 'R{n} = MIN(R{x}, R{y})', 0x62: 'R{n} = MAX(R{x}, R{y})',
           0x63: 'R{n} = CLIP R{x} BY R{y}',
           0x81: 'F{n} = F{x} + F{y}', 0x82: 'F{n} = F{x} - F{y}', 0x91: 'F{n} = ABS (F{x} + F{y})',
           0x92: 'F{n} = ABS (F{x} - F{y})', 0x89: 'F{n} = (F{x} + F{y})/2', 0x8A: 'COMP(F{x}, F{y})',
           0xA2: 'F{n} = -F{x}', 0xB0: 'F{n} = ABS F{x}', 0xA1: 'F{n} = PASS F{x}',
           0xA5: 'F{n} = RND F{x}', 0xBD: 'F{n} = SCALB F{x} BY R{y}', 0xAD: 'R{n} = MANT F{x}',
           0xC1: 'R{n} = LOGB F{x}', 0xD9: 'R{n} = FIX F{x} BY R{y}', 0xC9: 'R{n} = FIX F{x}',
           0xDD: 'R{n} = TRUNC F{x} BY R{y}', 0xCD: 'R{n} = TRUNC F{x}',
           0xDA: 'F{n} = FLOAT R{x} BY R{y}', 0xCA: 'F{n} = FLOAT R{x}', 0xC4: 'F{n} = RECIPS F{x}',
           0xC5: 'F{n} = RSQRTS F{x}', 0xE0: 'F{n} = F{x} COPYSIGN F{y}',
           0xE1: 'F{n} = MIN(F{x}, F{y})', 0xE2: 'F{n} = MAX(F{x}, F{y})',
           0xE3: 'F{n} = CLIP F{x} BY F{y}'}

SHF = {0x00: 'R{n} = LSHIFT R{x} BY {y}', 0x20: 'R{n} = R{n} OR LSHIFT R{x} BY {y}',
       0x04: 'R{n} = ASHIFT R{x} BY {y}', 0x24: 'R{n} = R{n} OR ASHIFT R{x} BY {y}',
       0x08: 'R{n} = ROT R{x} BY {y}', 0xC4: 'R{n} = BCLR R{x} BY {y}',
       0xC0: 'R{n} = BSET R{x} BY {y}', 0xC8: 'R{n} = BTGL R{x} BY {y}',
       0xCC: 'BTST R{x} BY {y}', 0x44: 'R{n} = FDEP R{x} BY {y}',
       0x4C: 'R{n} = FDEP R{x} BY {y} (SE)', 0x64: 'R{n} = R{n} OR FDEP R{x} BY {y}',
       0x6C: 'R{n} = R{n} OR FDEP R{x} BY {y} (SE)', 0x40: 'R{n} = FEXT R{x} BY {y}',
       0x48: 'R{n} = FEXT R{x} BY {y} (SE)', 0x80: 'R{n} = EXP R{x}',
       0x84: 'R{n} = EXP R{x} (EX)', 0x88: 'R{n} = LEFTZ R{x}', 0x8C: 'R{n} = LEFTO R{x}',
       0x90: 'R{n} = FPACK F{x}', 0x94: 'F{n} = FUNPACK R{x}', 0x74: 'BITDEP R{x} BY {y}',
       0x50: 'R{n} = BITEXT R{x}', 0x58: 'R{n} = BITEXT R{x} (NU)', 0x7C: 'BFFWRP = R{n}',
       0x70: 'R{n} = BFFWRP'}

MF_OPS = {0x04: ('R{m} = R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} + R{ya}'),
          0x05: ('R{m} = R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} - R{ya}'),
          0x06: ('R{m} = R{xm} * R{ym} (SSFR)', 'R{a} = (R{xa} + R{ya})/2'),
          0x08: ('MRF = MRF + R{xm} * R{ym} (SSF)', 'R{a} = R{xa} + R{ya}'),
          0x09: ('MRF = MRF + R{xm} * R{ym} (SSF)', 'R{a} = R{xa} - R{ya}'),
          0x0A: ('MRF = MRF + R{xm} * R{ym} (SSF)', 'R{a} = (R{xa} + R{ya})/2'),
          0x0C: ('R{m} = MRF + R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} + R{ya}'),
          0x0D: ('R{m} = MRF + R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} - R{ya}'),
          0x0E: ('R{m} = MRF + R{xm} * R{ym} (SSFR)', 'R{a} = (R{xa} + R{ya})/2'),
          0x10: ('MRF = MRF - R{xm} * R{ym} (SSF)', 'R{a} = R{xa} + R{ya}'),
          0x11: ('MRF = MRF - R{xm} * R{ym} (SSF)', 'R{a} = R{xa} - R{ya}'),
          0x12: ('MRF = MRF - R{xm} * R{ym} (SSF)', 'R{a} = (R{xa} + R{ya})/2'),
          0x14: ('R{m} = MRF - R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} + R{ya}'),
          0x15: ('R{m} = MRF - R{xm} * R{ym} (SSFR)', 'R{a} = R{xa} - R{ya}'),
          0x16: ('R{m} = MRF - R{xm} * R{ym} (SSFR)', 'R{a} = (R{xa} + R{ya})/2'),
          0x18: ('F{m} = F{xm} * F{ym}', 'F{a} = F{xa} + F{ya}'),
          0x19: ('F{m} = F{xm} * F{ym}', 'F{a} = F{xa} - F{ya}'),
          0x1A: ('F{m} = F{xm} * F{ym}', 'F{a} = FLOAT R{xa} BY R{ya}'),
          0x1B: ('F{m} = F{xm} * F{ym}', 'R{a} = FIX F{xa} BY R{ya}'),
          0x1C: ('F{m} = F{xm} * F{ym}', 'F{a} = (F{xa} + F{ya})/2'),
          0x1D: ('F{m} = F{xm} * F{ym}', 'F{a} = ABS F{xa}'),
          0x1E: ('F{m} = F{xm} * F{ym}', 'F{a} = MAX(F{xa}, F{ya})'),
          0x1F: ('F{m} = F{xm} * F{ym}', 'F{a} = MIN(F{xa}, F{ya})')}

MR_REG = {0: 'MR0F', 1: 'MR1F', 2: 'MR2F', 4: 'MR0B', 5: 'MR1B', 6: 'MR2B'}


def mul_text(op, n, x, y):
    hi = op >> 6
    f, r = op >> 3 & 1, op & 1
    ys, xs = op >> 5 & 1, op >> 4 & 1
    mod = ('S' if xs else 'U') + ('S' if ys else 'U') + ('F' if f else 'I') + ('R' if r else '')
    dst = (op >> 1) & 3
    if hi == 1:
        d = {0: f'R{n}', 2: 'MRF', 3: 'MRB'}.get(dst, '?')
        return f'{d} = R{x} * R{y} ({mod})'
    if hi in (2, 3):
        acc = 'MRF' if dst in (0, 2) else 'MRB'
        d = {0: f'R{n}', 1: f'R{n}', 2: 'MRF', 3: 'MRB'}[dst]
        sign = '+' if hi == 2 else '-'
        return f'{d} = {acc} {sign} R{x} * R{y} ({mod})'
    if op >> 4 == 0:
        acc = 'MRF' if dst in (0, 2) else 'MRB'
        d = {0: f'R{n}', 1: f'R{n}', 2: 'MRF', 3: 'MRB'}[dst]
        return f'{d} = SAT {acc}'
    if op >> 4 == 1:
        if op == 0x14:
            return 'MRF = 0'
        if op == 0x16:
            return 'MRB = 0'
        acc = 'MRF' if dst in (0, 2) else 'MRB'
        d = {0: f'R{n}', 1: f'R{n}', 2: 'MRF', 3: 'MRB'}[dst]
        return f'{d} = RND {acc}'
    if op == 0x30:
        return f'F{n} = F{x} * F{y}'
    return f'MUL?{op:02x}'


def compute_text(c):
    """23-bit COMPUTE field."""
    if c == 0:
        return ''
    if c >> 22:                                          # multifunction
        if (c >> 20) & 3 in (2, 3):                      # multiplier + dual add/sub
            fl = (c >> 20) & 1
            s, m, a = bits(c, 19, 16), bits(c, 15, 12), bits(c, 11, 8)
            xm, ym, xa, ya = bits(c, 7, 6), bits(c, 5, 4) + 4, bits(c, 3, 2) + 8, bits(c, 1, 0) + 12
            r = 'F' if fl else 'R'
            mul = f'{r}{m} = {r}{xm} * {r}{ym}' + ('' if fl else ' (SSFR)')
            return f'{mul}, {r}{a} = {r}{xa} + {r}{ya}, {r}{s} = {r}{xa} - {r}{ya}'
        op = bits(c, 21, 16)
        m, a = bits(c, 15, 12), bits(c, 11, 8)
        xm, ym, xa, ya = bits(c, 7, 6), bits(c, 5, 4) + 4, bits(c, 3, 2) + 8, bits(c, 1, 0) + 12
        p = MF_OPS.get(op)
        if not p:
            return f'MULTIFUNC?{op:02x}'
        f = dict(m=m, a=a, xm=xm, ym=ym, xa=xa, ya=ya)
        return p[0].format(**f) + ', ' + p[1].format(**f)
    cu = bits(c, 21, 20)
    op = bits(c, 19, 12)
    n, x, y = bits(c, 11, 8), bits(c, 7, 4), bits(c, 3, 0)
    if cu == 0:
        if bits(c, 19, 16) in (0x7, 0xF):                # dual add/subtract
            fl = bits(c, 19, 16) == 0xF
            s, a = bits(c, 15, 12), bits(c, 11, 8)
            r = 'F' if fl else 'R'
            return f'{r}{a} = {r}{x} + {r}{y}, {r}{s} = {r}{x} - {r}{y}'
        t = ALU_FIX.get(op)
        return t.format(n=n, x=x, y=y) if t else f'ALU?{op:02x}'
    if cu == 1:
        return mul_text(op, n, x, y)
    if cu == 2:
        if bits(c, 22, 17) == 0b100000 >> 0 and False:
            pass
        t = SHF.get(op)
        return t.format(n=n, x=x, y=f'R{y}') if t else f'SHIFT?{op:02x}'
    # cu == 3 is not used by single-function ops; MR data move is 100000 in 22..17
    return f'CU3?{c:06x}'


def mr_move_text(c):
    d, op, r = bits(c, 16, 16), bits(c, 15, 12), bits(c, 3, 0)
    return f'{MR_REG.get(op, "MR?")} = R{r}' if d else f'R{r} = {MR_REG.get(op, "MR?")}'


def comp(c):
    if bits(c, 22, 17) == 0b100000 and False:
        return mr_move_text(c)
    return compute_text(c)


# ------------------------------------------------------------------ instruction types
def decode(w, pc=0):
    """Return (type, text) for a 48-bit instruction at address pc."""
    top3 = bits(w, 47, 45)
    t8 = bits(w, 47, 40)
    cond = COND[bits(w, 37, 33)]
    c23 = bits(w, 22, 0)
    ct = comp(c23)

    def iff(s):
        return s if cond == 'TRUE' else f'IF {cond} {s}'

    if w == 0:
        return '21a', 'NOP'
    if top3 == 0b001:                                    # type 1: compute + dual move
        dmd, dmi, dmm = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38)
        pmd, dmr = bits(w, 37, 37), bits(w, 36, 33)
        pmi, pmm, pmr = bits(w, 32, 30) + 8, bits(w, 29, 27) + 8, bits(w, 26, 23)
        dm = f'DM(I{dmi},M{dmm}) = R{dmr}' if dmd else f'R{dmr} = DM(I{dmi},M{dmm})'
        pm = f'PM(I{pmi},M{pmm}) = R{pmr}' if pmd else f'R{pmr} = PM(I{pmi},M{pmm})'
        return '1a', ', '.join(x for x in (ct, dm, pm) if x)
    if top3 == 0b010:                                    # type 3: ureg <-> mem, I/M
        u, i, m = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38)
        g, d, l, ur = bits(w, 32, 32), bits(w, 31, 31), bits(w, 30, 30), ureg(bits(w, 29, 23))
        mem = 'PM' if g else 'DM'
        if g:
            i, m = i + 8, m + 8
        addr = f'{mem}(I{i},M{m})' if u else f'{mem}(M{m},I{i})'
        mv = f'{addr} = {ur}' if d else f'{ur} = {addr}'
        if l:
            mv += ' (LW)'
        return '3a', iff(', '.join(x for x in (ct, mv) if x))
    if top3 == 0b011:
        if bits(w, 44, 44) == 0:                         # type 4: imm6 modify + move
            i, g, d, u = bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39), bits(w, 38, 38)
            data, dreg = sx(bits(w, 32, 27), 6), bits(w, 26, 23)
            mem = 'PM' if g else 'DM'
            if g:
                i += 8
            addr = f'{mem}(I{i},{data})' if u else f'{mem}({data},I{i})'
            mv = f'{addr} = R{dreg}' if d else f'R{dreg} = {addr}'
            return '4a', iff(', '.join(x for x in (ct, mv) if x))
        if bits(w, 43, 43) == 0:                         # type 5: ureg = ureg
            src = (bits(w, 42, 38) << 2) | bits(w, 32, 31)
            dst = bits(w, 30, 23)
            mv = f'{ureg(dst)} = {ureg(src)}'
            return '5a', iff(', '.join(x for x in (ct, mv) if x))
        return '5a', iff(', '.join(x for x in (ct, f'R{bits(w, 26, 23)} <-> S{bits(w, 42, 38)}') if x))
    if top3 == 0b100:
        if bits(w, 44, 44) == 0:                         # type 6: imm shift + move
            i, m = bits(w, 43, 41), bits(w, 40, 38)
            g, d = bits(w, 32, 32), bits(w, 31, 31)
            dataex, dreg = bits(w, 30, 27), bits(w, 26, 23)
            op = bits(w, 21, 16)
            data = (dataex << 8) | bits(w, 15, 8)
            rn, rx = bits(w, 7, 4), bits(w, 3, 0)
            t = SHF.get(op << 2, f'SHIFTIM?{op:02x}')
            sh = t.format(n=rn, x=rx, y=sx(bits(w, 15, 8), 8) if op << 2 in (0, 0x20, 4, 0x24, 8)
                          else data)
            return '6a', iff(sh)
        i, m, g = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37)  # type 16: imm32 -> mem
        mem = 'PM' if g else 'DM'
        if g:
            i, m = i + 8, m + 8
        return '16a', f'{mem}(I{i},M{m}) = {bits(w, 31, 0):#x}'
    if top3 == 0b101:                                    # type 15: ureg <-> mem, I + data32
        g, i, d, l = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39)
        ur, data = ureg(bits(w, 38, 32)), sx(bits(w, 31, 0), 32)
        mem = 'PM' if g else 'DM'
        if g:
            i += 8
        addr = f'{mem}({data:#x},I{i})'
        return '15a', (f'{addr} = {ur}' if d else f'{ur} = {addr}') + (' (LW)' if l else '')
    if top3 in (0b110, 0b111):                           # type 10: jump + else compute + move
        d, dmi, dmm = bits(w, 44, 44), bits(w, 43, 41), bits(w, 40, 38)
        dreg = bits(w, 26, 23)
        if top3 == 0b110:
            tgt = f'(M{bits(w, 29, 27) + 8},I{bits(w, 32, 30) + 8})'
        else:
            tgt = f'(PC,{sx(bits(w, 32, 27), 6)})'
        mv = f'DM(I{dmi},M{dmm}) = R{dreg}' if d else f'R{dreg} = DM(I{dmi},M{dmm})'
        return '10a', f'IF {cond} JUMP {tgt}, ELSE {ct}, {mv}'
    # top3 == 000: group by bits 44..40
    sub = bits(w, 44, 40)
    if sub == 0b00001:                                   # type 2: cond + compute
        return '2a', iff(ct) if ct else iff('NOP')
    if sub == 0b00010:                                   # type 6 without memory move
        op = bits(w, 21, 16)                             # (layout inferred from the kernel;
        rn, rx = bits(w, 7, 4), bits(w, 3, 0)            #  the manual prints the 'with move'
        data = (bits(w, 30, 27) << 8) | bits(w, 15, 8)   #  table twice)
        t = SHF.get(op << 2, f'SHIFTIM?{op:02x}')
        y = sx(bits(w, 15, 8), 8) if op << 2 in (0, 0x20, 4, 0x24, 8) else data
        return '6a', iff(t.format(n=rn, x=rx, y=y))
    if sub == 0b00100:                                   # type 7: compute + modify
        g, isrc, m, idis = bits(w, 38, 38), bits(w, 32, 30), bits(w, 29, 27), bits(w, 26, 24)
        if g:
            isrc, m = isrc + 8, m + 8
        mod = f'MODIFY(I{isrc},M{m})'
        return '7a', iff(', '.join(x for x in (ct, mod) if x))
    if sub in (0b00110, 0b00111):                        # type 8: direct/relative branch
        b, a = bits(w, 39, 39), bits(w, 38, 38)
        j, ci = bits(w, 26, 26), bits(w, 24, 24)
        addr = bits(w, 23, 0)
        if sub == 0b00111:
            addr = (pc + sx(addr, 24)) & 0xFFFFFF
        op = 'CALL' if b else 'JUMP'
        fl = [x for x, on in (('DB', j), ('LA', a), ('CI', ci)) if on]
        return '8a', iff(f'{op} {addr:#x}' + (f' ({", ".join(fl)})' if fl else ''))
    if sub in (0b01000, 0b01001):                        # type 9: indirect/rel + compute
        b, a = bits(w, 39, 39), bits(w, 38, 38)
        j, e, ci = bits(w, 26, 26), bits(w, 25, 25), bits(w, 24, 24)
        if sub == 0b01000:
            tgt = f'(M{bits(w, 29, 27) + 8},I{bits(w, 32, 30) + 8})'
        else:
            tgt = f'{(pc + sx(bits(w, 32, 27), 6)) & 0xFFFFFF:#x}'
        op = 'CALL' if b else 'JUMP'
        fl = [x for x, on in (('DB', j), ('LA', a), ('CI', ci)) if on]
        s = iff(f'{op} {tgt}' + (f' ({", ".join(fl)})' if fl else ''))
        if ct:
            s += (', ELSE ' if e else ', ') + ct
        return '9a', s
    if sub in (0b01010, 0b01011):                        # type 11: RTS / RTI
        j, e = bits(w, 26, 26), bits(w, 25, 25)
        op = 'RTS' if sub == 0b01010 else 'RTI'
        fl = [x for x, on in (('DB', j), ('LR', bits(w, 24, 24) and sub == 0b01010)) if on]
        s = iff(op + (f' ({", ".join(fl)})' if fl else ''))
        if ct:
            s += (', ELSE ' if e else ', ') + ct
        return '11a', s
    if sub == 0b01100:                                   # type 12: LCNTR = imm, DO UNTIL LCE
        return '12a', f'LCNTR = {bits(w, 39, 24)}, DO {(pc + sx(bits(w, 23, 0), 24)) & 0xFFFFFF:#x} UNTIL LCE'
    if sub == 0b01101:
        return '12a', f'LCNTR = {ureg(bits(w, 38, 32))}, DO {(pc + sx(bits(w, 23, 0), 24)) & 0xFFFFFF:#x} UNTIL LCE'
    if sub == 0b01110:                                   # type 13: DO UNTIL term
        return '13a', f'DO {(pc + sx(bits(w, 23, 0), 24)) & 0xFFFFFF:#x} UNTIL {TERM[bits(w, 37, 33)]}'
    if sub == 0b01111:                                   # type 17: ureg = imm32
        return '17a', f'{ureg(bits(w, 38, 32))} = {bits(w, 31, 0):#x}'
    if sub >> 2 == 0b100:                                # type 14: ureg <-> mem direct
        g, d, l = bits(w, 41, 41), bits(w, 40, 40), bits(w, 39, 39)
        ur, addr = ureg(bits(w, 38, 32)), bits(w, 31, 0)
        mem = 'PM' if g else 'DM'
        s = f'{mem}({addr:#x}) = {ur}' if d else f'{ur} = {mem}({addr:#x})'
        return '14a', s + (' (LW)' if l else '')
    if sub == 0b10100:                                   # type 18: bit op on sreg
        bop = bits(w, 39, 37)
        name = {0: 'SET', 1: 'CLR', 2: 'TGL', 4: 'TST', 5: 'XOR'}.get(bop, f'?{bop}')
        return '18a', f'BIT {name} {SREG[bits(w, 36, 32)]} {bits(w, 31, 0):#x}'
    if sub == 0b10110:                                   # type 19: modify / bitrev imm
        br, g, idis, isrc = bits(w, 39, 39), bits(w, 38, 38), bits(w, 37, 35), bits(w, 34, 32)
        if g:
            isrc += 8
        data = sx(bits(w, 31, 0), 32)
        return '19a', (f'BITREV(I{isrc},{data})' if br else f'MODIFY(I{isrc},{data})')
    if sub == 0b10111:                                   # type 20: push/pop
        f = [n for n, b in (('PUSH LOOP', 39), ('POP LOOP', 38), ('PUSH STS', 37),
                            ('POP STS', 36), ('PUSH PCSTK', 35), ('POP PCSTK', 34),
                            ('FLUSH CACHE', 33)) if bits(w, b, b)]
        return '20a', ', '.join(f) or 'PUSH/POP?'
    if sub == 0b00000:                                   # type 22 idle / 21 nop
        if bits(w, 39, 39):
            return '22a', 'IDLE'
        if bits(w, 38, 38):
            return '22a', 'EMUIDLE'
        return '21a', 'NOP'
    if t8 == 0x18:
        return '25a', f'CJUMP {bits(w, 23, 0):#x} (DB)'
    if t8 == 0x19:
        return '25a', 'RFRAME'
    return '?', f'.word48 {w:012x}'


# ------------------------------------------------------------------ VISA (16/32/48-bit)
SHORT_OPS = ['R{n} = R{n} + R{x}', 'R{n} = R{n} - R{x}', 'R{n} = PASS R{x}', 'COMP(R{n}, R{x})',
             'R{n} = NOT R{x}', 'R{n} = R{x} + 1', 'R{n} = R{x} - 1', 'R{n} = R{n} * R{x} (SSI)',
             'F{n} = F{n} + F{x}', 'F{n} = F{n} - F{x}', 'F{n} = FLOAT R{x}', 'COMP(F{n}, F{x})',
             'R{n} = R{n} AND R{x}', 'R{n} = R{n} OR R{x}', 'R{n} = R{n} XOR R{x}',
             'F{n} = F{n} * F{x}']


def visa_length(w):
    """Length in 16-bit parcels of the VISA instruction whose first 48 bits are w."""
    p0 = w >> 32
    top4, top8 = p0 >> 12, p0 >> 8
    if top4 == 0b1100:                                    # type 2c short compute
        return 1
    if top4 == 0b1001:                                    # 16a / 16b / 15b / 3c
        sel = bits(w, 36, 34)
        return 3 if sel == 0 else 2 if sel in (1, 2) else 1
    if top8 in (0x0A, 0x0B) and bits(w, 39, 39):          # type 11c RTS/RTI
        return 1
    if top8 == 0x00 and bits(w, 32, 32):                  # type 21c NOP / 22c IDLE
        return 1
    if p0 == 0x1901:                                      # type 25c RFRAME
        return 1
    if top8 in (0x01, 0x0F) and bits(w, 39, 39):          # type 2b / 17b
        return 2
    if bits(w, 22, 16) == 0b0111111 and (w >> 45 in (0b001, 0b010, 0b011) or
                                         top8 in (0x04, 0x08, 0x09)):
        return 2                                          # 1b/3b/4b/5b/7b/9b
    return 3


def decode_visa(w, pc=0):
    """Decode the instruction at the top of w (48 bits); return (length, type, text)."""
    n = visa_length(w)
    p0 = w >> 32
    if n == 1:
        if p0 >> 12 == 0b1100:
            op, rn, rx = bits(w, 43, 40), bits(w, 39, 36), bits(w, 35, 32)
            return 1, '2c', SHORT_OPS[op].format(n=rn, x=rx)
        if p0 >> 12 == 0b1001:
            dmi, dmm, d, dreg = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37), bits(w, 35, 32)
            a = f'DM(I{dmi},M{dmm})'
            return 1, '3c', f'{a} = R{dreg}' if d else f'R{dreg} = {a}'
        if p0 >> 8 in (0x0A, 0x0B):
            op = 'RTS' if p0 >> 8 == 0x0A else 'RTI'
            c = COND[bits(w, 37, 33)]
            s = op + (' (DB)' if bits(w, 38, 38) else '')
            return 1, '11c', s if c == 'TRUE' else f'IF {c} {s}'
        if p0 == 0x1901:
            return 1, '25c', 'RFRAME'
        return 1, '21c', 'IDLE' if bits(w, 39, 39) else 'EMUIDLE' if bits(w, 38, 38) else 'NOP'
    if n == 2:
        top8 = p0 >> 8
        if top8 == 0x01:                                  # 2b: compute at bits 38..16
            return 2, '2b', compute_text(bits(w, 38, 16)) or 'NOP'
        if top8 == 0x0F:                                  # 17b: ureg = data16
            return 2, '17b', f'{ureg(bits(w, 38, 32))} = {sx(bits(w, 31, 16), 16):#x}'
        if p0 >> 12 == 0b1001:
            sel = bits(w, 36, 34)
            if sel == 1:                                  # 16b: imm16 -> mem
                i, m, g = bits(w, 43, 41), bits(w, 40, 38), bits(w, 37, 37)
                mem = 'PM' if g else 'DM'
                if g:
                    i, m = i + 8, m + 8
                return 2, '16b', f'{mem}(I{i},M{m}) = {sx(bits(w, 31, 16), 16):#x}'
            i, d, l, g = bits(w, 43, 41), bits(w, 40, 40), bits(w, 39, 39), bits(w, 38, 38)
            ur, data = ureg(bits(w, 33, 27)), sx(bits(w, 26, 20), 7)
            mem = 'PM' if g else 'DM'
            if g:
                i += 8
            a = f'{mem}({data},I{i})'
            return 2, '15b', (f'{a} = {ur}' if d else f'{ur} = {a}') + (' (LW)' if l else '')
        t, s = decode(w & ~0x7FFFFF, pc)                 # b-forms: no compute field
        return 2, t[:-1] + 'b', s
    t, s = decode(w, pc)
    return 3, t, s


def visa_stream(parcels, base):
    """Disassemble a list of 16-bit parcels starting at short-word address base."""
    out = []
    i = 0
    while i < len(parcels):
        w = 0
        for k in range(3):
            w = (w << 16) | (parcels[i + k] if i + k < len(parcels) else 0)
        n, t, s = decode_visa(w, base + i)
        out.append((base + i, n, w >> (16 * (3 - n)), t, s))
        i += n
    return out


def text(w, pc=0):
    return decode(w, pc)[1]


if __name__ == '__main__':
    import sharc_ldr as L
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--kernel', action='store_true')
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0x8C000)
    a = ap.parse_args()
    ws = L.stream_words(open(a.image, 'rb').read())
    ins = L.unpack48(ws[:384])
    for n, w in enumerate(ins):
        t, s = decode(w, a.base + n)
        print(f'{a.base + n:06x}  {w:012x}  {t:4} {s}')
