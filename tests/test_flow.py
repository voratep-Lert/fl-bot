"""Offline flow test: fake login + char servers with synthetic crypto tables (no game, no network)."""

import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flbot.config import ClientVersion, Config, ServerEntry
from flbot.crypto import PacketReader, PacketWriter, StreamCipher
from flbot.login import LoginSession
from flbot.net import split_packets


def make_tables():
    enc, dec = [], []
    for _ in range(10):
        perm = list(range(256))
        random.shuffle(perm)
        inv = [0] * 256
        for i, p in enumerate(perm):
            inv[p] = i
        enc.append(bytes(perm))
        dec.append(bytes(inv))
    return enc, dec


TABLES = make_tables()


async def serve(reader, writer, on_start, on_packet):
    cipher = StreamCipher(*TABLES)
    pending = b''

    async def send(pkt):
        writer.write(cipher.encrypt(pkt))
        await writer.drain()

    await on_start(send)
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            packets, pending = split_packets(pending, cipher.decrypt(data))
            for p in packets:
                await on_packet(p, send)
    except ConnectionError:
        pass
    writer.close()


async def main():
    seen = {}
    char_list = (PacketWriter(1, 0x7002).u16(0).raw(b'Hero\t12\t1\nMage\t5\t2\n0\t0\t0\n\x00').to_bytes())

    async def login_start(send):
        await send(PacketWriter(1, 0x7006).u8(1).to_bytes())

    async def login_pkt(p, send):
        pr = PacketReader(p)
        if pr.opcode == 0x9005:
            seen['ver'] = (pr.u16(), pr.u16(), pr.u32())
        elif pr.opcode == 0x9000:
            seen['login'] = (pr.string(), pr.string())
            await send(PacketWriter(1, 0x7009).u16(1).asciiz(b'Chan 1').u16(27).to_bytes())
            await send(PacketWriter(1, 0x7009).u16(2).asciiz(b'Chan 2').u16(23).to_bytes())
        elif pr.opcode == 0x9006:
            seen['channel'] = pr.u16()
            await send(PacketWriter(1, 0x7003).u16(1).u32(4).raw(b'\xde\xad\xbe\xef')
                       .asciiz(f'127.0.0.1#{char_port}').to_bytes())

    async def char_start(send):
        pass

    async def char_pkt(p, send):
        pr = PacketReader(p)
        if pr.opcode == 0x9007:
            seen['char_login'] = (pr.string(), pr.bytes_(pr.u32()))
            await send(char_list)
        elif pr.opcode == 0x9002:
            seen['select'] = pr.string()
            await send(PacketWriter(1, 0x7003).u16(1).u32(4).raw(b'\x01\x02\x03\x04')
                       .asciiz('game.example#4729').to_bytes())

    login_srv = await asyncio.start_server(lambda r, w: serve(r, w, login_start, login_pkt), '127.0.0.1', 0)
    char_srv = await asyncio.start_server(lambda r, w: serve(r, w, char_start, char_pkt), '127.0.0.1', 0)
    login_port = login_srv.sockets[0].getsockname()[1]
    char_port = char_srv.sockets[0].getsockname()[1]

    cfg = Config('unused.exe', [ServerEntry('test', '127.0.0.1', [login_port])], 'test',
                 ClientVersion(1, 2, 3), 5, 5)
    logs = []
    s = LoginSession(cfg, logs.append)
    s._tables = TABLES

    names = await s.login(cfg.servers[0], 'user1', 'pw1')
    assert seen['ver'] == (1, 2, 3), seen
    assert seen['login'] == (b'user1', b'pw1'), seen
    assert seen['char_login'] == (b'user1', b'\xde\xad\xbe\xef'), seen
    assert seen['channel'] == 1, seen
    assert names == [b'Hero', b'Mage'], names

    ticket = await s.select_character(b'Hero')
    assert seen['select'] == b'Hero'
    assert (ticket.redirect.host, ticket.redirect.port, ticket.redirect.hash) == \
        ('game.example', 4729, b'\x01\x02\x03\x04'), ticket
    print('OK', names)

    login_srv.close()
    char_srv.close()


asyncio.run(main())
