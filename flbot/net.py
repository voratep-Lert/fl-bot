"""Encrypted packet connection to a Fairyland server (login / char / game port)."""

import asyncio
import socket
from collections import deque

from .crypto import PacketReader, StreamCipher, packet_total_len

MAX_PACKET = 4 * 1024 * 1024


class FLError(Exception):
    pass


def opcode_of(raw):
    return PacketReader(raw).opcode


def split_packets(pending, data):
    buf = pending + data if pending else data
    out = []
    off = 0
    n = len(buf)
    while n - off >= 8:
        total = packet_total_len(buf, off)
        if total > MAX_PACKET:
            raise FLError(f'invalid packet length {total}')
        if total > n - off:
            break
        out.append(buf[off:off + total])
        off += total
    return out, buf[off:]


class FLConnection:
    def __init__(self, tables, label):
        self.label = label
        self._tables = tables
        self._cipher = None
        self._reader = None
        self._writer = None
        self._pending = b''
        self._queue = deque()

    async def open(self, host, port, timeout):
        try:
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise FLError(f'[{self.label}] cannot connect to {host}:{port} ({type(e).__name__}: {e})')
        sock = self._writer.get_extra_info('socket')
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
        self._cipher = StreamCipher(*self._tables)

    async def send(self, packet):
        self._writer.write(self._cipher.encrypt(packet))
        try:
            await self._writer.drain()
        except (ConnectionError, OSError) as e:
            raise FLError(f'[{self.label}] send failed: {e}')

    async def recv(self, timeout):
        """Return one complete decrypted packet."""
        while not self._queue:
            try:
                data = await asyncio.wait_for(self._reader.read(65536), timeout)
            except asyncio.TimeoutError:
                raise FLError(f'[{self.label}] timed out waiting for the server')
            except (ConnectionError, OSError) as e:
                raise FLError(f'[{self.label}] receive failed: {e}')
            if not data:
                raise FLError(f'[{self.label}] server closed the connection')
            packets, self._pending = split_packets(self._pending, self._cipher.decrypt(data))
            self._queue.extend(packets)
        return self._queue.popleft()

    async def close(self):
        w, self._writer = self._writer, None
        if w is None:
            return
        w.close()
        try:
            await w.wait_closed()
        except (ConnectionError, OSError):
            pass
