"""FLbot command-line entry point (no tkinter).  Usage: python3 cli.py [username]"""

import asyncio
import getpass
import sys

from flbot.config import ConfigError, load_config
from flbot.game import format_state
from flbot.login import ENCODING, LoginSession


async def run(cfg, user):
    session = LoginSession(cfg, print)
    try:
        print('Servers:')
        for i, s in enumerate(cfg.servers, 1):
            print(f'  {i}. {s.name}')
        choice = input(f'Server number [{cfg.default_server}]: ').strip()
        server = cfg.servers[int(choice) - 1] if choice else cfg.find_server(cfg.default_server)

        user = user or input('Username: ').strip()
        pw = getpass.getpass('Password: ')
        channels = await session.login(server, user, pw)

        print('Channels:')
        for i, ch in enumerate(channels, 1):
            print(f'  {i}. {ch.name} (population: {ch.population})')
        chan_choice = input(f'Channel number [1]: ').strip()
        channel = channels[int(chan_choice) - 1] if chan_choice else channels[0]
        
        names = await session.select_channel(channel.id)

        chars = [n.decode(ENCODING, 'replace') for n in names]
        if chars:
            print('Characters (guessed from server reply):')
            for i, c in enumerate(chars, 1):
                print(f'  {i}. {c}')
        text = input('Character number or name: ').strip()
        if text.isdigit() and 1 <= int(text) <= len(chars):
            name = names[int(text) - 1]
        else:
            name = text.encode(ENCODING, 'replace')

        ticket = await session.select_character(name)
        r = ticket.redirect
        print(f'Character selected. Game server: {r.host}:{r.port} (hash {len(r.hash)} bytes)')
        print('Entering game (this logs the character in; a running client will be kicked)...')
        print(format_state(await session.enter_game(ticket), ENCODING))
    finally:
        await session.close()


def main():
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(f'FLbot: {e}')
    try:
        asyncio.run(run(cfg, sys.argv[1] if len(sys.argv) > 1 else ''))
    except (KeyboardInterrupt, EOFError):
        print()
    except Exception as e:
        sys.exit(f'Error: {e}')


if __name__ == '__main__':
    main()
