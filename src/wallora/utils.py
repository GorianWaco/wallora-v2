"""Utility functions for Wallora."""
import hashlib
from pathlib import Path
from typing import Iterable, List

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

# Static images (GIF is also treated as animated media when scanned as video)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}

# Animated / video formats for live wallpapers
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".gif", ".ogv"}

# Everything the library should list
MEDIA_EXTENSIONS = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS


def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_video_file(path: Path) -> bool:
    """True for video containers and animated GIF."""
    if not path.is_file():
        return False
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_media_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def is_animated_media(path: Path) -> bool:
    """Whether this file should be set via the animated wallpaper pipeline."""
    ext = path.suffix.lower()
    if ext in {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv"}:
        return True
    # GIF may be static or animated; treat as animated (player handles both)
    if ext == ".gif":
        return True
    return False


def find_images_in_folder(folder, recursive: bool = True) -> List[Path]:
    """Return list of static image files in folder. Accepts str or Path."""
    folder = Path(folder)
    images: List[Path] = []
    if not folder.exists():
        return images
    try:
        if recursive:
            for p in folder.rglob("*"):
                if is_image_file(p) and p.suffix.lower() != ".gif":
                    images.append(p)
                elif is_image_file(p) and p.suffix.lower() == ".gif":
                    # Keep GIF only in media scan to avoid double-listing? No —
                    # find_images used by old paths; prefer find_media_in_folder.
                    images.append(p)
        else:
            for p in folder.iterdir():
                if is_image_file(p):
                    images.append(p)
    except PermissionError:
        pass
    return sorted(images, key=lambda p: p.name.lower())


def find_media_in_folder(folder, recursive: bool = True) -> List[Path]:
    """Return images + videos suitable as wallpapers."""
    folder = Path(folder)
    media: List[Path] = []
    if not folder.exists():
        return media
    try:
        iterator = folder.rglob("*") if recursive else folder.iterdir()
        for p in iterator:
            if is_media_file(p):
                media.append(p)
    except PermissionError:
        pass
    return sorted(media, key=lambda p: p.name.lower())


def get_thumbnail_path(original: Path, size: int) -> Path:
    """Generate deterministic thumbnail cache path."""
    from wallora.config import THUMB_DIR

    h = hashlib.sha256(str(original).encode("utf-8")).hexdigest()[:16]
    return THUMB_DIR / f"{h}_{size}.png"


def scale_pixbuf_keep_aspect(pixbuf: GdkPixbuf.Pixbuf, max_w: int, max_h: int) -> GdkPixbuf.Pixbuf:
    w, h = pixbuf.get_width(), pixbuf.get_height()
    if w <= max_w and h <= max_h:
        return pixbuf
    ratio = min(max_w / w, max_h / h)
    new_w = max(1, int(w * ratio))
    new_h = max(1, int(h * ratio))
    return pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)


def get_primary_monitor_geometry() -> tuple[int, int]:
    """Return (width, height) of primary (or first) monitor."""
    display = Gdk.Display.get_default()
    if not display:
        return 1920, 1080
    monitors = display.get_monitors()
    if monitors.get_n_items() == 0:
        return 1920, 1080

    # In modern GTK4 + Wayland there is no is_primary(), pick first or one with largest area
    best = (1920, 1080)
    max_area = 0
    for i in range(monitors.get_n_items()):
        mon = monitors.get_item(i)
        if isinstance(mon, Gdk.Monitor):
            g = mon.get_geometry()
            area = g.width * g.height
            if area > max_area:
                max_area = area
                best = (g.width, g.height)
    return best


def get_all_monitors_geometry() -> List[tuple[int, int, int, int]]:
    """Return list of (x, y, w, h) for all monitors."""
    display = Gdk.Display.get_default()
    if not display:
        return [(0, 0, 1920, 1080)]
    geoms = []
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        mon = monitors.get_item(i)
        if isinstance(mon, Gdk.Monitor):
            g = mon.get_geometry()
            geoms.append((g.x, g.y, g.width, g.height))
    return geoms or [(0, 0, 1920, 1080)]


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m} min"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
