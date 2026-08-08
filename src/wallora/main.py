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


def _wait_for_session(timeout: float = 45.0) -> bool:
    """Wait until display/session env is available (autostart can race DE startup)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return True
        time.sleep(0.25)
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def restore_animated_wallpaper(silent: bool = False) -> bool:
    """Restore last animated wallpaper from state (used after login/reboot)."""
    try:
        _wait_for_session()
        # Brief extra delay so Mutter/compositor is ready for DESKTOP windows
        import time
        time.sleep(1.5)

        from wallora.animated import AnimatedWallpaperManager

        mgr = AnimatedWallpaperManager()
        ok, msg = mgr.restore()
        if not silent:
            print(f"Wallora: {msg}")
        return ok
    except Exception as e:
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

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Wallora v2 - Zaawansowany menedżer tapet (animowane + statyczne)")
        print("Użycie:")
        print("  wallora                      Uruchom interfejs graficzny")
        print("  wallora --random             Ustaw losową tapetę i zakończ (autostart)")
        print("  wallora --restore-animated   Przywróć animowaną tapetę po restarcie")
        print("  wallora --stop-animated      Zatrzymaj animowaną tapetę")
        print("  wallora --import-steam-profiles [--force] [--all-quality]")
        print("      Import teł profilu Steam (ustawione w profilu + cache, HD ≥720p)")
        print("      --all-quality  także małe podglądy 300×168 z Point Shop")
        print("      --equipped-only  tylko aktualnie ustawione tło (+ historia)")
        print("      → ~/.cache/wallora/steam-profiles/")
        print("  flatpak run org.wallora.Wallora --random")
        return 0

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
