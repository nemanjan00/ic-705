#!/usr/bin/env python3
"""Run the emulated IC-705 in a GTK window (rz-a1h QEMU machine, RZA1H_BOARD=ic705).

  run705_gui.py [--behavioral-sub] [--substat FILE] [--press-at S] [--hold S]

Default boot is the real power-on path (all documented in ic705/EMULATION.md):
  - sub-CPU = STM32L0 model running the decoded aux2 firmware (RZA1H_SUBCPU_FW), wired
    to SCIF3 (clock-synchronous SPI) and the P7_11/P5_3/P7_9/P5_4 handshake lines
  - ARM starts, finds no power-on factor and enters deep standby
  - POWER (P3_9) is pressed after --press-at s for --hold s: deep-standby cancel with
    factor bit 10, the loader's P8_7 wait times out (P8_7 held low) and sets the
    power-on flag -> full bring-up, main screen after ~90 s of wall time
  - ADC channels at 0x300 except ch6 = 0x3FF (ch6 in 721..802 + key bits = adjust mode)
  - DSP/FPGA SPI flash on RSPI4 = decoded DSP group (DSPs/FPGA themselves not emulated)
--behavioral-sub uses the old scripted sub-CPU stand-in (status file, see --substat).
"""
import argparse, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qemu705 as Q

FW = os.path.expanduser('~/icom/out/705E140_full')
ap = argparse.ArgumentParser()
ap.add_argument('--behavioral-sub', action='store_true')
ap.add_argument('--substat', default=os.path.join(Q.SCR, 'substat.txt'))
ap.add_argument('--press-at', type=float, default=6)
ap.add_argument('--hold', type=float, default=3)
a = ap.parse_args()
adc = ['0x300'] * 8
adc[6] = '0x3ff'
env = dict(os.environ, RZA1H_BOARD='ic705', RZA1H_PINS='8_7=0', RZA1H_DSWAKE_BIT='10',
           RZA1H_ADC_VALS=','.join(adc), RZA1H_RSPI4_FLASH=f'{FW}/dsp.image.bin')
if a.behavioral_sub:
    open(a.substat, 'a').close()
    env['RZA1H_IC705_SUBSTAT_FILE'] = a.substat
else:
    env['RZA1H_SUBCPU_FW'] = f'{FW}/aux2.application.bin'
sock = os.path.join(Q.SCR, 'qmp_gui.sock')
if os.path.exists(sock):
    os.unlink(sock)
args = [Q.QEMU, '-M', 'rz-a1h', '-kernel', Q.FLASH, '-display', 'gtk,zoom-to-fit=on',
        '-serial', 'none', '-monitor', 'none', '-icount', 'shift=1',
        '-qmp', f'unix:{sock},server=on,wait=off']
p = subprocess.Popen(args, env=env)
q = Q.QMP(sock)
time.sleep(a.press_at)
q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=0')
time.sleep(a.hold)
q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=1')
print('POWER pressed and released; the main screen appears after ~90 s.')
p.wait()
