"""Configuration management for Wallora."""
import json
import os
import sys
from pathlib import Path
from typing import Any

from gi.repository import GLib

APP_ID = "org.wallora.Wallora"

DEFAULT_CONFIG = {
    "library_folders": [],
    "last_folder": str(Path.home() / "Obrazy"),
    "slideshow": {
        "enabled": False,
        "interval_seconds": 300,
        "random": True,
        "only_favorites": False,
        "only_recent": False,
        "avoid_recent": True,
    },
    "default_scaling": "fill",  # fill, fit, stretch, center, tile, span
    "adjustments": {
        "brightness": 1.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "hue": 0,
        "gamma": 1.0,
        "sharpness": 1.0,
        "temperature": 0,
        "blur": 0.0,
        "vignette": 0.0,
        "edge_blur": 0.0,
        "edge_tint": 0,
        "bg_blur": 0.5,
        "bg_expand": 0.0,
        "bg_fade": 0.1,
    },
    "favorites": [],
    "favorites_vault": {
        # Folder outside the OS install (second disk / Nextcloud / Dokumenty)
        "folder": "",
        "auto_copy": True,        # copy file when starring
        "add_to_library": False,  # avoid duplicate thumbs next to originals
    },
    "recent": [],  # list of paths
    "window_width": 1200,
    "window_height": 800,
    "thumbnail_size": 220,
    "random_on_login": False,   # If True, we create an autostart entry to set random wallpaper on login
    "animated": {
        "mute": True,
        "loop": True,
        "set_poster": True,       # Also set a static poster frame as DE wallpaper
        "backend": "auto",        # auto | mpvpaper | xwinwrap+mpv | gtk-player | mpv-window
        "include_in_slideshow": False,  # Videos usually need long intervals; off by default
        "last_path": "",          # last animated file — restore fallback if cache is gone
    },
    "steam": {
        # Auto-download currently equipped profile wallpaper on app start
        # and remember each one you equip later (history).
        "auto_import_equipped": True,
        # Re-check while Wallora is open (seconds). 0 = only at start.
        "poll_interval_seconds": 600,
    },
}

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "wallora"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "wallora"
THUMB_DIR = CACHE_DIR / "thumbnails"


