#!/usr/bin/env python3
"""Run the emulated IC-705 in a GTK window and drive its controls from this terminal.

  run705_gui.py [--behavioral-sub] [--substat FILE] [--press-at S] [--hold S] [--eeprom FILE]

Boot is the real power-on path (ic705/EMULATION.md):
  - sub-CPU = STM32L0 model running the decoded aux2 firmware (RZA1H_SUBCPU_FW)
  - POWER (P3_9) pressed after --press-at s for --hold s: deep-standby cancel (factor
    bit 10), loader P8_7 timeout -> power-on flag -> full bring-up, UI after ~90 s
  - ARM ADC ch0..7 = 0x300 except ch6 = 0x3FF; sub-CPU ADC defaults in stm32l0.c
  - DSP/FPGA SPI flash on RSPI4 = decoded DSP group (DSPs/FPGA not emulated)
  - settings EEPROM image: --eeprom (default ~/icom/out/705_eeprom.bin)

Controls: the GTK window itself takes mouse = touch, wheel = dial, and the same letter keys
(see EMULATION.md "Window input"). From this terminal (it must have focus; keys go through the real sub-CPU ladder ADC path or the
MTU2 phase counter, see EMULATION.md "Front-panel keys"):
  m MENU   f FUNCTION   q QUICK   x/Esc EXIT   s M.SCOPE   k MULTI push   a AF/RF push
  l SPEECH/LOCK (hold 2 s)   u / d  UP / DOWN (hold 1 s)   r ch0-L3 (RIT-type key)
  1..3  ch0 L1 / ch1 L3 / ch1 L4 (not yet identified)
  Left/Right  dial -1/+1 count   Up/Down  dial +10/-10   PgUp/PgDn  dial +100/-100
  p POWER tap (P3_9)   P POWER hold 3 s   Q quit
"""
import argparse, os, subprocess, sys, termios, time, tty
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qemu705 as Q

FW = os.path.expanduser('~/icom/out/705E140_full')
LEVEL = {1: 0x0C0, 2: 0x400, 3: 0x800, 4: 0xC00}   # 12-bit ADC value per ladder level
KEYS = {  # key: (sub ADC channel, ladder level, hold seconds, label)
    'm': (1, 1, 0.7, 'MENU'), 'f': (14, 1, 0.7, 'FUNCTION'), 's': (14, 2, 0.7, 'M.SCOPE'),
    'q': (14, 3, 0.7, 'QUICK'), 'x': (14, 4, 0.7, 'EXIT'), '\x1b': (14, 4, 0.7, 'EXIT'),
    'k': (0, 2, 0.7, 'MULTI push'), 'a': (1, 2, 0.7, 'AF/RF push'), 'r': (0, 3, 0.7, 'ch0 L3'),
    'l': (15, 1, 2.0, 'SPEECH/LOCK (hold)'), 'u': (15, 3, 1.0, 'UP'), 'd': (15, 4, 1.0, 'DOWN'),
    '1': (0, 1, 0.7, 'ch0 L1'), '2': (1, 3, 0.7, 'ch1 L3'), '3': (1, 4, 0.7, 'ch1 L4'),
}
DIAL = {'\x1b[C': 1, '\x1b[D': -1, '\x1b[A': 10, '\x1b[B': -10, '\x1b[5~': 100, '\x1b[6~': -100}

ap = argparse.ArgumentParser()
ap.add_argument('--behavioral-sub', action='store_true')
ap.add_argument('--substat', default=os.path.join(Q.SCR, 'substat.txt'))
ap.add_argument('--press-at', type=float, default=6)
ap.add_argument('--hold', type=float, default=3)
ap.add_argument('--eeprom', default=os.path.expanduser('~/icom/out/705_eeprom.bin'))
a = ap.parse_args()
adc = ['0x300'] * 8
adc[6] = '0x3ff'
env = dict(os.environ, RZA1H_BOARD='ic705', RZA1H_PINS='8_7=0', RZA1H_DSWAKE_BIT='10',
           RZA1H_ADC_VALS=','.join(adc), RZA1H_RSPI4_FLASH=f'{FW}/dsp.image.bin',
           RZA1H_IC705_EEPROM=a.eeprom)
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


def power(hold):
    q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=0')
    time.sleep(hold)
    q.cmd('qom-set', path='/machine/gpio', property='pins', value='3_9=1')


def key(ch, lvl, hold):
    q.cmd('qom-set', path='/machine/subcpu', property='adc', value=f'{ch}={LEVEL[lvl]:#x}')
    time.sleep(hold)
    q.cmd('qom-set', path='/machine/subcpu', property='adc', value=f'{ch}=0xfff')


time.sleep(a.press_at)
power(a.hold)
print('POWER pressed; the main screen appears after ~90 s.\r\n' + __doc__.split('Controls')[1])
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setcbreak(fd)
try:
    while p.poll() is None:
        c = os.read(fd, 8).decode(errors='ignore')
        if c == 'Q':
            break
        if c in DIAL:
            q.cmd('qom-set', path='/machine/mtu2', property='dial', value=DIAL[c])
        elif c in KEYS:
            ch, lvl, hold, label = KEYS[c]
            print(f'{label}\r')
            key(ch, lvl, hold)
        elif c == 'p':
            power(0.5)
        elif c == 'P':
            power(3)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if p.poll() is None:
        q.cmd('quit')
    p.wait()
