"""Login -> character list -> character select, as a client.

Flow (mirrors what FAPProxyPy relays between the game and the server):
  Login server : S 0x7006 (ask version) -> C 0x9005 version + C 0x9000 user/pass -> S 0x7009 channels
                 -> C 0x9006 u16 channel id -> S 0x7003 redirect
  Char server  : C 0x9007 login + hash -> S 0x7002 character list -> C 0x9002 char name -> S 0x7003 redirect
  The last redirect (host#port + hash) is the game-server ticket; entering the game is not done here.
"""

import asyncio
import random
from dataclasses import dataclass

from .config import Config, ServerEntry
from .crypto import PacketReader, PacketWriter, load_tables
from .game import fetch_state
from .net import FLConnection, FLError, opcode_of

OP_CONNECT_FAILED = 0x7000
OP_CHAR_LIST = 0x7002
OP_REDIRECT = 0x7003
OP_VERSION_REQUEST = 0x7006
OP_CHANNEL_LIST = 0x7009
OP_VERSION = 0x9005
OP_LOGIN = 0x9000
OP_CHAR_SELECT = 0x9002
OP_CHANNEL_SELECT = 0x9006
OP_CHAR_LOGIN = 0x9007

ENCODING = 'cp874'


@dataclass
class Redirect:
    host: str
    port: int
    hash: bytes


@dataclass
class Channel:
    id: int
    name: str
    population: int


@dataclass
class GameTicket:
    char: bytes
    redirect: Redirect


def parse_redirect(raw):
    """0x7003 payload: u16 a, u32 n, hash[n], "host#port\\0"."""
    pr = PacketReader(raw)
    pr.u16()
    n = pr.u32()
    if n > 4096 or pr.index + n > pr.length:
        raise FLError(f'bad redirect packet: {raw.hex(" ")}')
    h = pr.bytes_(n)
    dest = pr.string().decode('latin-1').split('#')
    try:
        return Redirect(dest[0], int(dest[1]), h)
    except (IndexError, ValueError):
        raise FLError(f'bad redirect destination: {raw.hex(" ")}')


def parse_channel(raw):
    """0x7009 payload: u16 channel ID, asciiz name, u16 population."""
    pr = PacketReader(raw)
    chan_id = pr.u16()
    name = pr.string().decode(ENCODING, 'replace')
    population = pr.u16()
    return Channel(chan_id, name, population)


def parse_char_names(raw):
    """0x7002 payload: u16, then one record per line, tab-separated fields, name first; empty slots are '0'."""
    names = []
    for rec in raw[10:].split(b'\n'):
        name = rec.split(b'\t')[0].strip(b'\x00')
        if name and name != b'0':
            names.append(name)
    return names


class LoginSession:
    def __init__(self, cfg: Config, log):
        self.cfg = cfg
        self._log = log
        self._tables = None
        self._conn = None
        self._user = b''

    def _load_tables(self):
        if self._tables is None:
            self._tables = load_tables(self.cfg.exe_path)
        return self._tables

    async def close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    async def _wait_for(self, conn, wanted, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise FLError(f'[{conn.label}] timed out waiting for opcode 0x{wanted:04X}')
            raw = await conn.recv(remaining)
            op = opcode_of(raw)
            if op == wanted:
                return raw
            if op == OP_CONNECT_FAILED:
                raise FLError(f'[{conn.label}] server refused the connection: {raw.hex(" ")}')
            self._log(f'[{conn.label}] ignoring 0x{op:04X}: {raw.hex(" ")}')

    async def _collect_channels(self, conn, timeout):
        """Collect all channel list packets (0x7009) until we get a different opcode or timeout."""
        channels = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await conn.recv(remaining)
                op = opcode_of(raw)
                if op == OP_CHANNEL_LIST:
                    try:
                        channel = parse_channel(raw)
                        channels.append(channel)
                        self._log(f'[login] channel {channel.id}: {channel.name} ({channel.population})')
                    except Exception as e:
                        self._log(f'[login] failed to parse channel packet: {e}')
                else:
                    # Not a channel packet, we're done collecting
                    # Put it back for the next _wait_for call
                    return channels, raw
            except asyncio.TimeoutError:
                break
        
        if not channels:
            raise FLError('[login] no channels received')
        return channels, None

    async def _open_login(self, server: ServerEntry):
        ports = list(server.login_ports)
        random.shuffle(ports)
        last = None
        for port in ports:
            conn = FLConnection(self._load_tables(), 'login')
            try:
                await conn.open(server.host, port, self.cfg.connect_timeout)
            except FLError as e:
                last = e
                self._log(str(e))
                continue
            self._log(f'[login] connected to {server.host}:{port}')
            return conn
        raise last

    async def login(self, server: ServerEntry, user: str, password: str):
        """Log in and return list of available channels."""
        await self.close()
        cfg = self.cfg
        self._user = user.encode(ENCODING, 'replace')

        conn = await self._open_login(server)
        self._conn = conn
        await self._wait_for(conn, OP_VERSION_REQUEST, cfg.reply_timeout)
        v = cfg.client_version
        await conn.send(PacketWriter(1, OP_VERSION).u16(v.unk1).u16(v.unk2).u32(v.ver).to_bytes())
        await conn.send(PacketWriter(1, OP_LOGIN).asciiz(self._user)
                        .asciiz(password.encode(ENCODING, 'replace')).to_bytes())
        self._log('[login] credentials sent')
        
        # Collect all channel list packets
        channels, _ = await self._collect_channels(conn, cfg.reply_timeout)
        return channels

    async def select_channel(self, channel_id: int):
        """Select a channel and return character names (bytes)."""
        conn = self._conn
        if conn is None or conn.label != 'login':
            raise FLError('Not logged in to login server.')
        
        cfg = self.cfg
        await conn.send(PacketWriter(1, OP_CHANNEL_SELECT).u16(channel_id).to_bytes())
        self._log(f'[login] channel {channel_id} selected')
        redirect = parse_redirect(await self._wait_for(conn, OP_REDIRECT, cfg.reply_timeout))
        await self.close()
        self._log(f'[login] accepted, char server {redirect.host}:{redirect.port}')

        conn = FLConnection(self._load_tables(), 'char')
        await conn.open(redirect.host, redirect.port, cfg.connect_timeout)
        self._conn = conn
        await conn.send(PacketWriter(1, OP_CHAR_LOGIN).asciiz(self._user)
                        .u32(len(redirect.hash)).raw(redirect.hash).to_bytes())
        raw = await self._wait_for(conn, OP_CHAR_LIST, cfg.reply_timeout)
        self._log(f'[char] character list packet: {raw.hex(" ")}')
        return parse_char_names(raw)

    async def select_character(self, name: bytes):
        """Pick a character on the open char-server connection; returns the game-server ticket."""
        conn = self._conn
        if conn is None or conn.label != 'char':
            raise FLError('Not logged in.')
        await conn.send(PacketWriter(1, OP_CHAR_SELECT).asciiz(name).u8(0).to_bytes())
        redirect = parse_redirect(await self._wait_for(conn, OP_REDIRECT, self.cfg.reply_timeout))
        await self.close()
        return GameTicket(name, redirect)

    async def enter_game(self, ticket: GameTicket):
        """Log the character into the game server and return its initial GameState."""
        return await fetch_state(self.cfg, self._load_tables(), ticket, self._log, ENCODING)
