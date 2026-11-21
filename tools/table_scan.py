#!/usr/bin/env python3
"""Scale-invariant search for known numeric tables inside binary images.

For every template window v (a run of consecutive table values) and every position
in an image interpreted as an int16/int32/float32 array x, fit x ~= s*v and report
windows whose relative residual is tiny. Finds a table regardless of its fixed-point
scale. Usage: table_scan.py <header.h ...> -- <image ...>
"""
import re, sys
import numpy as np

def parse_tables(path):
    src = open(path).read()
    out = {}
    for m in re.finditer(r'const\s+(float|int)\s+(\w+)((?:\[\d+\])+)\s*=\s*\{(.*?)\};', src, re.S):
        typ, name, dims, body = m.groups()
        dims = [int(d) for d in re.findall(r'\[(\d+)\]', dims)]
        vals = np.array([float(v) for v in re.findall(r'-?\d+\.?\d*(?:[eE]-?\d+)?', body)])
        if len(vals) != int(np.prod(dims)):
            continue
        out[name] = vals.reshape(dims)
    return out

def windows(tab, W=8):
    flats = {'row': tab.reshape(-1)}
    if tab.ndim == 2:
        flats['col'] = tab.T.reshape(-1)
    for lay, f in flats.items():
        for start in range(0, max(1, len(f) - W), max(W, len(f) // 4)):
            v = f[start:start + W]
            if len(v) == W and np.ptp(v) > 0 and len(set(np.round(v, 6))) > W // 2:
                yield lay, start, v

def views(raw):
    for name, dt in (('s16be', '>i2'), ('s16le', '<i2'), ('s32le', '<i4'), ('f32le', '<f4')):
        n = len(raw) // np.dtype(dt).itemsize
        for off in range(np.dtype(dt).itemsize):
            m = (len(raw) - off) // np.dtype(dt).itemsize
            a = np.frombuffer(raw[off:off + m * np.dtype(dt).itemsize], dtype=dt).astype(np.float64)
            a[~np.isfinite(a)] = 0
            yield name, off, a

def scan(img, raw, tables, W=8, tol=2e-4):
    hits = []
    for vname, voff, x in views(raw):
        ones = np.ones(W)
        xx = np.convolve(x * x, ones, 'valid')
        for tname, tab in tables.items():
            for lay, start, v in windows(tab, W):
                vv = v @ v
                xv = np.correlate(x, v, 'valid')
                s = xv / vv
                res = (xx - s * xv) / np.maximum(xx, 1e-30)
                ok = np.where((res < tol) & (xx > (64 if 'f32' not in vname else 1e-6)) & (np.abs(s) > 0))[0]
                for i in ok[:3]:
                    hits.append((img, vname, voff + i * np.dtype({'s16be':'>i2','s16le':'<i2','s32le':'<i4','f32le':'<f4'}[vname]).itemsize,
                                 tname, lay, start, s[i], res[i]))
    return hits

if __name__ == '__main__':
    i = sys.argv.index('--')
    tables = {}
    for h in sys.argv[1:i]:
        tag = h.rsplit('/', 1)[-1].replace('_const.h', '')
        for k, v in parse_tables(h).items():
            tables[f'{tag}:{k}'] = v
    for img in sys.argv[i + 1:]:
        for h in scan(img, open(img, 'rb').read(), tables):
            print(f'{h[0].rsplit("/",1)[-1]:20} {h[1]:5} +0x{h[2]:07x}  {h[3]:34} {h[4]} @{h[5]:<4} scale={h[6]:.4g} res={h[7]:.1e}')
