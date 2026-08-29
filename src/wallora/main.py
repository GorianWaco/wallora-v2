#!/usr/bin/env python3
"""Entry point for Wallora."""
import sys
import os
import random

# Add src to path when running from source
if __name__ == "__main__":
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw

from wallora.app import WalloraApp
from wallora import APP_ID
from wallora.config import Config
from wallora.library import Library
from wallora.processor import ImageProcessor
from wallora.wallpaper_setter import WallpaperSetter


def apply_random_wallpaper(silent: bool = False) -> bool:
    """
    Pick a random wallpaper from the library and set it.
    Uses the user's current default scaling and adjustments.
    """
    try:
        config = Config()
        library = Library(config)
        processor = ImageProcessor()
        setter = WallpaperSetter()

        # Scan synchronously for CLI mode
        folders = library.get_folders()
        if not folders:
            # Fallback to common locations
            for p in [os.path.expanduser("~/Obrazy"), os.path.expanduser("~/Pictures")]:
                if os.path.isdir(p):
                    library.add_folder(p)
            folders = library.get_folders()

        # Collect static images only (random-on-login should not start a video daemon)
        from wallora.utils import find_media_in_folder, is_animated_media
        all_images = []
        for folder in folders:
            for p in find_media_in_folder(folder):
                if not is_animated_media(p):
                    all_images.append(p)

        if not all_images:
            if not silent:
                print("Wallora: Nie znaleziono żadnych obrazów w bibliotece.")
            return False

        # Pick random
        chosen = random.choice(all_images)

        # Load and process
        if not processor.load(chosen):
            return False

        adjustments = config.get("adjustments", {})
        scaling = config.get("default_scaling", "fit")

        processed = processor.process_for_wallpaper(
            adjustments,
            scaling=scaling,
            multi_monitor=(scaling == "span")
        )

        if processed is None:
            return False

        out_path = processor.save_processed(processed)
        success = setter.set_wallpaper(out_path, scaling=scaling)

        if success and not silent:
            print(f"Wallora: Ustawiono losową tapetę: {chosen.name}")
        elif not success and not silent:
            print("Wallora: Nie udało się ustawić tapety.")

        return success

    except Exception as e:
        if not silent:
            print(f"Wallora error (random): {e}")
        return False


_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_DESKTOP",
    "XAUTHORITY",
    "GNOME_SETUP_DISPLAY",
)


