"""Persistent copies of favorite wallpapers (survive OS reinstall).

User picks a folder outside the system install (second disk, Nextcloud,
~/Dokumenty, USB). Starring a wallpaper copies the file there and writes
``wallora-favorites.json`` so Wallora can restore favorites after a fresh
install — just point it at the same folder.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wallora.config import Config
from wallora.utils import find_media_in_folder

INDEX_NAME = "wallora-favorites.json"
README_NAME = "CZYTAJ-MNIE.txt"
README_TEXT = """Kopie ulubionych tapet z Wallora 2
=================================

Ten folder jest niezależny od instalacji systemu.
Po przeinstalowaniu Linuksa:

1. Zainstaluj Wallorę 2
2. Preferencje → Ulubione poza systemem → Wybierz ten folder
3. Kliknij „Przywróć ulubione z folderu”

Albo: w Wallorze „Dodaj folder biblioteki” i wskaż ten katalog.
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha1_prefix(path: Path, nbytes: int = 1_048_576) -> str:
    h = hashlib.sha1()
    try:
        with path.open("rb") as fh:
            h.update(fh.read(nbytes))
            extra = path.stat().st_size
            h.update(str(extra).encode("ascii"))
    except OSError:
        h.update(path.name.encode("utf-8", errors="replace"))
    return h.hexdigest()[:8]


