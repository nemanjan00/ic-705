#!/usr/bin/env python3
"""Run the IC-705 main-DSP firmware (sharc_sim.py) as the device on QEMU's RSPI3 (the ARM's main-DSP link).

  dsp_bridge.py [--sock PATH] [--image dsp.image.bin] [--log FILE]

QEMU is started with RZA1H_RSPI3_SOCK=PATH (rspi_gen.c): every byte the ARM clocks on RSPI3
is sent here, given to the DSP's SPIB slave (SpibLink), and the DSP's output byte is sent
back. The DSP runs freely between bytes with its peripheral models (SPI, SPIB, TWI, DPI,
SPORT DMA); while it idles with nothing pending, time jumps to the next SPORT block.
Every reply carries the DSP's FLAG2 level, which rspi_gen.c applies to the ARM input named
by RZA1H_RSPI3_SOCK_PIN (7_6): the DSP raises FLAG2 while it has a status frame queued, and
the ARM reads RSPI3 only while P7_6 is high (FW 0x20118E68). (--qmp/--pin drive the same pin
over QMP instead; not needed with the socket pin.)
The log shows commands as the DSP's command handler sees them (4 bytes, big-endian, in
receive mode) and every byte it transmits.
"""
import argparse, collections, os, socket, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sharc_sim as S
import qemu705 as Q

ap = argparse.ArgumentParser()
ap.add_argument('--sock', default='/tmp/ic705_dsp.sock')
ap.add_argument('--image', default=os.path.expanduser('~/icom/out/705E140_full/dsp.image.bin'))
ap.add_argument('--log', default=None)
ap.add_argument('--qmp', default=None, help='QEMU QMP socket for the handshake line')
ap.add_argument('--pin', default=None, help='ARM port_bit driven by FLAG2, e.g. 3_8')
ap.add_argument('--rec', default=None, help='record every RSPI byte as "step in out" lines')
ap.add_argument('--word', type=int, default=4, help='bytes per chip-select frame')
ap.add_argument('--chunk', type=int, default=50, help='instructions per lock hold')
ap.add_argument('--period', type=int, default=4000, help='instructions per SPORT block')
a = ap.parse_args()
log = open(a.log, 'w', buffering=1) if a.log else sys.stdout

sim = S.Sharc()
S.load_image(sim, open(a.image, 'rb').read())
sim.goto(0x8C004)
sim.hle.update(S.HLE)
image = open(a.image, 'rb').read()
sim.iop[0x1000] = S.Spi(0x1000, S.SpiNor(sim, image))
spib = S.SpibLink()
sim.iop[0x2800] = spib
sim.iop[0x4400] = S.Twi()
sim.iop[0x1C32] = S.Dpi()
sports = S.Sports(period=a.period)
sim.tickers.append(sports)
lock = threading.Lock()
rec = open(a.rec, 'w', buffering=1) if a.rec else None
sim.trace = collections.deque(maxlen=200)
sys.setswitchinterval(1e-5)
t0 = time.time()


def say(msg):
    log.write(f'{time.time() - t0:9.3f} {msg}\n')


polls = [0]
sync = [0]
syncs = [0]
unframed = [0]          # a command word was delivered: let the DSP catch up
pos = [0]
word = [[]]
held = []


def rx_mode():
    r = spib.r
    return bool(r.get(0, 0) >> 14 & 1 and r.get(0x84, 0) & 3 == 3)


def log_cmd(w):
    sync[0] = 1 if w[0] == 0x80 else 2        # SPORT blocks the DSP gets before the ARM resumes
    if w[0] == 0x80:
        polls[0] += 1
    else:
        say('cmd  %02x%02x%02x%02x' % tuple(w))


def run_dsp(n):
    global steps
    for _ in range(n):
        try:
            sim.step()
        except S.Halt:
            sports.n = sports.period - 1          # idle: skip to the next block
            for t in sim.tickers:
                t.tick(sim)
        steps += 1


