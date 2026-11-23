#!/usr/bin/env python3
"""Tk front panel for the emulated IC-705 (used by run705_gui.py; see ic705/EMULATION.md
"Front-panel inputs").

Every control goes through QMP to the same inputs the firmware reads:
  - sub-CPU key ladders: /machine/subcpu adc "<ch>=<12-bit level>" (ch 0, 1, 14, 15)
  - ARM ADC key ladders: /machine/adc vals "<ch>=<10-bit value>" (ch 4, 5, 6)
  - main dial: /machine/mtu2 dial <counts> (MTU2 ch1 phase counting)
  - encoders on port 8: /machine/gpio pins "8_a=..,8_b=.." stepped through Gray code;
    the ARM samples them from its ADC tick (0x2008E2F0)
  - POWER: /machine/gpio pins 3_9 (active low)
  - PTT: sub-CPU PC15, active low (/machine/subcpu pins "C15=0"; "C15=-1" releases it to its
    pull-up). The sub-CPU reports it as key-word bit 0x8000; the ARM then sets its transmit
    flag (0x203806F4 bit 6). Mic-jack PTT is assumed to share this line [INF].
Buttons are held while the mouse button is held. Knobs: mouse wheel over the knob, or the
arrow buttons (shift-click = 10 steps).
"""
import queue, threading, time, tkinter as tk

SUB_LEVEL = {1: 0x0C0, 2: 0x400, 3: 0x800, 4: 0xC00}
SUB_OPEN = 0xFFF
ARM_OPEN = 0x3FF
# (label, kind, channel, level/value). Names in brackets are not confirmed.
SUB_KEYS = [
    ('MENU', 1, 1), ('FUNCTION', 14, 1), ('M.SCOPE', 14, 2), ('QUICK', 14, 3), ('EXIT', 14, 4),
    ('MULTI push', 0, 2), ('AF/RF push', 1, 2), ('SPEECH/LOCK', 15, 1),
    ('UP', 15, 3), ('DOWN', 15, 4), ('[filter] ch0L1', 0, 1), ('[RIT] ch0L3', 0, 3),
    ('ch1 L3', 1, 3), ('ch1 L4', 1, 4), ('ch15 L2', 15, 2),
]
ARM_KEYS = [  # 10-bit values inside the windows of 0x2008DBF0 (ch6) / 0x2008DE40 (ch4, ch5)
    ('[V/M] ch6 L1', 6, 90), ('ch6 L0', 6, 200), ('ch6 L2', 6, 355), ('ch6 L3', 6, 550),
    ('ch4 L1', 4, 460), ('ch4 L2', 4, 620), ('ch4 L3', 4, 740), ('ch4 L4', 4, 845),
    ('ch5 L1', 5, 460), ('ch5 L2', 5, 620), ('ch5 L3', 5, 740), ('ch5 L4', 5, 845),
]
GRAY = [(0, 0), (1, 0), (1, 1), (0, 1)]
# (label, pin a, pin b, active-low, Gray transitions per step)
ENCODERS = [
    ('MULTI', 10, 11, True, 4),       # 0x2008E13C: one count per full cycle; 1 kHz steps seen
    ('AF / RF-SQL', 8, 9, False, 2),  # 0x2008E200: one count per two transitions; AF GAIN
    ('PBT', 0, 1, False, 2),          # 0x2008E200; opens the BW/SFT (twin PBT) window
]
ENC_IDLE = '8_0=0,8_1=0,8_8=0,8_9=0,8_10=1,8_11=1'


