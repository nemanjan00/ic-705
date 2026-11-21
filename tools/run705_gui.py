#!/usr/bin/env python3
"""Run the emulated IC-705 in a GTK window (rz-a1h QEMU machine, RZA1H_BOARD=ic705).

  run705_gui.py [--no-hook] [--substat FILE]

Boot sequence it automates (all documented in ic705/EMULATION.md):
  - POWER (P3_9) held at reset, released after 5 s
  - ADC channels at 0x300 (DC supply class 2 in 0x2000B770)
  - DSP/FPGA SPI flash on RSPI4 = decoded DSP group
  - boot hook: power-mode dispatcher 0x2007B7C0 forced to a1=1 (full bring-up), unless
    --no-hook
  - sub-CPU status override file (default scratch substat.txt): lines "off=hexbytes",
    re-read every 10 ms; e.g. echo 0=ff > substat.txt pulses status byte 0.
"""
import argparse, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qemu705 as Q, rsp

ap = argparse.ArgumentParser()
ap.add_argument('--no-hook', action='store_true')
ap.add_argument('--substat', default=os.path.join(Q.SCR, 'substat.txt'))
a = ap.parse_args()
open(a.substat, 'a').close()
env = dict(os.environ, RZA1H_BOARD='ic705', RZA1H_PINS='3_9=0',
           RZA1H_ADC_VALS=','.join(['0x300'] * 8), RZA1H_IC705_SUBSTAT_FILE=a.substat,
           RZA1H_RSPI4_FLASH=os.path.expanduser('~/icom/out/705E140_full/dsp.image.bin'))
sock = os.path.join(Q.SCR, 'qmp_gui.sock')
if os.path.exists(sock):
    os.unlink(sock)
args = [Q.QEMU, '-M', 'rz-a1h', '-kernel', Q.FLASH, '-display', 'gtk,zoom-to-fit=on',
        '-serial', 'none', '-monitor', 'none', '-icount', 'shift=1',
        '-qmp', f'unix:{sock},server=on,wait=off']
if not a.no_hook:
    args += ['-S', '-gdb', 'tcp::1250']
p = subprocess.Popen(args, env=env)
q = Q.QMP(sock)
if not a.no_hook:
    g = rsp.RSP(port=1250, timeout=180); g.cmd('?')
    g.bp(0x2007B7C0); g.cont()
    g.cmd('P0=01000000'); g.bp(0x2007B7C0, False); g._send(b'c')
    print('boot hook applied (power-mode dispatcher -> full bring-up)')
time.sleep(5)
q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=1')
print('POWER released; window is live. sub-CPU status file:', a.substat)
p.wait()
