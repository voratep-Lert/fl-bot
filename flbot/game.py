"""Enter the game server with a ticket and collect the initial state dump (stats, inventory).

Layouts come from FAPProxyPy/src/flhandlers.py and real login captures.  Protocol-1 packets are parsed fully;
from protocol-4 chains only props (14) and the character record (5) are read, which is where MaxHP lives.
Max MP comes from the 0x000A score block.
"""

import asyncio
from dataclasses import dataclass, field

from .config import Config
from .crypto import PacketReader, PacketWriter
from .net import FLConnection, FLError

OP_CHAR_ENTER = 0x800C
OP_MESSAGE = 1
OP_CHAR_SHOW = 5
OP_CHAR_SCORE = 10
OP_INV_ADD = 11
OP_PROP_LONG = 14
OP_PROP_SHORT = 15

PROP_NAMES = {2: 'level', 3: 'max_hp', 4: 'max_mp', 5: 'hp', 6: 'mp'}
IDLE_TIMEOUT = 2.0
TOTAL_TIMEOUT = 15.0


@dataclass
class Item:
    slot: int
    count: int
    name: bytes
    item_id: int
    path: bytes


@dataclass
class GameState:
    char: bytes
    props: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    score: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    packets: int = 0

    def prop(self, name):
        for pid, n in PROP_NAMES.items():
            if n == name:
                return self.props.get(pid)
        return None


def apply_chain(state, pr):
    """Protocol-4 packet: several sub-packets back to back, ended by opcode 0."""
    op = pr.opcode
    for _ in range(1000):
        if op == OP_PROP_LONG:
            pid = pr.s16()
            state.props[pid] = pr.u32()
        elif op == OP_CHAR_SHOW:
            pr.string()
            typ = pr.s16()
            pr.s16()
            pr.s32()
            pr.s16()
            pr.s16()
            level = pr.s16()
            hp = pr.s32()
            max_hp = pr.s32()
            pr.s32()
            pr.u8()
            pr.s32()
            pr.s32()
            pr.string()
            pr.string()
            pr.string()
            pr.string()
            for _ in range(8):
                pr.s32()
            pr.s16()
            # odd typ marks the player's own record; fields after level are CurHP, MaxHP (captures: 345/345, 312/345)
            if typ > 0 and typ % 2 == 1 and 3 not in state.props and 0 < hp <= max_hp:
                state.props[2] = level
                state.props[3] = max_hp
        else:
            return
        op = pr.u16()
        if op == 0:
            return


def apply_packet(state, raw, encoding):
    pr = PacketReader(raw)
    if pr.protocol == 4:
        apply_chain(state, pr)
        return
    if pr.protocol != 1:
        return
    op = pr.opcode
    if op == OP_PROP_LONG:
        pid = pr.s16()
        state.props[pid] = pr.u32()
    elif op == OP_PROP_SHORT:
        pid = pr.s16()
        state.props[pid] = pr.s16()
    elif op == OP_INV_ADD:
        path = pr.string()
        slot = pr.u16()
        count = pr.u16()
        name = pr.string()
        state.items.append(Item(slot, count, name, pr.s32(), path))
    elif op == OP_CHAR_SCORE:
        for _ in range(6):
            pr.u8()
        state.score = [pr.s32() for _ in range(26)]
        # score[14] = CurMP, score[15] = MaxMP (two captures, MP 532 and 148 with [15] fixed at 532)
        if state.score[15] > 0 and state.score[15] >= state.score[14]:
            state.props.setdefault(4, state.score[15])
    elif op == OP_MESSAGE:
        state.messages.append(pr.string().decode(encoding, 'replace'))


async def fetch_state(cfg: Config, tables, ticket, log, encoding='cp874'):
    r = ticket.redirect
    conn = FLConnection(tables, 'game')
    await conn.open(r.host, r.port, cfg.connect_timeout)
    state = GameState(ticket.char)
    try:
        await conn.send(PacketWriter(1, OP_CHAR_ENTER).asciiz(ticket.char).raw(r.hash).u8(0).to_bytes())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TOTAL_TIMEOUT
        while loop.time() < deadline:
            try:
                raw = await conn.recv(IDLE_TIMEOUT if state.packets else cfg.reply_timeout)
            except FLError as e:
                if not state.packets:
                    raise
                log(f'[game] stream ended: {e}')
                break
            state.packets += 1
            apply_packet(state, raw, encoding)
    finally:
        await conn.close()
    return state


def format_state(state, encoding='cp874'):
    def dec(b):
        return b.decode(encoding, 'replace')

    def pair(cur, mx):
        return f'{"?" if cur is None else cur}/{"?" if mx is None else mx}'

    lines = [f'Character: {dec(state.char)}  ({state.packets} packets received)',
             f'HP {pair(state.prop("hp"), state.prop("max_hp"))}   MP {pair(state.prop("mp"), state.prop("max_mp"))}']
    other = {pid: v for pid, v in state.props.items() if pid not in PROP_NAMES}
    if other:
        lines.append('Other props: ' + ', '.join(f'#{pid}={v}' for pid, v in sorted(other.items())))
    if state.score:
        lines.append('Score block (unlabelled): ' + ' '.join(map(str, state.score)))
    equip = [i for i in state.items if i.slot < 100]
    bag = [i for i in state.items if i.slot >= 100]
    lines.append(f'Equipment ({len(equip)}):')
    lines += [f'  [{i.slot:>3}] {dec(i.name)}  id={i.item_id}' for i in equip]
    lines.append(f'Inventory ({len(bag)}):')
    lines += [f'  [{i.slot:>3}] {dec(i.name)} x{i.count}  id={i.item_id}' for i in bag]
    return '\n'.join(lines)
