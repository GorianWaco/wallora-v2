"""Wallpaper library management + thumbnail generation."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, List, Optional

import gi
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GLib, GdkPixbuf

from PIL import Image

from wallora.config import Config
from wallora.models import WallpaperItem
from wallora.utils import (
    SUPPORTED_EXTENSIONS,
    find_media_in_folder,
    get_thumbnail_path,
    is_animated_media,
    is_image_file,
    is_video_file,
)


class Library:
    def __init__(self, config: Config):
        self.config = config
        self._items: List[WallpaperItem] = []
        self._scan_lock = threading.Lock()

    @property
    def items(self) -> List[WallpaperItem]:
        return list(self._items)

    def get_folders(self) -> List[str]:
        return list(self.config.get("library_folders", []))

    def add_folder(self, path: str) -> bool:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            return False
        self.config.add_library_folder(str(p))
        return True

    def remove_folder(self, path: str):
        self.config.remove_library_folder(path)

    def scan(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_finished: Optional[Callable[[List[WallpaperItem]], None]] = None,
    ):
        """Scan all configured folders in background thread."""
        folders = [Path(f) for f in self.get_folders()]

        def worker():
            with self._scan_lock:
                new_items: List[WallpaperItem] = []
                total = 0
                for folder in folders:
                    media = find_media_in_folder(folder)
                    total += len(media)
                    for media_path in media:
                        item = WallpaperItem(
                            path=media_path,
                            is_favorite=self.config.is_favorite(str(media_path)),
                            is_animated=is_animated_media(media_path),
                        )
                        new_items.append(item)
                        if on_progress:
                            # thread safe dispatch
                            GLib.idle_add(on_progress, len(new_items), total)

                new_items.sort(key=lambda i: i.name.lower())
                self._items = new_items

                if on_finished:
                    GLib.idle_add(on_finished, self._items)

        threading.Thread(target=worker, daemon=True).start()

    def get_filtered(
        self,
        search: str = "",
        only_favorites: bool = False,
        only_recent: bool = False,
    ) -> List[WallpaperItem]:
        items = self._items

        if only_favorites:
            items = [i for i in items if i.is_favorite or self.config.is_favorite(str(i.path))]
        if only_recent:
            rec = self.config.get("recent", [])
            items = [i for i in items if str(i.path) in rec]

        if search:
            s = search.lower()
            items = [i for i in items if s in i.name.lower() or s in str(i.path).lower()]

        return items

    # --- Thumbnails ---

    def get_thumbnail(
        self,
        item: WallpaperItem,
        size: Optional[int] = None,
        on_ready: Optional[Callable[[WallpaperItem, GdkPixbuf.Pixbuf], None]] = None,
    ) -> Optional[GdkPixbuf.Pixbuf]:
        """
        Return cached thumbnail pixbuf synchronously if possible.
        If not cached, generate in background and call on_ready.
        """
        size = size or self.config.thumbnail_size
        thumb_path = get_thumbnail_path(item.path, size)

        if thumb_path.exists():
            try:
                if thumb_path.stat().st_size > 0:
                    return GdkPixbuf.Pixbuf.new_from_file(str(thumb_path))
                else:
                    thumb_path.unlink(missing_ok=True)
            except Exception:
                try:
                    thumb_path.unlink(missing_ok=True)
                except Exception:
                    pass

        # Generate in background
        def gen():
            try:
                pix = self._generate_thumbnail(item.path, size, thumb_path)
                if pix and on_ready:
                    GLib.idle_add(on_ready, item, pix)
            except Exception as e:
                print("Thumbnail gen error:", e)

        threading.Thread(target=gen, daemon=True).start()
        return None

    def _generate_thumbnail(self, src: Path, size: int, dest: Path) -> Optional[GdkPixbuf.Pixbuf]:
        try:
            if is_video_file(src) or (src.suffix.lower() == ".gif" and is_animated_media(src)):
                # Prefer ffmpeg frame for video; GIF still works via Pillow
                if src.suffix.lower() != ".gif":
                    if self._generate_video_thumbnail(src, size, dest):
                        return GdkPixbuf.Pixbuf.new_from_file(str(dest))
                    return None

            with Image.open(src) as im:
                # For animated GIF use first frame
                im.seek(0)
                im.thumbnail((size, size), Image.Resampling.LANCZOS)
                if im.mode in ("RGBA", "P"):
                    bg = Image.new("RGB", im.size, (48, 48, 48))
                    if im.mode == "P":
                        im = im.convert("RGBA")
                    bg.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
                    im = bg
                elif im.mode != "RGB":
                    im = im.convert("RGB")

                im.save(dest, "PNG", optimize=True)

            if not dest.exists() or dest.stat().st_size == 0:
                return None

            return GdkPixbuf.Pixbuf.new_from_file(str(dest))
        except Exception as e:
            print(f"Failed generating thumb for {src}: {e}")
            try:
                if dest.exists():
                    dest.unlink()
            except Exception:
                pass
            return None

    def _generate_video_thumbnail(self, src: Path, size: int, dest: Path) -> bool:
        """Extract a frame with ffmpeg and scale with Pillow."""
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("ffmpeg"):
            return False

        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            for seek in ("00:00:01.000", "00:00:00.200", "00:00:00.000"):
                cmd = [
                    "ffmpeg", "-y", "-ss", seek,
                    "-i", str(src),
                    "-frames:v", "1",
                    "-q:v", "3",
                    str(tmp_path),
                ]
                try:
                    subprocess.run(
                        cmd,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    break
            else:
                tmp_path.unlink(missing_ok=True)
                return False

            with Image.open(tmp_path) as im:
                im = im.convert("RGB")
                im.thumbnail((size, size), Image.Resampling.LANCZOS)
                im.save(dest, "PNG", optimize=True)

            tmp_path.unlink(missing_ok=True)
            return dest.exists() and dest.stat().st_size > 0
        except Exception as e:
            print(f"Video thumb error for {src}: {e}")
            return False

    def invalidate_thumbnail(self, item: WallpaperItem):
        size = self.config.thumbnail_size
        p = get_thumbnail_path(item.path, size)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    def clear_cache(self):
        from wallora.config import THUMB_DIR, CACHE_DIR

        for f in THUMB_DIR.glob("*.png"):
            try:
                f.unlink()
            except Exception:
                pass