class FavoritesVault:
    def __init__(self, config: Config):
        self.config = config

    def folder(self) -> Optional[Path]:
        raw = (self.config.get("favorites_vault.folder") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()

    def is_ready(self) -> bool:
        folder = self.folder()
        return bool(folder and folder.is_dir())

    def auto_copy_enabled(self) -> bool:
        return bool(self.config.get("favorites_vault.auto_copy", True))

    def suggested_folder(self) -> Path:
        docs = Path.home() / "Dokumenty"
        if not docs.is_dir():
            docs = Path.home() / "Documents"
        if not docs.is_dir():
            docs = Path.home()
        return docs / "Wallora-ulubione"

    def set_folder(self, path: str | Path) -> Path:
        folder = Path(path).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        self.config.set("favorites_vault.folder", str(folder))
        self._ensure_readme(folder)
        self._save_index(self._load_index(folder), folder)
        if self.config.get("favorites_vault.add_to_library", False):
            self.config.add_library_folder(str(folder))
        return folder

    def is_inside(self, path: Path | str) -> bool:
        folder = self.folder()
        if not folder:
            return False
        try:
            Path(path).expanduser().resolve().relative_to(folder.resolve())
            return True
        except (ValueError, OSError):
            return False

    def copy_file(self, src: Path | str) -> tuple[bool, str, Optional[Path]]:
        """Copy one wallpaper into the vault. Safe to call if already copied."""
        src = Path(src).expanduser()
        folder = self.folder()
        if folder is None:
            return False, "Najpierw wybierz folder kopii ulubionych", None
        if not src.is_file():
            return False, f"Brak pliku: {src}", None
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Nie można utworzyć folderu: {e}", None

        if self.is_inside(src):
            return True, "Plik już jest w folderze kopii", src

        dest = self._destination_for(folder, src)
        try:
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                self._record(folder, dest, src)
                return True, f"Już zapisane: {dest.name}", dest
            shutil.copy2(src, dest)
        except OSError as e:
            return False, f"Kopiowanie nie powiodło się: {e}", None

        self._record(folder, dest, src)
        self._ensure_readme(folder)
        return True, f"Zapisano kopię: {dest.name}", dest

    def copy_paths(self, paths: list[Path | str]) -> dict:
        copied = skipped = failed = 0
        errors: list[str] = []
        last_dest: Optional[Path] = None
        seen: set[str] = set()
        for raw in paths:
            key = str(Path(raw).expanduser())
            if key in seen:
                continue
            seen.add(key)
            ok, msg, dest = self.copy_file(raw)
            if not ok:
                failed += 1
                errors.append(msg)
            elif dest is not None and "Już zapisane" in msg:
                skipped += 1
                last_dest = dest
            else:
                copied += 1
                last_dest = dest
        return {
            "copied": copied,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "dest": last_dest,
        }

    def copy_all_favorites(self) -> dict:
        return self.copy_paths(list(self.config.get("favorites", []) or []))

    def restore(self, folder: Path | str | None = None) -> dict:
        """Register vault files as library + favorites (after reinstall)."""
        if folder is not None:
            target = self.set_folder(folder)
        else:
            target = self.folder()
            if target is None:
                return {"ok": False, "added": 0, "marked": 0, "msg": "Brak folderu kopii"}
            target = self.set_folder(target)

        self.config.add_library_folder(str(target))
        index = self._load_index(target)
        names: list[str] = []
        for item in index.get("items", []):
            name = str(item.get("file") or "").strip()
            if name:
                names.append(name)

        media = find_media_in_folder(target)
        by_name = {p.name: p for p in media}
        marked = 0
        for name in names:
            path = by_name.get(name)
            if path is not None:
                self.config.add_favorite(str(path))
                marked += 1
        for path in media:
            if path.name not in names:
                self.config.add_favorite(str(path))
                marked += 1
        return {
            "ok": True,
            "added": len(media),
            "marked": marked,
            "folder": str(target),
            "msg": f"Przywrócono {marked} ulubionych z {target}",
        }

    def maybe_adopt_folder(self, path: Path | str) -> int:
        """If folder is a vault (has index), mark its files as favorites."""
        folder = Path(path).expanduser()
        index_path = folder / INDEX_NAME
        if not index_path.is_file():
            return 0
        result = self.restore(folder)
        return int(result.get("marked") or 0)

    def heal_missing_favorites(self) -> int:
        """Rewrite favorite paths that vanished (cache wiped) to vault copies."""
        folder = self.folder()
        if folder is None or not folder.is_dir():
            return 0
        index = self._load_index(folder)
        by_source = {}
        by_name = {}
        for item in index.get("items", []):
            name = str(item.get("file") or "")
            src = str(item.get("source") or "")
            dest = folder / name
            if name and dest.is_file():
                by_name[name] = dest
                if src:
                    by_source[src] = dest

        favs = list(self.config.get("favorites", []) or [])
        changed = False
        healed = 0
        new_favs: list[str] = []
        seen: set[str] = set()
        for fav in favs:
            p = Path(fav)
            if p.is_file():
                key = str(p)
            else:
                dest = by_source.get(fav) or by_name.get(p.name)
                if dest is not None and dest.is_file():
                    key = str(dest)
                    healed += 1
                    changed = True
                else:
                    key = fav
            if key not in seen:
                seen.add(key)
                new_favs.append(key)
        if changed:
            self.config.set("favorites", new_favs)
        return healed

    def _destination_for(self, folder: Path, src: Path) -> Path:
        dest = folder / src.name
        if not dest.exists():
            return dest
        try:
            if dest.stat().st_size == src.stat().st_size:
                return dest
        except OSError:
            pass
        return folder / f"{src.stem}_{_sha1_prefix(src)}{src.suffix}"

    def _load_index(self, folder: Path) -> dict:
        path = folder / INDEX_NAME
        if not path.is_file():
            return {"version": 1, "items": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"version": 1, "items": []}
            data.setdefault("version", 1)
            data.setdefault("items", [])
            return data
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "items": []}

    def _save_index(self, data: dict, folder: Path) -> None:
        data["version"] = 1
        data["updated"] = _now()
        path = folder / INDEX_NAME
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            print("Favorites vault index save failed:", e)

    def _record(self, folder: Path, dest: Path, src: Path) -> None:
        data = self._load_index(folder)
        items = [i for i in data.get("items", []) if i.get("file") != dest.name]
        items.append({
            "file": dest.name,
            "source": str(src.resolve()) if src.exists() else str(src),
            "added": _now(),
        })
        data["items"] = items
        self._save_index(data, folder)

    @staticmethod
    def _ensure_readme(folder: Path) -> None:
        path = folder / README_NAME
        if path.exists():
            return
        try:
            path.write_text(README_TEXT, encoding="utf-8")
        except OSError:
            pass