class Panel:
    def __init__(self, q, on_quit=None):
        self.q, self.jobs = q, queue.Queue()
        self.phase = {e[0]: 0 for e in ENCODERS}
        threading.Thread(target=self._worker, daemon=True).start()
        self.do(lambda: self.pins(ENC_IDLE))
        self.root = tk.Tk()
        self.root.title('IC-705 front panel')
        self.root.protocol('WM_DELETE_WINDOW', on_quit or self.root.destroy)
        self._build()

    # --- QMP, serialised in one worker thread
    def do(self, fn):
        self.jobs.put(fn)

    def _worker(self):
        while True:
            fn = self.jobs.get()
            try:
                fn()
            except Exception as e:      # a dead QEMU must not kill the panel
                print('panel:', e)

    def pins(self, v):
        self.q.cmd('qom-set', path='/machine/gpio', property='pins', value=v)

    def sub(self, ch, v):
        self.q.cmd('qom-set', path='/machine/subcpu', property='adc', value=f'{ch}={v:#x}')

    def ptt(self, on):
        self.q.cmd('qom-set', path='/machine/subcpu', property='pins', value='C15=0' if on else 'C15=-1')

    def arm(self, ch, v):
        self.q.cmd('qom-set', path='/machine/adc', property='vals', value=f'{ch}={v}')

    def dial(self, n):
        self.do(lambda: self.q.cmd('qom-set', path='/machine/mtu2', property='dial', value=n))

    def enc(self, name, steps):
        _, a, b, low, per = next(e for e in ENCODERS if e[0] == name)

        def run():
            d = 1 if steps > 0 else -1
            for _ in range(abs(steps) * per):
                self.phase[name] = (self.phase[name] + d) % 4
                x, y = GRAY[self.phase[name]]
                if low:
                    x, y = 1 - x, 1 - y
                self.pins(f'8_{a}={x},8_{b}={y}')
                time.sleep(0.03)        # slow enough to stay below the speed-up threshold
        self.do(run)

    # --- widgets
    def _hold_button(self, parent, text, press, release, **kw):
        b = tk.Button(parent, text=text, width=13, **kw)
        b.bind('<ButtonPress-1>', lambda e: self.do(press))
        b.bind('<ButtonRelease-1>', lambda e: self.do(release))
        return b

    def _knob(self, parent, name, turn):
        f = tk.LabelFrame(parent, text=name, padx=4, pady=4)
        c = tk.Canvas(f, width=64, height=64, highlightthickness=0)
        c.create_oval(4, 4, 60, 60, fill='#444', outline='#999', width=2)
        mark = c.create_line(32, 32, 32, 8, fill='white', width=3)
        c.pack()
        ang = [0.0]

        def step(n):
            import math
            ang[0] += n * 0.26
            c.coords(mark, 32, 32, 32 + 24 * math.sin(ang[0]), 32 - 24 * math.cos(ang[0]))
            turn(n)
        c.bind('<MouseWheel>', lambda e: step(1 if e.delta > 0 else -1))
        c.bind('<Button-4>', lambda e: step(1))
        c.bind('<Button-5>', lambda e: step(-1))
        row = tk.Frame(f)
        row.pack()
        for t, n in (('◀', -1), ('▶', 1)):
            btn = tk.Button(row, text=t, width=2)
            btn.bind('<ButtonRelease-1>', lambda e, n=n: step(n * (10 if e.state & 1 else 1)))
            btn.pack(side='left')
        return f

    def _build(self):
        knobs = tk.Frame(self.root)
        knobs.pack(padx=6, pady=6)
        self._knob(knobs, 'Main dial', self.dial).pack(side='left', padx=4)
        for e in ENCODERS:
            self._knob(knobs, e[0], lambda n, name=e[0]: self.enc(name, n)).pack(side='left', padx=4)

        keys = tk.LabelFrame(self.root, text='Keys (sub-CPU ladders)', padx=4, pady=4)
        keys.pack(fill='x', padx=6)
        pw = self._hold_button(keys, 'POWER', lambda: self.pins('3_9=0'), lambda: self.pins('3_9=1'),
                               bg='#a33', fg='white')
        pw.grid(row=0, column=0, padx=2, pady=2)

        tx = tk.LabelFrame(self.root, text='PTT (sub-CPU PC15; body/mic PTT)', padx=4, pady=4)
        tx.pack(fill='x', padx=6, pady=(6, 0), before=keys)
        self._hold_button(tx, 'PTT (hold)', lambda: self.ptt(True), lambda: self.ptt(False),
                          bg='#c60', fg='white').pack(side='left', padx=2)
        latch = tk.BooleanVar(value=False)
        tk.Checkbutton(tx, text='PTT latch', variable=latch,
                       command=lambda: self.do(lambda: self.ptt(latch.get()))).pack(side='left', padx=8)
        for i, (label, ch, lvl) in enumerate(SUB_KEYS, 1):
            self._hold_button(keys, label, lambda ch=ch, lvl=lvl: self.sub(ch, SUB_LEVEL[lvl]),
                              lambda ch=ch: self.sub(ch, SUB_OPEN)).grid(
                row=i // 4, column=i % 4, padx=2, pady=2)

        arm = tk.LabelFrame(self.root, text='ARM ADC ladders (mic/accessory side, mostly unidentified)',
                            padx=4, pady=4)
        arm.pack(fill='x', padx=6, pady=6)
        for i, (label, ch, v) in enumerate(ARM_KEYS):
            self._hold_button(arm, label, lambda ch=ch, v=v: self.arm(ch, v),
                              lambda ch=ch: self.arm(ch, ARM_OPEN)).grid(
                row=i // 4, column=i % 4, padx=2, pady=2)
        tk.Label(self.root, text='[brackets] = inferred name. Hold = held key. '
                 'Wheel over a knob, or ◀ ▶ (shift = ×10).', fg='#666').pack(pady=(0, 6))

    def mainloop(self):
        self.root.mainloop()