def serve():
    if os.path.exists(a.sock):
        os.unlink(a.sock)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(a.sock)
    srv.listen(1)
    while True:
        c, _ = srv.accept()
        say('QEMU connected')
        rx, tx = [], []
        while True:
            d = c.recv(2, socket.MSG_WAITALL)
            if len(d) < 2:
                say('QEMU disconnected')
                break
            # The ARM clocks 4-byte words under one chip select (FW 0x20118E00). The DSP must
            # not change SPIB direction inside a word, so the simulator is paused from a
            # word's first byte to its last. Timing compensation (the simulator is ~1000x
            # slower than the DSP, so it stays in transmit mode far longer than the real
            # one): a command word that arrives while the DSP transmits is held and delivered
            # once it receives again, and an all-FF read-clock word that arrives while it
            # receives (P7_6 still high from the slower handshake) is not delivered.
            if d[0] & 1 and pos[0] != 0:              # new chip-select frame: drop a partial word
                say(f'partial word dropped: {word[0]}')
                pos[0] = 0
                del word[1:]
                lock.release()
            if pos[0] == 0 and not d[0] & 1:
                unframed[0] += 1
            d = d[1:]
            if pos[0] == 0:
                lock.acquire()
                word[0] = []
                word.append(rx_mode())
            word[0].append(d[0])
            receiving = word[-1]
            if receiving:
                out = 0xFF                             # SPIB receive DMA drives no data
            else:
                out = spib.xfer(sim, d[0])            # transmit DMA: the reply byte
            if rec:
                rec.write(f'{steps} {d[0]:02x} {out:02x}\n')
            pos[0] = (pos[0] + 1) % 4
            if pos[0] == 0:
                w = word[0]
                del word[1:]
                if w != [0xFF] * 4:
                    if receiving:
                        for b in w:
                            spib.xfer(sim, b)
                        log_cmd(w)
                    else:
                        held.append(w)
                elif not receiving and 0x28 - spib.rd(sim, 0x2882) >= 4 * sim.rd(0xB2C9A) + 9:
                    sync[0] = 1    # frame fully read (FW 0x125C5B): let the DSP drop FLAG2
                lock.release()
                if sync[0]:
                    n_blocks = sync[0]
                    # Time sync: QEMU's vCPU waits for this reply byte, so holding it back
                    # freezes the ARM (and its virtual clock) until the DSP main loop has
                    # run -- it handles received words once per SPORT block -- and a command
                    # is answered as quickly as on the real radio (2 blocks for commands, 1
                    # for the 0x80 status polls, which only need FLAG2 raised).
                    sync[0] = False
                    # (this thread runs the DSP itself: the main thread may be blocked in
                    # a QMP call that QEMU cannot serve while its vCPU waits here)
                    target = sports.blocks.get((4, 'A'), 0) + n_blocks
                    with lock:
                        while sports.blocks.get((4, 'A'), 0) < target:
                            run_dsp(500)
                    syncs[0] += 1
            mode = 't' if not receiving else '-'
            c.sendall(bytes([out, 0x80 | (sim.sreg['FLAGS'] >> 4 & 1)]))   # data, FLAG2 -> P7_6
            if mode == 't':
                tx.append(out)
            elif tx:
                say('sent ' + ' '.join(f'{x:02x}' for x in tx))
                tx = []


qmp = None
flag2 = None


def connect_qmp():
    global qmp
    for _ in range(600):
        if os.path.exists(a.qmp):
            try:
                qmp = Q.QMP(a.qmp)
                say('QMP connected')
                return
            except OSError:
                pass
        time.sleep(0.1)


threading.Thread(target=serve, daemon=True).start()
if a.qmp:
    threading.Thread(target=connect_qmp, daemon=True).start()
say(f'listening on {a.sock}')
steps = 0
next_status = [5000000]
while True:
    with lock:
        for _ in range(a.chunk):
            try:
                sim.step()
            except NotImplementedError as e:
                say(f'CRASH at step {steps}: {e!r}')
                for pc, vs in list(sim.trace)[-60:]:
                    w = sim.p_get(pc) << 32 | sim.p_get(pc + 1) << 16 | sim.p_get(pc + 2) if vs else sim.rd48(pc)
                    say(f'  {pc:06x} ' + (S.D.decode_visa(w, pc)[2] if vs else S.D.text(w, pc)))
                os._exit(1)
            except S.Halt:
                sports.n = sports.period - 1          # idle: skip to the next block
                for t in sim.tickers:
                    t.tick(sim)
            steps += 1
    if held and pos[0] == 0:
        with lock:
            if rx_mode():
                for w in held:
                    for b in w:
                        spib.xfer(sim, b)
                    log_cmd(w)
                    say('(held word delivered: %02x%02x%02x%02x)' % tuple(w))
                held.clear()
    f2 = sim.sreg['FLAGS'] >> 4 & 1
    if f2 != flag2:
        flag2 = f2
        if qmp and a.pin:
            try:
                qmp.cmd('qom-set', path='/machine/gpio', property='pins', value=f'{a.pin}={f2}')
            except OSError:
                say('QMP gone'); os._exit(0)
        say(f'FLAG2 = {f2}')
    if steps >= next_status[0]:
        next_status[0] += 5000000
        say(f'{steps} instructions, pc {sim.pc:#x}, SPORT blocks {sports.blocks.get((4, "A"), 0)}, 0x80 polls {polls[0]}, ver ' + bytes(sim.rd(0xb2c90 + i) & 0x7F for i in range(8)).decode(errors='replace') + f' syncs {syncs[0]}, unframed words {unframed[0]}')
