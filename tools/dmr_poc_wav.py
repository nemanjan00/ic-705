#!/usr/bin/env python3
"""End-to-end [VM] check: DMR dibits -> patched DV-DSP modulator (emulated) -> WAV.

Builds a continuous DMR stream in base-station layout (24-bit CACH + 264-bit burst
per 30 ms slot), voice superframes A..F on both slots, with the BS voice sync in
burst A and random payload elsewhere.  The dibits are fed one per call to the
patched gmsk_mod_bit running in the C55x micro-emulator; the 48 kHz output is the
modulation signal the DV DSP would hand to the next stage, i.e. what an ideal FM
discriminator would recover.  Decode the WAV with dsd-neo to confirm it is DMR.
"""
import random, struct, sys, wave
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import dv_mod_poc as p

BS_VOICE_SYNC = 0x755FD7DF75F7

def bits_to_dibits(bits):
    return [(bits[i] << 1) | bits[i + 1] for i in range(0, len(bits), 2)]

def burst(center48):
    pay = [random.getrandbits(1) for _ in range(216)]
    return pay[:108] + center48 + pay[108:]

def stream(n_superframes=12, seed=3):
    random.seed(seed)
    sync = [(BS_VOICE_SYNC >> (47 - i)) & 1 for i in range(48)]
    bits = []
    for _ in range(n_superframes):
        for b in range(6):                 # bursts A..F
            for slot in range(2):
                cach = [random.getrandbits(1) for _ in range(24)]
                center = sync if b == 0 else [random.getrandbits(1) for _ in range(48)]
                bits += cach + burst(center)
    return bits_to_dibits(bits)

if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else '/tmp/dmr_poc.wav'
    dib = stream()
    y, _ = p.run(f'{p.OUT}/c55_mod4fsk_patched.json', dib, setup=p.patched_setup(0x4800))
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b''.join(struct.pack('<h', v) for v in y))
    print(f'{len(dib)} dibits -> {len(y)} samples ({len(y)/48000:.2f} s) -> {out}')
