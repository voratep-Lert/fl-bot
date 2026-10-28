"""Tk window: pick server, log in, pick character.  Network runs on a background asyncio thread."""

import asyncio
import queue
import threading
import tkinter as tk
from tkinter import ttk

from .config import Config
from .game import format_state
from .login import ENCODING, LoginSession


class App:
    def __init__(self, root: tk.Tk, cfg: Config):
        self.root = root
        self.cfg = cfg
        self._q = queue.SimpleQueue()
        self._channels = []
        self._chars = {}
        self._logged_in = False
        self._channel_selected = False
        self._busy = False

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.session = LoginSession(cfg, self.log)

        root.title('FLbot')
        root.geometry('560x560')
        self._build()
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(100, self._pump)

    def _build(self):
        f = ttk.Frame(self.root, padding=8)
        f.pack(fill='both', expand=True)

        acct = ttk.Labelframe(f, text='Login', padding=6)
        acct.pack(fill='x')
        acct.columnconfigure(1, weight=1)

        ttk.Label(acct, text='Server:').grid(row=0, column=0, sticky='w')
        self._server = ttk.Combobox(acct, state='readonly', values=[s.name for s in self.cfg.servers])
        self._server.set(self.cfg.default_server)
        self._server.grid(row=0, column=1, sticky='ew', padx=4, pady=2)

        ttk.Label(acct, text='Username:').grid(row=1, column=0, sticky='w')
        self._user = ttk.Entry(acct)
        self._user.grid(row=1, column=1, sticky='ew', padx=4, pady=2)

        ttk.Label(acct, text='Password:').grid(row=2, column=0, sticky='w')
        self._pw = ttk.Entry(acct, show='*')
        self._pw.grid(row=2, column=1, sticky='ew', padx=4, pady=2)
        self._pw.bind('<Return>', lambda e: self._do_login())

        self._btn_login = ttk.Button(acct, text='Login', command=self._do_login)
        self._btn_login.grid(row=3, column=1, sticky='e', padx=4, pady=(4, 0))

        chans = ttk.Labelframe(f, text='Channel', padding=6)
        chans.pack(fill='x', pady=(8, 0))
        chans.columnconfigure(0, weight=1)
        self._channel = ttk.Combobox(chans, state='disabled')
        self._channel.grid(row=0, column=0, sticky='ew', padx=4)
        self._btn_select_channel = ttk.Button(chans, text='Select Channel', state='disabled', command=self._do_select_channel)
        self._btn_select_channel.grid(row=0, column=1, padx=4)

        chars = ttk.Labelframe(f, text='Character', padding=6)
        chars.pack(fill='x', pady=(8, 0))
        chars.columnconfigure(0, weight=1)
        self._char = ttk.Combobox(chars, state='disabled')
        self._char.grid(row=0, column=0, sticky='ew', padx=4)
        self._btn_select = ttk.Button(chars, text='Select Character', state='disabled', command=self._do_select)
        self._btn_select.grid(row=0, column=1, padx=4)
        ttk.Label(chars, text='Names are guessed from the server reply — type the name if it is not listed.',
                  foreground='gray').grid(row=1, column=0, columnspan=2, sticky='w', padx=4, pady=(4, 0))

        self._status = tk.StringVar(value='Ready.')
        ttk.Label(f, textvariable=self._status).pack(fill='x', pady=(8, 2))

        log_frame = ttk.Labelframe(f, text='Log', padding=4)
        log_frame.pack(fill='both', expand=True)
        self._log_text = tk.Text(log_frame, height=10, wrap='word', state='disabled')
        sb = ttk.Scrollbar(log_frame, orient='vertical', command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._log_text.pack(fill='both', expand=True)

    # ── thread bridge ──────────────────────────────────────────────────

    def log(self, text):
        self._q.put(('log', text))

    def _submit(self, coro, on_ok):
        self._set_busy(True)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: self._q.put(('done', f, on_ok)))

    def _pump(self):
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == 'log':
                    self._append_log(item[1])
                else:
                    self._finish(item[1], item[2])
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _finish(self, fut, on_ok):
        self._set_busy(False)
        try:
            result = fut.result()
        except Exception as e:
            self._status.set(f'Error: {e}')
            self._append_log(f'ERROR: {e}')
            return
        on_ok(result)

    def _append_log(self, text):
        self._log_text.configure(state='normal')
        self._log_text.insert('end', text + '\n')
        self._log_text.see('end')
        self._log_text.configure(state='disabled')

    def _set_busy(self, busy):
        self._busy = busy
        self._btn_login.configure(state='disabled' if busy else 'normal')
        self._btn_select_channel.configure(state='normal' if self._logged_in and not busy else 'disabled')
        self._btn_select.configure(state='normal' if self._channel_selected and not busy else 'disabled')

    # ── actions ────────────────────────────────────────────────────────

    def _do_login(self):
        if self._busy:
            return
        user, pw = self._user.get().strip(), self._pw.get()
        if not user or not pw:
            self._status.set('Enter username and password.')
            return
        server = self.cfg.find_server(self._server.get())
        self._logged_in = False
        self._channel_selected = False
        self._channel.set('')
        self._channel['values'] = []
        self._channel.configure(state='disabled')
        self._char.set('')
        self._char['values'] = []
        self._char.configure(state='disabled')
        self._status.set(f'Logging in to {server.name}…')
        self._submit(self.session.login(server, user, pw), self._on_logged_in)

    def _on_logged_in(self, channels):
        self._logged_in = True
        self._channels = channels
        shown = [f'{ch.name} (pop: {ch.population})' for ch in channels]
        self._channel['values'] = shown
        self._channel.configure(state='readonly')
        if shown:
            self._channel.set(shown[0])
        self._btn_select_channel.configure(state='normal')
        self._status.set(f'Logged in. {len(channels)} channel(s) available.')

    def _do_select_channel(self):
        if self._busy:
            return
        idx = self._channel.current()
        if idx < 0 or idx >= len(self._channels):
            self._status.set('Choose a channel.')
            return
        channel = self._channels[idx]
        self._status.set(f'Selecting channel {channel.name}…')
        self._submit(self.session.select_channel(channel.id), self._on_channel_selected)

    def _on_channel_selected(self, names):
        self._channel_selected = True
        self._chars = {n.decode(ENCODING, 'replace'): n for n in names}
        shown = list(self._chars)
        self._char['values'] = shown
        self._char.configure(state='normal')
        if shown:
            self._char.set(shown[0])
        self._btn_select.configure(state='normal')
        self._status.set(f'Channel selected. {len(shown)} character candidate(s) found.')

    def _do_select(self):
        if self._busy:
            return
        text = self._char.get().strip()
        if not text:
            self._status.set('Choose or type a character name.')
            return
        name = self._chars.get(text) or text.encode(ENCODING, 'replace')
        self._status.set(f'Selecting {text}…')
        self._submit(self.session.select_character(name), self._on_selected)

    def _on_selected(self, ticket):
        self._logged_in = False
        self._channel_selected = False
        self._btn_select_channel.configure(state='disabled')
        self._btn_select.configure(state='disabled')
        r = ticket.redirect
        self._status.set(f'Character selected. Game server: {r.host}:{r.port}')
        self._append_log(f'[game] ticket for {ticket.char.decode(ENCODING, "replace")}: '
                         f'{r.host}:{r.port}, hash {len(r.hash)} bytes')
        self._status.set('Entering game...')
        self._submit(self.session.enter_game(ticket), self._on_state)

    def _on_state(self, state):
        self._status.set('Game data received.')
        self._append_log(format_state(state, ENCODING))

    def _on_close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.session.close(), self.loop).result(timeout=2)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()
