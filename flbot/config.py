"""Loads config.json (server hosts, Fairyland.exe path, client version, timeouts)."""

import json
import os
import sys
from dataclasses import dataclass

FROZEN = bool(getattr(sys, 'frozen', False))
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')


class ConfigError(Exception):
    pass


@dataclass
class ServerEntry:
    name: str
    host: str
    login_ports: list


@dataclass
class ClientVersion:
    unk1: int
    unk2: int
    ver: int


@dataclass
class Config:
    exe_path: str
    servers: list
    default_server: str
    client_version: ClientVersion
    connect_timeout: float
    reply_timeout: float

    def find_server(self, name):
        for s in self.servers:
            if s.name == name:
                return s
        return None


def _resolve_exe(path):
    path = os.path.expanduser(path or 'Fairyland.exe')
    if not os.path.isabs(path):
        path = os.path.join(APP_DIR, path)
    if not os.path.exists(path):
        local = os.path.join(APP_DIR, 'Fairyland.exe')
        if os.path.exists(local):
            return local
    return path


def load_config(path=CONFIG_PATH):
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except OSError as e:
        raise ConfigError(f'Cannot read config file {path}: {e}')
    except json.JSONDecodeError as e:
        raise ConfigError(f'Invalid JSON in {path}: {e}')

    servers = []
    for i, s in enumerate(raw.get('servers') or []):
        try:
            ports = [int(p) for p in s['login_ports']]
            if not ports:
                raise ValueError('login_ports is empty')
            servers.append(ServerEntry(str(s['name']), str(s['host']), ports))
        except (KeyError, TypeError, ValueError) as e:
            raise ConfigError(f'servers[{i}] is invalid: {e!r}')
    if not servers:
        raise ConfigError('config.json must define at least one entry in "servers".')

    cv = raw.get('client_version') or {}
    try:
        version = ClientVersion(int(cv.get('unk1', 0)), int(cv.get('unk2', 0)), int(cv.get('ver', 0)))
    except (TypeError, ValueError) as e:
        raise ConfigError(f'client_version is invalid: {e!r}')

    default = raw.get('default_server') or servers[0].name
    if default not in [s.name for s in servers]:
        default = servers[0].name

    return Config(
        exe_path=_resolve_exe(raw.get('exe_path')),
        servers=servers,
        default_server=default,
        client_version=version,
        connect_timeout=float(raw.get('connect_timeout', 20)),
        reply_timeout=float(raw.get('reply_timeout', 20)),
    )