def _unquote_systemd_env(value: str) -> str:
    value = value.strip()
    if value.startswith("$'") and value.endswith("'"):
        return value[2:-1]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _import_user_systemd_environment() -> None:
    """Copy session vars from the user manager (systemd starts before GNOME exports them)."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["systemctl", "--user", "show-environment"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key in _SESSION_ENV_KEYS and val and not os.environ.get(key):
            os.environ[key] = _unquote_systemd_env(val)


def _import_environ_from_process(comm: str) -> None:
    """Last-resort copy of DISPLAY/WAYLAND from gnome-shell / gnome-session."""
    from pathlib import Path

    try:
        for comm_file in Path("/proc").glob("*/comm"):
            try:
                if comm_file.read_text(encoding="utf-8", errors="ignore").strip() != comm:
                    continue
                raw = (comm_file.parent / "environ").read_bytes()
            except (OSError, PermissionError):
                continue
            for item in raw.split(b"\0"):
                if b"=" not in item:
                    continue
                key_b, _, val_b = item.partition(b"=")
                try:
                    key = key_b.decode()
                    val = val_b.decode()
                except UnicodeDecodeError:
                    continue
                if key in _SESSION_ENV_KEYS and val and not os.environ.get(key):
                    os.environ[key] = val
            return
    except Exception:
        return


def _import_session_environment() -> None:
    _import_user_systemd_environment()
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    for name in ("gnome-shell", "gnome-session", "gsd-xsettings"):
        _import_environ_from_process(name)
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return


def _wait_for_session(timeout: float = 90.0) -> bool:
    """Wait until display/session env is available (autostart can race DE startup)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        _import_session_environment()
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return True
        time.sleep(0.25)
    _import_session_environment()
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _wait_for_gnome_shell(timeout: float = 40.0) -> bool:
    """GNOME autostart often fires before the shell can honor DESKTOP windows."""
    import shutil
    import subprocess
    import time

    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or ""
    ).lower()
    if "gnome" not in desktop:
        return True
    if not shutil.which("gdbus"):
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rc = subprocess.run(
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell",
                    "--object-path", "/org/gnome/Shell",
                    "--method", "org.freedesktop.DBus.Peer.Ping",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if rc.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def _wait_for_x11_wm(timeout: float = 45.0) -> bool:
    """Wait until XWayland + EWMH WM is actually answering (not just $DISPLAY)."""
    import shutil
    import subprocess
    import time

    if not os.environ.get("DISPLAY"):
        return False
    if not shutil.which("xprop"):
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.check_output(
                ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                stderr=subprocess.DEVNULL,
                timeout=2,
                text=True,
            )
            if "0x" in out:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def restore_animated_wallpaper(silent: bool = False) -> bool:
    """Restore last animated wallpaper from state (used after login/reboot)."""
    try:
        import time

        from wallora.animated import (
            AnimatedWallpaperManager,
            acquire_restore_lock,
            log_restore,
        )

        lock = acquire_restore_lock()
        if lock is None:
            log_restore("restore skipped: already in progress")
            return True

        try:
            _import_session_environment()
            log_restore(
                "restore begin "
                f"DISPLAY={os.environ.get('DISPLAY', '')!r} "
                f"WAYLAND={os.environ.get('WAYLAND_DISPLAY', '')!r} "
                f"DESKTOP={os.environ.get('XDG_CURRENT_DESKTOP', '')!r}"
            )
            session_ok = _wait_for_session()
            gnome_ok = _wait_for_gnome_shell()
            x11_ok = _wait_for_x11_wm()
            log_restore(
                f"session ready session={session_ok} gnome={gnome_ok} x11={x11_ok}"
            )
            # Mutter still remaps early XWayland clients after the WM check appears.
            time.sleep(2.0)

            mgr = AnimatedWallpaperManager()
            ok, msg = mgr.restore()
            log_restore(f"restore result ok={ok} msg={msg}")
            if not silent:
                print(f"Wallora: {msg}")
            return ok
        finally:
            lock.close()
    except Exception as e:
        try:
            from wallora.animated import log_restore

            log_restore(f"restore error: {e}")
        except Exception:
            pass
        if not silent:
            print(f"Wallora error (restore-animated): {e}")
        return False


def main():
    # Support CLI flag for "change wallpaper on login"
    if "--random" in sys.argv or "--set-random" in sys.argv:
        ok = apply_random_wallpaper(silent=True)
        return 0 if ok else 1

    if "--restore-animated" in sys.argv or "--restore" in sys.argv:
        ok = restore_animated_wallpaper(silent=False)
        return 0 if ok else 1

    if "--release-session" in sys.argv:
        # Logout / graphical-session stop: kill the player, keep restore state.
        try:
            from wallora.animated import AnimatedWallpaperManager, log_restore

            log_restore("release-session: stop player, keep autostart")
            AnimatedWallpaperManager().stop(cancel_restore=False)
        except Exception as e:
            print(f"Wallora error (release-session): {e}")
            return 1
        return 0

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Wallora v2 - Zaawansowany menedżer tapet (animowane + statyczne)")
        print("Użycie:")
        print("  wallora                      Uruchom interfejs graficzny")
        print("  wallora --random             Ustaw losową tapetę i zakończ (autostart)")
        print("  wallora --restore-animated   Przywróć animowaną tapetę po restarcie")
        print("  wallora --release-session    Zatrzymaj odtwarzacz (zostaw autostart)")
        print("  wallora --stop-animated      Zatrzymaj animowaną tapetę")
        print("  wallora --export-favorites [FOLDER]")
        print("      Skopiuj ulubione do folderu (domyślnie z preferencji / Dokumenty)")
        print("  wallora --restore-favorites [FOLDER]")
        print("      Po reinstalacji: dodaj folder kopii i oznacz tapety jako ulubione")
        print("  wallora --import-steam-profiles [--force] [--all-quality]")
        print("      Import teł profilu Steam (ustawione w profilu + cache, HD ≥720p)")
        print("      --all-quality  także małe podglądy 300×168 z Point Shop")
        print("      --equipped-only  tylko aktualnie ustawione tło (+ historia)")
        print("      → ~/.cache/wallora/steam-profiles/")
        print("  flatpak run org.wallora.Wallora --random")
        return 0

    if "--export-favorites" in sys.argv:
        from wallora.favorites_vault import FavoritesVault

        config = Config()
        vault = FavoritesVault(config)
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        folder = args[0] if args else None
        if folder:
            vault.set_folder(folder)
        elif not vault.is_ready():
            vault.set_folder(vault.suggested_folder())
        result = vault.copy_all_favorites()
        print(
            f"Wallora: kopia ulubionych → {vault.folder()} "
            f"(+{result['copied']}, już było {result['skipped']}, błędów {result['failed']})"
        )
        for err in result["errors"][:8]:
            print(f"  ! {err}")
        return 1 if result["failed"] and not result["copied"] else 0

    if "--restore-favorites" in sys.argv:
        from wallora.favorites_vault import FavoritesVault

        config = Config()
        vault = FavoritesVault(config)
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        folder = args[0] if args else None
        result = vault.restore(folder)
        print(f"Wallora: {result.get('msg')}")
        return 0 if result.get("ok") else 1

    if "--stop-animated" in sys.argv:
        from wallora.animated import AnimatedWallpaperManager
        mgr = AnimatedWallpaperManager()
        if mgr.stop():
            print("Wallora: zatrzymano animowaną tapetę")
            return 0
        print("Wallora: brak aktywnej animowanej tapety")
        return 0

    if "--import-steam-profiles" in sys.argv:
        from wallora.steam_import import import_and_register

        force = "--force" in sys.argv
        quality = "all" if "--all-quality" in sys.argv else "high"
        equipped_only = "--equipped-only" in sys.argv
        config = Config()
        result = import_and_register(
            config,
            quality=quality,
            force=force,
            progress=lambda msg: print(msg),
            include_equipped=True,
            include_cache=not equipped_only,
        )
        print(result.summary())
        if result.errors and result.imported == 0 and result.skipped == 0:
            for e in result.errors[:8]:
                print(f"  ! {e}")
            return 1
        if result.filtered_low_quality:
            print(
                f"  (odrzucono {result.filtered_low_quality} podglądów niskiej jakości; "
                f"użyj --all-quality aby je zachować)"
            )
        if result.equipped_found:
            print(
                f"Wallora: tła z profilu Steam: {result.equipped_found} "
                f"(+{result.equipped_imported} nowych)"
            )
        print(f"Wallora: zaimportowano {result.imported} teł → {result.dest_dir}")
        return 0

    # Normal GUI launch
    app = WalloraApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
