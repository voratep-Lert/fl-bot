"""Fairyland stream cipher + packet helpers (copied from FAPProxyPy/src/flcrypto.py, trimmed).

Cipher:
  * Two tables x 10 blocks x 256 bytes are read from Fairyland.exe: find the magic
    "MapID\\0\\0\\0basic_string..." after offset 2 MB, enc table = magic+28, dec table = magic+28+2560.
  * Per direction: first stream byte = seed -> type = seed % 10, key = seed; the byte after the seed is junk.
  * decrypt: s = dec[type][c]; out = op(key, s); key = s
  * encrypt: t = op'(key, p); key = t; out = enc[type][t]
"""

import random
import struct

MAGIC = bytes.fromhex('4d6170494400000062617369635f737472696e670000000000000000')
SCAN_FROM = 2097152
TABLE_SIZE = 2560


class CryptoError(Exception):
    pass


def load_tables(exe_path):
    """Return (enc_blocks, dec_blocks): two lists of ten 256-byte tables read from Fairyland.exe."""
    try:
        with open(exe_path, 'rb') as f:
            data = f.read()
    except OSError as e:
        raise CryptoError(f"Unable to open '{exe_path}' for reading. ({e})")
    if len(data) < 2100000:
        raise CryptoError('Fairyland.exe is smaller than the minimum crypto table size (might be packed).')
    idx = data.find(MAGIC, SCAN_FROM)
    if idx - SCAN_FROM <= 0:
        raise CryptoError('Crypto substitution tables not found in the executable (magic string missing).')
    loc = idx + len(MAGIC)
    enc = data[loc:loc + TABLE_SIZE]
    dec = data[loc + TABLE_SIZE:loc + 2 * TABLE_SIZE]
    enc_blocks = [enc[i * 256:(i + 1) * 256] for i in range(10)]
    dec_blocks = [dec[i * 256:(i + 1) * 256] for i in range(10)]
    for b in enc_blocks + dec_blocks:
        if len(set(b)) != 256:
            raise CryptoError('Crypto tables are not valid permutations (self test failed).')
    return enc_blocks, dec_blocks


def _build_op_tables():
    dec_op = bytearray(65536)
    enc_op = bytearray(65536)
    for k in range(256):
        if k & 1 << k % 8:
            mode = 0
        elif k & 1 << k % 4:
            mode = 1
        else:
            mode = 2
        base = k << 8
        for v in range(256):
            if mode == 0:
                dec_op[base | v] = v ^ k
                enc_op[base | v] = v ^ k
            elif mode == 1:
                dec_op[base | v] = v - k & 255
                enc_op[base | v] = v + k & 255
            else:
                dec_op[base | v] = v + k & 255
                enc_op[base | v] = v - k & 255
    return bytes(dec_op), bytes(enc_op)


DEC_OP, ENC_OP = _build_op_tables()


class StreamCipher:
    """State for one connection (both directions)."""

    __slots__ = ('_enc_blocks', '_dec_blocks', '_dec_set', '_dec_type', '_dec_key', '_dec_drop',
                 '_enc_set', '_enc_type', '_enc_key')

    def __init__(self, enc_blocks, dec_blocks):
        self._enc_blocks = enc_blocks
        self._dec_blocks = dec_blocks
        self._dec_set = False
        self._dec_type = 0
        self._dec_key = 0
        self._dec_drop = 0
        self._enc_set = False
        self._enc_type = 0
        self._enc_key = 0

    def decrypt(self, data):
        if not data:
            return b''
        if not self._dec_set:
            seed = data[0]
            self._dec_key = seed
            self._dec_type = seed % 10
            self._dec_set = True
            self._dec_drop = 1
            data = data[1:]
            if not data:
                return b''
        s = data.translate(self._dec_blocks[self._dec_type])
        op = DEC_OP
        prev = self._dec_key
        out = bytearray(len(s))
        for i, v in enumerate(s):
            out[i] = op[prev << 8 | v]
            prev = v
        self._dec_key = prev
        if self._dec_drop:
            self._dec_drop = 0
            del out[0]
        return bytes(out)

    def encrypt(self, plain):
        prefix = b''
        if not self._enc_set:
            seed = random.randrange(0, 256)
            self._enc_key = seed
            self._enc_type = seed % 10
            self._enc_set = True
            prefix = bytes((seed,))
            plain = bytes((random.randrange(0, 255),)) + plain
        op = ENC_OP
        k = self._enc_key
        t = bytearray(len(plain))
        for i, v in enumerate(plain):
            k = op[k << 8 | v]
            t[i] = k
        self._enc_key = k
        return prefix + bytes(t).translate(self._enc_blocks[self._enc_type])


def packet_total_len(buf, off=0):
    proto = buf[off] << 8 | buf[off + 1]
    if proto == 1:
        return struct.unpack_from('>I', buf, off + 4)[0] + 8
    return struct.unpack_from('>I', buf, off + 2)[0] + 6


class PacketReader:
    """Big-endian reader; reading past the end returns 0 and does not advance."""

    __slots__ = ('buf', 'length', 'index')

    def __init__(self, buf):
        self.buf = buf
        self.length = len(buf)
        self.index = 8

    @property
    def protocol(self):
        if self.length >= 2:
            return self.buf[0] << 8 | self.buf[1]
        return 0

    @property
    def opcode(self):
        off = 2 if self.protocol == 1 else 6
        if self.length >= off + 2:
            return struct.unpack_from('>H', self.buf, off)[0]
        return 0

    def _fmt(self, fmt, n):
        if self.index + n > self.length:
            return 0
        v = struct.unpack_from(fmt, self.buf, self.index)[0]
        self.index += n
        return v

    def u8(self):
        return self._fmt('>B', 1)

    def u16(self):
        return self._fmt('>H', 2)

    def u32(self):
        return self._fmt('>I', 4)

    def s16(self):
        return self._fmt('>h', 2)

    def s32(self):
        return self._fmt('>i', 4)

    def string(self):
        """Read up to the next NUL (or end of buffer); returns raw bytes."""
        end = self.buf.find(b'\x00', self.index)
        if end < 0:
            s = self.buf[self.index:]
            self.index = self.length
        else:
            s = self.buf[self.index:end]
            self.index = end + 1
        return bytes(s)

    def bytes_(self, n):
        end = min(self.index + n, self.length)
        out = bytes(self.buf[self.index:end]) + b'\x00' * max(0, n - (end - self.index))
        self.index = self.index + n
        return out


class PacketWriter:
    def __init__(self, proto, opcode):
        self.proto = proto
        if proto == 1:
            self.b = bytearray(struct.pack('>HHI', proto, opcode, 0))
            return
        self.b = bytearray(struct.pack('>HIH', proto, 0, opcode))

    def u8(self, v):
        self.b.append(v & 255)
        return self

    def u16(self, v):
        self.b += struct.pack('>H', v & 65535)
        return self

    def u32(self, v):
        self.b += struct.pack('>I', v & 4294967295)
        return self

    def raw(self, data):
        self.b += data
        return self

    def asciiz(self, s):
        if isinstance(s, str):
            s = s.encode('latin-1', 'replace')
        self.b += (s or b'') + b'\x00'
        return self

    def to_bytes(self):
        n = len(self.b)
        if self.proto == 1:
            struct.pack_into('>I', self.b, 4, n - 8)
        else:
            struct.pack_into('>I', self.b, 2, n - 6)
        return bytes(self.b)
