"""FLbot entry point.  Edit config.json to change server hosts / Fairyland.exe path / client version."""

import sys
import tkinter as tk
from tkinter import messagebox

from flbot.config import ConfigError, load_config
from flbot.gui import App


def main():
    root = tk.Tk()
    try:
        cfg = load_config()
    except ConfigError as e:
        messagebox.showerror('FLbot', str(e))
        sys.exit(1)
    App(root, cfg)
    root.mainloop()


if __name__ == '__main__':
    main()