class Config:
    def __init__(self):
        self.data: dict[str, Any] = DEFAULT_CONFIG.copy()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Merge to keep new defaults
                    self._deep_update(self.data, loaded)
            except Exception as e:
                print(f"Failed to load config: {e}")

    def save(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def _deep_update(self, base: dict, update: dict):
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value

    def get(self, key: str, default=None):
        keys = key.split(".")
        val = self.data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key: str, value: Any):
        keys = key.split(".")
        d = self.data
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self.save()

    def add_library_folder(self, path: str):
        folders = self.get("library_folders", [])
        if path not in folders:
            folders.append(path)
            self.set("library_folders", folders)

    def remove_library_folder(self, path: str):
        folders = self.get("library_folders", [])
        if path in folders:
            folders.remove(path)
            self.set("library_folders", folders)

    def add_favorite(self, path: str):
        favs = self.get("favorites", [])
        if path not in favs:
            favs.append(path)
            self.set("favorites", favs)

    def remove_favorite(self, path: str):
        favs = self.get("favorites", [])
        if path in favs:
            favs.remove(path)
            self.set("favorites", favs)

    def is_favorite(self, path: str) -> bool:
        return path in self.get("favorites", [])

    def add_recent(self, path: str, max_items: int = 30):
        rec = self.get("recent", [])
        if path in rec:
            rec.remove(path)
        rec.insert(0, path)
        self.set("recent", rec[:max_items])

    @property
    def thumbnail_size(self) -> int:
        return self.get("thumbnail_size", 220)

    def reset_adjustments(self):
        self.data["adjustments"] = DEFAULT_CONFIG["adjustments"].copy()
        self.save()

    # --- Autostart helpers ---

    def _autostart_dir(self) -> Path:
        autostart_dir = Path(GLib.get_user_config_dir()) / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        return autostart_dir

    def _get_autostart_desktop_path(self) -> Path:
        return self._autostart_dir() / "org.wallora.Wallora.random.desktop"

    def _get_animated_autostart_path(self) -> Path:
        return self._autostart_dir() / "org.wallora.Wallora.animated.desktop"

    @staticmethod
    def wallora_exec(*args: str) -> str:
        """Build a reliable CLI command for autostart / desktop entries."""
        import shutil

        is_flatpak = os.path.exists("/.flatpak-info") or os.environ.get("FLATPAK_ID")
        if is_flatpak:
            base = "flatpak run org.wallora.Wallora"
            return f"{base} {' '.join(args)}".strip()

        wallora_bin = shutil.which("wallora")
        if wallora_bin:
            return f"{wallora_bin} {' '.join(args)}".strip()

        # Source tree: …/wallora-v2/src/wallora/config.py → run script at repo root
        run_script = Path(__file__).resolve().parent.parent.parent / "run"
        if run_script.is_file():
            return f"{run_script} {' '.join(args)}".strip()

        return f"{sys.executable} -m wallora.main {' '.join(args)}".strip()

    def enable_random_on_login(self):
        """Create an autostart .desktop entry that sets a random wallpaper on login."""
        desktop_path = self._get_autostart_desktop_path()
        exec_line = self.wallora_exec("--random")

        content = f"""[Desktop Entry]
Type=Application
Name=Wallora - Losowa tapeta przy logowaniu
Comment=Ustawia losową tapetę przy starcie sesji
Exec={exec_line}
StartupNotify=false
Terminal=false
X-GNOME-Autostart-enabled=true
Hidden=false
"""

        try:
            desktop_path.write_text(content, encoding="utf-8")
            self.set("random_on_login", True)
            return True
        except Exception as e:
            print("Failed to create autostart entry:", e)
            return False

    def disable_random_on_login(self):
        """Remove the autostart entry."""
        desktop_path = self._get_autostart_desktop_path()
        try:
            if desktop_path.exists():
                desktop_path.unlink()
            self.set("random_on_login", False)
            return True
        except Exception as e:
            print("Failed to remove autostart entry:", e)
            return False

    def is_random_on_login_enabled(self) -> bool:
        return self.get("random_on_login", False) and self._get_autostart_desktop_path().exists()

    def _animated_systemd_unit_path(self) -> Path:
        unit_dir = Path(GLib.get_user_config_dir()) / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        return unit_dir / "wallora-restore-animated.service"

    def _systemctl_user(self, *args: str) -> bool:
        import shutil
        import subprocess

        cmd = ["systemctl", "--user", *args]
        if os.path.exists("/.flatpak-info") or os.environ.get("FLATPAK_ID"):
            spawn = shutil.which("flatpak-spawn")
            if spawn:
                cmd = [spawn, "--host", "systemctl", "--user", *args]
        try:
            rc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            return rc.returncode == 0
        except Exception:
            return False

    def _install_animated_systemd_unit(self) -> bool:
        """GNOME 50+ skips XDG autostart marked as session services; systemd is reliable."""
        unit_path = self._animated_systemd_unit_path()
        exec_line = self.wallora_exec("--restore-animated")
        stop_line = self.wallora_exec("--release-session")
        desired = f"""[Unit]
Description=Wallora - restore animated wallpaper after login
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=oneshot
ExecStart={exec_line}
ExecStop={stop_line}
TimeoutStartSec=180
RemainAfterExit=yes

[Install]
WantedBy=graphical-session.target
"""
        existing = unit_path.read_text(encoding="utf-8") if unit_path.exists() else ""
        if existing != desired:
            unit_path.write_text(desired, encoding="utf-8")
            self._systemctl_user("daemon-reload")
        if not self._systemctl_user("is-enabled", "--quiet", "wallora-restore-animated.service"):
            return self._systemctl_user("enable", "wallora-restore-animated.service")
        return True

    def _remove_animated_systemd_unit(self) -> bool:
        # Never `disable --now`: restore itself used to call this and get SIGTERM.
        self._systemctl_user("disable", "wallora-restore-animated.service")
        unit_path = self._animated_systemd_unit_path()
        try:
            if unit_path.exists():
                unit_path.unlink()
        except Exception:
            return False
        self._systemctl_user("daemon-reload")
        return True

    def enable_animated_restore_on_login(self) -> bool:
        """Autostart that restarts the last animated wallpaper after login/reboot."""
        desktop_path = self._get_animated_autostart_path()
        exec_line = self.wallora_exec("--restore-animated")

        # Do NOT set X-GNOME-Autostart-Phase. GNOME 49/50 treats that as a
        # session service, skips the .desktop file, and systemd then adds
        # NotShowIn=GNOME — so restore never runs after login.
        content = f"""[Desktop Entry]
Type=Application
Name=Wallora - Przywróć animowaną tapetę
Comment=Po restarcie wznawia ostatnią animowaną tapetę
Exec={exec_line}
StartupNotify=false
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
X-KDE-autostart-after=panel
Hidden=false
"""

        try:
            desktop_path.write_text(content, encoding="utf-8")
            self._install_animated_systemd_unit()
            return True
        except Exception as e:
            print("Failed to create animated autostart:", e)
            return False

    def disable_animated_restore_on_login(self) -> bool:
        """Remove animated restore autostart (e.g. when user stops animation)."""
        desktop_path = self._get_animated_autostart_path()
        ok = True
        try:
            if desktop_path.exists():
                desktop_path.unlink()
        except Exception as e:
            print("Failed to remove animated autostart:", e)
            ok = False
        if not self._remove_animated_systemd_unit():
            ok = False
        return ok

    def is_animated_restore_on_login_enabled(self) -> bool:
        if self._get_animated_autostart_path().exists():
            return True
        return self._systemctl_user("is-enabled", "--quiet", "wallora-restore-animated.service")
