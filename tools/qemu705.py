#!/usr/bin/env python3
"""Run the IC-705 flash image on the rz-a1h QEMU machine (RZA1H_BOARD=ic705) and observe it.

  qemu705.py [--secs N] [--samples K] [--shot out.png] [--debug devs] [--sd img] [-- extra qemu args]

Prints PC/LR/mode samples over time, a summary of accesses to unmodelled devices
(-d unimp), and optionally a screendump of the VDC5 console.
"""
import argparse, collections, json, os, re, socket, subprocess, sys, time

QEMU = os.path.expanduser('~/icom/ic7300-homebrew/qemu-machine/qemu-src/build/qemu-system-arm')
FLASH = os.path.expanduser('~/icom/out/705_flash.bin')
SCR = '/tmp/claude-1000/-home-nemanjan00-icom/a98c066f-3329-434e-a02a-fa726ac32d80/scratchpad'

class QMP:
    def __init__(self, path):
        for _ in range(100):
            try:
                self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); break
            except OSError:
                time.sleep(0.05)
        self.f = self.s.makefile('rw')
        json.loads(self.f.readline())
        self.cmd('qmp_capabilities')
    def cmd(self, name, **args):
        self.f.write(json.dumps({'execute': name, 'arguments': args}) + '\n'); self.f.flush()
        while True:
            r = json.loads(self.f.readline())
            if 'return' in r or 'error' in r:
                return r
    def hmp(self, line):
        return self.cmd('human-monitor-command', **{'command-line': line}).get('return', '')

def regs(q):
    t = q.hmp('info registers')
    g = dict(re.findall(r'(R\d\d|PSR)=([0-9a-f]+)', t))
    return int(g.get('R15', '0'), 16), int(g.get('R14', '0'), 16), g.get('PSR', '?'), t

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--secs', type=float, default=20)
    ap.add_argument('--samples', type=int, default=10)
    ap.add_argument('--shot')
    ap.add_argument('--debug', default='')
    ap.add_argument('--sd')
    ap.add_argument('--icount', default='shift=1')
    ap.add_argument('--pins', default='3_9=0', help='RZA1H_PINS at reset (default: POWER held)')
    ap.add_argument('--release-at', type=float, default=5, help='release POWER (P3_9) after N s')
    ap.add_argument('--adc', default=','.join(['0x300'] * 8), help='RZA1H_ADC_VALS')
    ap.add_argument('--dsfr', default='0')
    ap.add_argument('--force-on', action='store_true',
                    help='boot hook: force power-mode dispatcher 0x2007B7C0 a1=1 (full bring-up)')
    ap.add_argument('extra', nargs='*')
    a = ap.parse_args()
    sock = f'{SCR}/qmp705.sock'; log = f'{SCR}/qemu705_unimp.log'
    for p in (sock, log):
        if os.path.exists(p): os.unlink(p)
    args = [QEMU, '-M', 'rz-a1h', '-kernel', FLASH, '-display', 'none', '-serial', 'none',
            '-monitor', 'none', '-qmp', f'unix:{sock},server=on,wait=off',
            '-d', 'unimp,guest_errors', '-D', log]
    if a.icount != 'off': args += ['-icount', a.icount]
    if a.sd: args += ['-drive', f'if=sd,format=raw,file={a.sd}']
    args += a.extra
    env = dict(os.environ, RZA1H_BOARD='ic705', RZA1H_DEBUG=a.debug, RZA1H_PINS=a.pins,
               RZA1H_ADC_VALS=a.adc, RZA1H_DSFR=a.dsfr)
    if a.force_on:
        args += ['-S', '-gdb', 'tcp::1240']
    p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=open(f'{SCR}/qemu705_stderr.log', 'w'))
    q = QMP(sock)
    t0 = time.time()
    if a.force_on:
        import rsp
        g = rsp.RSP(port=1240, timeout=120); g.cmd('?')
        g.bp(0x2007B7C0); g.cont()
        print('boot hook: dispatcher a1 =', g.regs()[0], '-> 1')
        g.cmd('P0=01000000'); g.bp(0x2007B7C0, False); g._send(b'c')
    released = False
    pcs = collections.Counter()
    for i in range(a.samples):
        time.sleep(a.secs / a.samples)
        if not released and time.time() - t0 >= a.release_at:
            q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=1'); released = True
        pc, lr, psr, _ = regs(q)
        pcs[pc] += 1
        print(f't={a.secs/a.samples*(i+1):5.1f}s pc={pc:#010x} lr={lr:#010x} psr={psr}', flush=True)
    if a.shot:
        r = q.cmd('screendump', filename=a.shot, format='png'); print('screendump', r)
    try: q.cmd('quit')
    except Exception: pass
    p.wait(timeout=10)
    acc = collections.Counter()
    for line in open(log, errors='ignore'):
        m = re.search(r'(\S+): unimplemented device (read|write).*?offset (0x[0-9a-f]+)', line)
        if m: acc[(m.group(1), m.group(2), int(m.group(3), 16))] += 1
    print('--- unimplemented accesses (top 25) ---')
    for (dev, rw, off), n in acc.most_common(25):
        print(f'{n:7} {dev:28} {rw:5} +{off:#07x}')
    print('stderr tail:', open(f'{SCR}/qemu705_stderr.log').read()[-600:])

if __name__ == '__main__':
    main()
