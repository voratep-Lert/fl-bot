"""Loads config.json (server hosts, Fairyland.exe path, client version, timeouts).

If config.json has "proxy_config" (path to FAPProxy2.exe.config), its RemoteIP / ports / CustomServers /
ClientVersion override the matching values in config.json.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
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


def load_proxy_settings(path):
    """FAPProxy2.exe.config -> {setting name: text}."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise ConfigError(f'Cannot read proxy config {path}: {e}')
    out = {}
    for s in root.iter('setting'):
        v = s.find('value')
        out[s.get('name')] = (v.text or '').strip() if v is not None else ''
    return out


def _apply_proxy_settings(pc, servers, version):
    host = pc.get('RemoteIP')
    if host:
        try:
            first, last = int(pc['RemoteFirstLoginPort']), int(pc['RemoteLastLoginPort'])
        except (KeyError, ValueError) as e:
            raise ConfigError(f'RemoteFirstLoginPort/RemoteLastLoginPort invalid in proxy config: {e!r}')
        # FAPProxyPy picks randrange(first, last), so last is excluded
        ports = list(range(first, max(last, first + 1)))
        name = next((s.name for s in servers if s.host == host), host)
        servers = [ServerEntry(name, host, ports)] + [s for s in servers if s.host != host]
    for item in pc.get('CustomServers', '').split(';'):
        name, _, hp = item.partition('=')
        chost, _, cport = hp.rpartition(':')
        if name.strip() and chost and cport.isdigit() and not any(s.name == name.strip() for s in servers):
            servers.append(ServerEntry(name.strip(), chost.strip(), [int(cport)]))
    cv = pc.get('ClientVersion')
    if cv:
        try:
            unk1, unk2, ver = (int(x) for x in cv.split(','))
        except ValueError as e:
            raise ConfigError(f'ClientVersion must be "unk1,unk2,ver" in proxy config: {e!r}')
        version = ClientVersion(unk1, unk2, ver)
    return servers, version


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

    cv = raw.get('client_version') or {}
    try:
        version = ClientVersion(int(cv.get('unk1', 0)), int(cv.get('unk2', 0)), int(cv.get('ver', 0)))
    except (TypeError, ValueError) as e:
        raise ConfigError(f'client_version is invalid: {e!r}')

    if raw.get('proxy_config'):
        pc_path = os.path.normpath(os.path.join(APP_DIR, os.path.expanduser(raw['proxy_config'])))
        servers, version = _apply_proxy_settings(load_proxy_settings(pc_path), servers, version)
    if not servers:
        raise ConfigError('No servers: add entries to "servers" or set RemoteIP in the proxy config.')

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
