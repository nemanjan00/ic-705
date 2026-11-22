#!/usr/bin/env python3
"""Run the IC-705 DV DSP firmware (c55sim.py, TMS320C5517) as the device on QEMU's RSPI0.

  dv_bridge.py [--sock PATH] [--log FILE]

QEMU is started with RZA1H_RSPI0_SOCK=PATH and RZA1H_RSPI0_SOCK_PIN=3_8 (rspi_gen.c). Each
byte the ARM clocks on RSPI0 arrives as (flags, byte) and is clocked into the DV DSP's McSPI
(slave, channel 0); the reply is (DSP output byte, 0x80 | GPIO15). GPIO15 (IODATAOUT1 bit
15) is the DV DSP's "data ready" line, read by the ARM as P3_8 (FW 0x20201F4C).

The ARM's packets are length-framed: [L][type][L-2 data bytes][FF]; 0xFF while idle polls.
Time sync as in dsp_bridge.py: the reply to a byte waits until the DSP's McSPI interrupt has
handled it, and after the last byte of a packet the DSP runs --frame-steps more instructions
(its main loop parses the packet and may queue a reply) before the ARM continues.
"""
import argparse, os, socket, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c55sim as C

ap = argparse.ArgumentParser()
ap.add_argument('--sock', default='/tmp/ic705_dv.sock')
ap.add_argument('--image', default=os.path.expanduser('~/icom/out/705E140_full/dspB.mem'))
ap.add_argument('--insns', default=os.path.expanduser('~/icom/out/705E140_full/dspB_insns.json'))
ap.add_argument('--log', default=None)
ap.add_argument('--frame-steps', type=int, default=20000)
a = ap.parse_args()
log = open(a.log, 'w', buffering=1) if a.log else sys.stdout
t0 = time.time()


def say(msg):
    log.write(f'{time.time() - t0:9.3f} {msg}\n')


img, ins = C.load_dspb(a.image, a.insns)
sim = C.C55Sim(img, ins, 0xA9AC)
C.default_board(sim)
spi = sim.ports[0x3500]
lock = threading.Lock()
sys.setswitchinterval(1e-5)
steps = [0]


def gpio15():
    return sim.io_last.get(0x1C0A, 0) >> 15 & 1


def run(n):
    sim.run(n)
    steps[0] += n


def serve():
    if os.path.exists(a.sock):
        os.unlink(a.sock)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(a.sock)
    srv.listen(1)
    c, _ = srv.accept()
    say('QEMU connected')
    left, pkt, out_bytes = 0, [], []
    while True:
        d = c.recv(2, socket.MSG_WAITALL)
        if len(d) < 2:
            say('QEMU disconnected')
            os._exit(0)
        b = d[1]
        with lock:
            out = spi.xfer(b)
            for _ in range(2000):                    # let the McSPI ISR take the byte
                if spi.rx_taken and spi.tx_loaded:    # (it acks first, reads RX, loads TX)
                    break
                run(10)
            if left == 0 and b != 0xFF:              # first byte of a packet: its length
                left, pkt = b + 1, []
            if left:
                pkt.append(b)
                left -= 1
                if left == 0:
                    say('pkt  ' + ' '.join(f'{x:02x}' for x in pkt))
                    run(a.frame_steps)
            if out != 0xFF or out_bytes:
                out_bytes.append(out)
                if out == 0xFF and len(out_bytes) > 1:
                    say('sent ' + ' '.join(f'{x:02x}' for x in out_bytes))
                    out_bytes = []
            pin = gpio15()
        c.sendall(bytes([out, 0x80 | pin]))


def serve_logged():
    try:
        serve()
    except Exception:
        import traceback
        say('serve thread died:\n' + traceback.format_exc())
        os._exit(1)


threading.Thread(target=serve_logged, daemon=True).start()
say(f'listening on {a.sock}')
last_pin, next_status = None, 5000000
while True:
    with lock:
        try:
            run(500)
        except Exception as e:
            say(f'CRASH: {e!r} at {sim.pc:#x}')
            os._exit(1)
        p = gpio15()
    if p != last_pin:
        last_pin = p
        say(f'GPIO15 = {p}')
    if steps[0] >= next_status:
        next_status += 5000000
        say(f'{steps[0]} steps, pc {sim.pc:#x}')
