"""Set wallpaper using the best available method for the current desktop environment."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from gi.repository import GLib

from wallora.utils import get_primary_monitor_geometry


def _run(cmd: list[str]) -> bool:
    """Run command, return success."""
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _run_get_output(cmd: list[str]) -> Optional[str]:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except Exception:
        return None


class WallpaperSetter:
    def __init__(self):
        self.desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
        self.session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        self._last_set_path: Optional[Path] = None

    def get_backend_name(self) -> str:
        if "gnome" in self.desktop or "unity" in self.desktop:
            return "GNOME"
        elif "kde" in self.desktop or "plasma" in self.desktop:
            return "KDE Plasma"
        elif "xfce" in self.desktop:
            return "XFCE"
        elif "cinnamon" in self.desktop:
            return "Cinnamon"
        elif "mate" in self.desktop:
            return "MATE"
        elif "sway" in self.desktop:
            return "Sway"
        elif "hyprland" in self.desktop or "hypr" in self.desktop:
            return "Hyprland"
        elif self.session_type == "wayland":
            return "Wayland (generic)"
        else:
            return "Generic (feh/nitrogen)"

    def supports_live_set(self) -> bool:
        return True

    def set_wallpaper(
        self,
        image_path: Path | str,
        scaling: str = "fill",
        use_processed: bool = True,
    ) -> bool:
        """
        Set the wallpaper. image_path should be the already processed file.
        """
        image_path = Path(image_path).expanduser().resolve()
        if not image_path.exists():
            print(f"Wallpaper file does not exist: {image_path}")
            return False

        success = False
        backend = self.get_backend_name()

        # Copy to a stable location that DE can always access (important for Flatpak)
        stable_path = self._get_stable_path(image_path)
        try:
            if str(stable_path) != str(image_path):
                shutil.copy2(image_path, stable_path)
        except Exception:
            stable_path = image_path

        self._last_set_path = stable_path

        # Static wallpaper must win over live video/GIF (and clear restore-on-login).
        # Stop *before* DE keys so the new image is visible immediately on GNOME.
        self._stop_animated_if_running()

        # GNOME / Cinnamon / Unity
        if "gnome" in self.desktop or "unity" in self.desktop or "cinnamon" in self.desktop:
            success = self._set_gnome(stable_path, scaling)

        # KDE Plasma
        elif "kde" in self.desktop or "plasma" in self.desktop:
            success = self._set_kde(stable_path)

        # XFCE
        elif "xfce" in self.desktop:
            success = self._set_xfce(stable_path, scaling)

        # MATE
        elif "mate" in self.desktop:
            success = self._set_mate(stable_path, scaling)

        # Sway / Hyprland / other Wayland
        elif "sway" in self.desktop:
            success = self._set_sway(stable_path)
        elif "hypr" in self.desktop:
            success = self._set_hyprland(stable_path)
        elif self.session_type == "wayland":
            success = self._set_wayland_generic(stable_path)

        # Fallbacks (X11 mostly)
        if not success:
            success = self._set_generic(stable_path, scaling)

        # Also try gsettings as last fallback for many modern DEs
        if not success:
            success = self._set_gsettings_fallback(stable_path, scaling)

        if success:
            # Also notify via gsettings picture-uri (helps some tools)
            self._notify_gnome_compatible(stable_path)

        return success

    def _stop_animated_if_running(self):
        """Always stop player + clear restore state (even if process already dead)."""
        try:
            from wallora.animated import AnimatedWallpaperManager
            AnimatedWallpaperManager().stop()
        except Exception:
            pass

    def _get_stable_path(self, src: Path) -> Path:
        """Return a path outside flatpak sandbox if possible."""
        # Prefer XDG cache or Pictures so host DE sees it
        cache = Path(GLib.get_user_cache_dir()) / "wallora"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / "active_wallpaper.jpg"
        return target

    # --- Backend implementations ---

    def _set_gnome(self, path: Path, scaling: str) -> bool:
        uri = path.as_uri()
        options = self._gnome_picture_options(scaling)

        cmds = [
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri],
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri],
            ["gsettings", "set", "org.gnome.desktop.background", "picture-options", options],
        ]
        # Cinnamon uses slightly different keys sometimes
        if "cinnamon" in self.desktop:
            cmds.append(["gsettings", "set", "org.cinnamon.desktop.background", "picture-options", options])

        ok = all(_run(c) for c in cmds)
        return ok

    def _set_kde(self, path: Path) -> bool:
        # plasma-apply-wallpaperimage is the cleanest
        if shutil.which("plasma-apply-wallpaperimage"):
            return _run(["plasma-apply-wallpaperimage", str(path)])

        # DBus fallback
        script = f"""
        var allDesktops = desktops();
        d = allDesktops[0];
        d.wallpaperPlugin = "org.kde.image";
        d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
        d.writeConfig("Image", "file:{path}");
        """
        return _run(["qdbus", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script])

    def _set_xfce(self, path: Path, scaling: str) -> bool:
        # xfconf-query
        prop = "/backdrop/screen0/monitor0/workspace0/last-image"
        ok = _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", str(path)])
        # Try to set style too
        style = {"fill": "5", "fit": "4", "stretch": "3", "center": "2", "tile": "1"}.get(scaling, "5")
        _run(["xfconf-query", "-c", "xfce4-desktop", "-p", "/backdrop/screen0/monitor0/workspace0/image-style", "-s", style])
        return ok

    def _set_mate(self, path: Path, scaling: str) -> bool:
        uri = path.as_uri()
        options = self._gnome_picture_options(scaling)
        return all([
            _run(["gsettings", "set", "org.mate.desktop.background", "picture-filename", str(path)]),
            _run(["gsettings", "set", "org.mate.desktop.background", "picture-options", options]),
        ])

    def _set_sway(self, path: Path) -> bool:
        if shutil.which("swaybg"):
            # kill previous swaybg if possible
            subprocess.run(["pkill", "-x", "swaybg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Launch in background
            subprocess.Popen(["swaybg", "-i", str(path), "-m", "fill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return False

    def _set_hyprland(self, path: Path) -> bool:
        if shutil.which("swww"):
            # swww is excellent
            return _run(["swww", "img", str(path)])
        if shutil.which("hyprpaper"):
            # hyprpaper needs config reload — we write a temp config
            cfg = Path(GLib.get_user_cache_dir()) / "wallora" / "hyprpaper.conf"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(f'preload = {path}\nwallpaper = ,{path}\n')
            return _run(["hyprctl", "hyprpaper", "reload", str(path)])
        # fallback to swaybg
        return self._set_sway(path)

    def _set_wayland_generic(self, path: Path) -> bool:
        if shutil.which("swww"):
            return _run(["swww", "img", str(path)])
        if shutil.which("swaybg"):
            subprocess.run(["pkill", "-x", "swaybg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen(["swaybg", "-i", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return False

    def _set_generic(self, path: Path, scaling: str) -> bool:
        """feh or nitrogen"""
        if shutil.which("feh"):
            mode = {
                "fill": "--bg-fill",
                "fit": "--bg-max",
                "stretch": "--bg-scale",
                "center": "--bg-center",
                "tile": "--bg-tile",
            }.get(scaling, "--bg-fill")
            return _run(["feh", mode, str(path)])
        if shutil.which("nitrogen"):
            return _run(["nitrogen", "--set-zoom-fill", "--save", str(path)])
        return False

    def _set_gsettings_fallback(self, path: Path, scaling: str) -> bool:
        """Works for many GNOME-like environments."""
        uri = path.as_uri()
        options = self._gnome_picture_options(scaling)
        ok1 = _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])
        _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri])
        ok2 = _run(["gsettings", "set", "org.gnome.desktop.background", "picture-options", options])
        return ok1 or ok2

    def _notify_gnome_compatible(self, path: Path):
        """Some tools watch this key."""
        uri = path.as_uri()
        _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])

    def _gnome_picture_options(self, scaling: str) -> str:
        scaling = scaling.lower()
        # Because for *_blur modes we pre-render the entire canvas at exact resolution,
        # we want the desktop environment to use the image without further letterboxing.
        if scaling.endswith("_blur"):
            return "stretched"
        mapping = {
            "fill": "zoom",
            "fit": "scaled",
            "stretch": "stretched",
            "center": "centered",
            "tile": "wallpaper",
            "span": "spanned",
        }
        return mapping.get(scaling, "zoom")

    def get_current_wallpaper_path(self) -> Optional[Path]:
        """Best effort detection of current wallpaper."""
        # GNOME
        if "gnome" in self.desktop or "cinnamon" in self.desktop:
            uri = _run_get_output(
                ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"]
            ) or _run_get_output(
                ["gsettings", "get", "org.cinnamon.desktop.background", "picture-uri"]
            )
            if uri:
                uri = uri.strip("'\"")
                if uri.startswith("file://"):
                    return Path(uri[7:])
                return Path(uri)

        # Try our stable cache
        stable = Path(GLib.get_user_cache_dir()) / "wallora" / "active_wallpaper.jpg"
        if stable.exists():
            return stable
        return None
