#!/usr/bin/env python3
"""DV-DSP modulator PoC: run the firmware's gmsk_mod_bit in the C55x micro-emulator.

  stock   : positive control -> must reproduce an independent GMSK model
  patched : 4FSK (DMR) variant -> must demodulate back to the input dibits
"""
import math, random, struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from c55_emu import load, hle_dsplib_fir, s16, C55

# names given in our IDA database (byte addresses)
C55.symbols = {'gmsk_dev': 0x65D8, 'gmsk_pulse_bt05_x10': 0x1FCD2,
               'gmsk_delayline': 0x6594, 'fir': 0xAAC8}

OUT = '/home/nemanjan00/icom/out/705E140_full'
FN = 0x16B60          # gmsk_mod_bit (byte address)
FIR = 0xAAC8          # DSPLIB fir
W_DEV, W_INV = 0x65D8 // 2, 0x65E6 // 2
OUTBUF = 0x6800       # scratch word address for the 10 output samples

def run(insn_json, symbols, setup=None):
    st = {}
    cpu = load(f'{OUT}/dspB.mem', insn_json, hle={FIR: hle_dsplib_fir(st)})
    cpu.r['XSP'] = 0x6300
    cpu.wr(W_DEV, 0x6000); cpu.wr(W_INV, 0)
    if setup: setup(cpu)
    y = []
    for s in symbols:
        cpu.r['T0'] = s
        cpu.r['XAR0'] = OUTBUF
        cpu.call(FN)
        y += [s16(cpu.rd(OUTBUF + i)) for i in range(10)]
    return y, cpu

def gmsk_model(bits, table, dev=0x6000):
    # independent model: impulse of -/+dev (bit 1 -> -dev, bit 0 -> +dev) at the last
    # slot of each 10-sample block, convolved with the 32-tap pulse in Q15
    x = []
    for b in bits:
        x += [0] * 9 + [(-dev if b else dev)]
    y, hist = [], [0] * len(table)
    for v in x:
        hist = [v] + hist[:-1]
        acc = sum(a * b for a, b in zip(table, hist)) << 1
        y.append(max(-32768, min(32767, (acc + 0x8000) >> 16)))
    return y

if __name__ == '__main__':
    random.seed(1)
    bits = [random.getrandbits(1) for _ in range(200)]
    y, cpu = run(f'{OUT}/c55_gmsk_mod_bit.json', bits)
    table = [s16(cpu.rd(0x1FCD2 // 2 + i)) for i in range(32)]
    ref = gmsk_model(bits, table)
    diff = max(abs(a - b) for a, b in zip(y, ref))
    print(f'stock gmsk_mod_bit: {len(y)} samples, max |emu - model| = {diff}',
          'OK' if diff == 0 else 'MISMATCH')
    print('peak', max(map(abs, y)), 'of 32767 =', round(max(map(abs, y)) / 32767, 3))
    struct.pack_into  # noqa


# ---------------------------------------------------------------- 4FSK (DMR) PoC
SPS, ALPHA, SPAN = 10, 0.2, 8
NT = SPS * SPAN                       # 80 taps, matches the patched "mov #50h, T1"
W_RRC, W_DLY, W_LVL = 0x7400, 0x7480, 0x7500
DIBIT_LEVEL = {0b01: +3, 0b00: +1, 0b10: -1, 0b11: -3}   # ETSI TS 102 361-1 §10.2
MS_VOICE_SYNC = 0x7F7D5DD57DFD                            # 48 bits = 24 dibits

def rrc(n_taps, sps, a):
    h = []
    for i in range(n_taps):
        t = (i - (n_taps - 1) / 2) / sps
        if abs(t) < 1e-9:
            v = 1 - a + 4 * a / math.pi
        elif abs(abs(t) - 1 / (4 * a)) < 1e-9:
            v = a / math.sqrt(2) * ((1 + 2 / math.pi) * math.sin(math.pi / (4 * a)) +
                                    (1 - 2 / math.pi) * math.cos(math.pi / (4 * a)))
        else:
            v = (math.sin(math.pi * t * (1 - a)) + 4 * a * t * math.cos(math.pi * t * (1 + a))) / \
                (math.pi * t * (1 - (4 * a * t) ** 2))
        h.append(v)
    return h

def q15_table(h):
    m = max(abs(v) for v in h)
    return [round(v / m * 32000) for v in h]

def demod(y, sps=SPS):
    """Receiver model: RRC matched filter, best-phase sampling, 4-level slicer."""
    h = rrc(NT, sps, ALPHA)
    z = [sum(h[k] * y[n - k] for k in range(len(h)) if 0 <= n - k < len(y)) for n in range(len(y))]
    best = None
    for ph in range(sps):
        pts = z[ph::sps]
        a = sorted(abs(p) for p in pts)
        spread = a[int(len(a) * 0.9)]
        score = sum(min(abs(abs(p) - spread), abs(abs(p) - spread / 3)) for p in pts) / spread
        if best is None or score < best[0]:
            best = (score, ph, pts, spread)
    _, ph, pts, outer = best
    inv = {v: k for k, v in DIBIT_LEVEL.items()}
    lv = [min((+3, +1, -1, -3), key=lambda L: abs(p / (outer / 3) - L)) for p in pts]
    return [inv[L] for L in lv], ph, pts, outer

def patched_setup(level_amp):
    def setup(cpu):
        for i, v in enumerate(q15_table(rrc(NT, SPS, ALPHA))):
            cpu.wr(W_RRC + i, v)
        for i in range(NT + 2):
            cpu.wr(W_DLY + i, 0)
        for d, L in DIBIT_LEVEL.items():
            cpu.wr(W_LVL + d, round(level_amp * L / 3))
    return setup

def dmr_test_dibits(n_payload=200, seed=7):
    random.seed(seed)
    pre = [random.getrandbits(2) for _ in range(n_payload // 2)]
    sync = [(MS_VOICE_SYNC >> (46 - 2 * i)) & 3 for i in range(24)]
    post = [random.getrandbits(2) for _ in range(n_payload // 2)]
    return pre + sync + post, len(pre)

def run_4fsk(level_amp=0x5000):
    dib, sync_at = dmr_test_dibits()
    y, cpu = run(f'{OUT}/c55_mod4fsk_patched.json', dib, setup=patched_setup(level_amp))
    return dib, sync_at, y

if __name__ == '__main__':
    for amp in (0x5000, 0x6000):
        dib, sync_at, y = run_4fsk(amp)
        rx, ph, pts, outer = demod(y)
        # group delay: TX RRC (NT-1)/2 + RX RRC (NT-1)/2 samples + the slot-9 impulse
        lag = min(range(0, 20), key=lambda L: sum(a != b for a, b in zip(dib, rx[L:])))
        errs = sum(a != b for a, b in zip(dib[:-10], rx[lag:lag + len(dib) - 10]))
        peak = max(map(abs, y))
        sync = [(MS_VOICE_SYNC >> (46 - 2 * i)) & 3 for i in range(24)]
        found = [i for i in range(len(rx) - 24) if rx[i:i + 24] == sync]
        print(f'4FSK amp=0x{amp:04x}: peak {peak} ({peak/32767:.2f} FS), symbol lag {lag}, '
              f'symbol errors {errs}/{len(dib)-10}, MS voice sync found at rx[{found}] '
              f'(sent at {sync_at}+lag={sync_at+lag})')
