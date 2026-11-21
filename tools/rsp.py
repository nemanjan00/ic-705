#!/usr/bin/env python3
"""Minimal GDB remote-serial-protocol client for QEMU's -gdb stub (ARM)."""
import socket, time

class RSP:
    def __init__(self, host='127.0.0.1', port=1234, timeout=120):
        for _ in range(200):
            try:
                self.s = socket.create_connection((host, port)); break
            except OSError:
                time.sleep(0.05)
        self.s.settimeout(timeout)
        self.buf = b''
    def _send(self, data):
        pkt = b'$' + data + b'#' + b'%02x' % (sum(data) & 0xff)
        self.s.sendall(pkt)
    def _recv(self):
        while True:
            i = self.buf.find(b'$')
            j = self.buf.find(b'#', i)
            if i >= 0 and j >= 0 and len(self.buf) >= j + 3:
                data = self.buf[i + 1:j]; self.buf = self.buf[j + 3:]
                self.s.sendall(b'+')
                return data
            self.buf += self.s.recv(65536)
    def cmd(self, c):
        self._send(c.encode() if isinstance(c, str) else c)
        while True:
            r = self._recv()
            if r != b'':            # skip empty notifications
                return r
    def regs(self):
        g = self.cmd('g')
        vals = [int.from_bytes(bytes.fromhex(g[i:i + 8].decode()), 'little') for i in range(0, 16 * 8, 8)]
        return vals
    def mem(self, addr, n):
        return bytes.fromhex(self.cmd(f'm{addr:x},{n:x}').decode())
    def bp(self, addr, on=True):
        return self.cmd(f"{'Z' if on else 'z'}0,{addr:x},4")
    def cont(self):
        self._send(b'c'); return self._recv()
