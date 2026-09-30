from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .version import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = APP_NAME


def startup_available() -> bool:
    """Return True only for a Windows build installed by the Glean installer."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    app_dir = Path(sys.executable).resolve().parent
    try:
        return any(app_dir.glob("unins*.exe"))
    except OSError:
        return False


def startup_command() -> str:
    """Command stored in HKCU Run. --startup launches Glean directly to tray."""
    executable = str(Path(sys.executable).resolve())
    return subprocess.list2cmdline([executable, "--startup"])


def is_startup_enabled() -> bool:
    if not startup_available():
        return False

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, _RUN_VALUE)
    except (FileNotFoundError, OSError):
        return False

    return str(value or "").strip() == startup_command()


def set_startup_enabled(enabled: bool) -> None:
    """Enable/disable Glean in the current user's Windows startup list."""
    if not startup_available():
        if enabled:
            raise OSError("Glean startup integration is available only for an installed Windows build.")
        return

    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        _RUN_KEY,
        0,
        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
            except FileNotFoundError:
                pass
