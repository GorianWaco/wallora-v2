"""Import animowanych teł profilu Steam do biblioteki Wallora.

Źródła:
  1. **Aktualnie ustawione** tło profilu (localconfig.vdf + API Steam)
  2. Historia ustawionych teł (każda kolejna zapamiętana lokalnie)
  3. Cache Chromium klienta Steam (htmlcache)
  4. CDN Steam (preferowane — czystsze pliki niż cache)

Domyślnie importuje tylko **wysoką jakość** (min. ~720p / ≥200 KB)
z cache sklepu (miniatury 300×168 są odrzucane). Tła **ustawione
w profilu** ściągane są zawsze (pełny ``movie_webm``, nie small).

Wynik: ~/.cache/wallora/steam-profiles/
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from wallora.config import CACHE_DIR

STEAM_PROFILES_DIR = CACHE_DIR / "steam-profiles"
EQUIPPED_HISTORY_FILE = STEAM_PROFILES_DIR / "equipped_history.json"

ITEM_WEBM_URL_RE = re.compile(
    rb"https?://[^\s\"'<>]*?(?:steamstatic\.com|akamaihd\.net)/"
    rb"(?:community_assets/)?(?:steamcommunity/public/)?images/items/"
    rb"(\d+)/([a-f0-9]+)\.webm",
    re.IGNORECASE,
)
ITEM_WEBM_PATH_RE = re.compile(
    rb"(?:community_assets/)?(?:steamcommunity/public/)?images/items/"
    rb"(\d+)/([a-f0-9]+)\.webm",
    re.IGNORECASE,
)

# localconfig / API: items/<appid>/<hash>.webm  (not movie_webm_small)
MOVIE_WEBM_RE = re.compile(
    r'(?:movie_webm|item_movie_webm)\\?"?\s*[:=]\s*\\?"?items/(\d+)/([a-f0-9]+)\.webm',
    re.IGNORECASE,
)
MOVIE_WEBM_RE_JSON = re.compile(
    r'"movie_webm"\s*:\s*"items/(\d+)/([a-f0-9]+)\.webm"',
    re.IGNORECASE,
)
# escaped inside VDF JSON strings: \"movie_webm\":\"items/...\"
MOVIE_WEBM_RE_ESC = re.compile(
    r'\\"movie_webm\\":\\"items/(\d+)/([a-f0-9]+)\.webm\\"',
    re.IGNORECASE,
)
MOVIE_WEBM_SMALL_RE = re.compile(
    r'movie_webm_small\\?"?\s*[:=]\s*\\?"?items/(\d+)/([a-f0-9]+)\.webm',
    re.IGNORECASE,
)
MOVIE_MP4_RE = re.compile(
    r'(?:movie_mp4|item_movie_mp4)\\?"?\s*[:=]\s*\\?"?items/(\d+)/([a-f0-9]+)\.mp4',
    re.IGNORECASE,
)
MOVIE_MP4_SMALL_RE = re.compile(
    r'movie_mp4_small\\?"?\s*[:=]\s*\\?"?items/(\d+)/([a-f0-9]+)\.mp4',
    re.IGNORECASE,
)
ITEM_IMAGE_ACTION_RE = re.compile(
    r"https?://[^\s\"']+/images/items/(\d+)/([a-f0-9]+)\.(jpg|jpeg|png|webp)",
    re.IGNORECASE,
)

WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".webm", ".mp4"}

# Point Shop tiles are ~300×168. Real animated profile BGs start around 640×400.
MIN_BYTES_HIGH = 200_000
MIN_WIDTH_HIGH = 960
MIN_HEIGHT_HIGH = 540
MIN_BYTES_ANY = 5_000
SHOP_PREVIEW_MAX_W = 320
SHOP_PREVIEW_MAX_H = 200
SHOP_PREVIEW_MAX_BYTES = 40_000

CDN_BASES = (
    "https://shared.fastly.steamstatic.com/community_assets/images/items",
    "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/items",
    "https://steamcdn-a.akamaihd.net/steamcommunity/public/images/items",
    "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/items",
)

ProgressCb = Optional[Callable[[str], None]]
_INVENTORY_CACHE: dict = {"at": 0.0, "items": None}


@dataclass
class SteamImportResult:
    dest_dir: Path
    discovered: int = 0
    extracted: int = 0
    downloaded: int = 0
    skipped: int = 0
    upgraded: int = 0
    filtered_low_quality: int = 0
    failed: int = 0
    equipped_found: int = 0
    equipped_imported: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return self.extracted + self.downloaded + self.upgraded

    def summary(self) -> str:
        parts = [
            f"znaleziono {self.discovered}",
            f"z cache {self.extracted}",
            f"z CDN {self.downloaded}",
            f"uaktualniono {self.upgraded}",
            f"pominięto {self.skipped}",
        ]
        if self.equipped_found:
            parts.append(
                f"ustawione w profilu {self.equipped_found} "
                f"(+{self.equipped_imported} nowych)"
            )
        if self.filtered_low_quality:
            parts.append(f"odrzucono podglądy {self.filtered_low_quality}")
        if self.failed:
            parts.append(f"niedostępnych {self.failed}")
        return f"Import Steam: {', '.join(parts)}. Folder: {self.dest_dir}"


def _merge_result(into: SteamImportResult, other: SteamImportResult) -> None:
    into.discovered += other.discovered
    into.extracted += other.extracted
    into.downloaded += other.downloaded
    into.skipped += other.skipped
    into.upgraded += other.upgraded
    into.filtered_low_quality += other.filtered_low_quality
    into.failed += other.failed
    into.equipped_found += other.equipped_found
    into.equipped_imported += other.equipped_imported
    into.errors.extend(other.errors)


def steam_root_candidates() -> list[Path]:
    home = Path.home()
    cands = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def find_htmlcache_dirs() -> list[Path]:
    dirs: list[Path] = []
    for root in steam_root_candidates():
        for rel in (
            "config/htmlcache/Default/Cache/Cache_Data",
            "config/htmlcache/Default/Cache",
        ):
            p = root / rel
            if p.is_dir():
                dirs.append(p)
    return dirs


def find_localconfig_paths() -> list[Path]:
    """Steam userdata/*/config/localconfig.vdf (equipped profile items)."""
    out: list[Path] = []
    for root in steam_root_candidates():
        ud = root / "userdata"
        if not ud.is_dir():
            continue
        try:
            for user_dir in ud.iterdir():
                p = user_dir / "config" / "localconfig.vdf"
                if p.is_file():
                    out.append(p)
        except OSError:
            continue
    return out


def find_login_steamids() -> list[str]:
    """SteamID64 from loginusers.vdf."""
    ids: list[str] = []
    seen: set[str] = set()
    for root in steam_root_candidates():
        for rel in ("config/loginusers.vdf", "config/loginusers.vdf".replace("/", "\\")):
            p = root / "config" / "loginusers.vdf"
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in re.finditer(r'"(\d{17})"', text):
                sid = m.group(1)
                if sid not in seen:
                    seen.add(sid)
                    ids.append(sid)
    return ids


def _item_key(appid: str, hashhex: str) -> str:
    return f"{appid}_{hashhex}"


def _dest_path(dest_dir: Path, appid: str, hashhex: str, ext: str = "webm") -> Path:
    ext = str(ext).lstrip(".").lower() or "webm"
    if ext == "jpeg":
        ext = "jpg"
    return dest_dir / f"steam_{appid}_{hashhex[:12]}.{ext}"


def _item_ext(info: dict) -> str:
    ext = str(info.get("ext") or "").lstrip(".").lower()
    if ext:
        return "jpg" if ext == "jpeg" else ext
    url = str(info.get("url") or "")
    if "." in url.rsplit("/", 1)[-1]:
        tail = url.rsplit(".", 1)[-1].split("?", 1)[0].lower()
        if tail in {"webm", "mp4", "jpg", "jpeg", "png", "webp"}:
            return "jpg" if tail == "jpeg" else tail
    return "webm"


def _load_equipped_history() -> dict[str, dict]:
    if not EQUIPPED_HISTORY_FILE.is_file():
        return {}
    try:
        data = json.loads(EQUIPPED_HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def _save_equipped_history(history: dict[str, dict]) -> None:
    try:
        STEAM_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        EQUIPPED_HISTORY_FILE.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _add_equipped_item(
    found: dict[str, dict],
    appid: str,
    hashhex: str,
    *,
    name: str = "",
    source: str = "equipped",
    slot: str = "profile_background",
) -> None:
    appid = str(appid).strip()
    hashhex = str(hashhex).strip().lower()
    if not appid.isdigit() or len(hashhex) < 16:
        return
    # Never treat movie_webm_small hashes as equipped full assets here —
    # caller must pass full movie_webm only.
    key = _item_key(appid, hashhex)
    url = f"{CDN_BASES[0]}/{appid}/{hashhex}.webm"
    prev = found.get(key)
    if prev is None:
        found[key] = {
            "appid": appid,
            "hash": hashhex,
            "url": url,
            "ext": "webm",
            "source_file": None,
            "body_size": 0,
            "name": name or "",
            "source": source,
            "slot": slot,
            "equipped": True,
        }
    else:
        prev["equipped"] = True
        if name and not prev.get("name"):
            prev["name"] = name
        if source and not prev.get("source"):
            prev["source"] = source


def discover_equipped_from_localconfig(
    paths: Optional[Iterable[Path]] = None,
) -> dict[str, dict]:
    """Parse equipped profile movie_webm from Steam localconfig.vdf files."""
    found: dict[str, dict] = {}
    small_keys: set[str] = set()
    for path in paths if paths is not None else find_localconfig_paths():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in MOVIE_WEBM_SMALL_RE.finditer(text):
            small_keys.add(_item_key(m.group(1), m.group(2).lower()))
        for rx in (MOVIE_WEBM_RE_ESC, MOVIE_WEBM_RE_JSON, MOVIE_WEBM_RE):
            for m in rx.finditer(text):
                appid, h = m.group(1), m.group(2).lower()
                key = _item_key(appid, h)
                if key in small_keys:
                    continue
                # Skip if this match is actually a _small field (belt & suspenders)
                start = max(0, m.start() - 24)
                snippet = text[start : m.start()]
                if "small" in snippet.lower():
                    continue
                _add_equipped_item(found, appid, h, source="localconfig")
        for m in MOVIE_MP4_RE.finditer(text):
            start = max(0, m.start() - 24)
            if "small" in text[start : m.start()].lower():
                continue
            appid, h = m.group(1), m.group(2).lower()
            key = _item_key(appid, h)
            if key in found:
                continue
            found[key] = {
                "appid": appid,
                "hash": h,
                "url": f"{CDN_BASES[0]}/{appid}/{h}.mp4",
                "ext": "mp4",
                "source_file": None,
                "body_size": 0,
                "name": "",
                "source": "localconfig",
                "slot": "profile_background",
                "equipped": True,
            }
    # Remove any that are known small hashes
    for k in list(found.keys()):
        if k in small_keys:
            del found[k]
    return found


def discover_equipped_from_api(
    steamids: Optional[Iterable[str]] = None,
    *,
    timeout: float = 15.0,
) -> dict[str, dict]:
    """Public IPlayerService/GetProfileItemsEquipped (no API key)."""
    found: dict[str, dict] = {}
    ids = list(steamids) if steamids is not None else find_login_steamids()
    slots = (
        ("profile_background", "profile_background"),
        ("mini_profile_background", "mini_profile_background"),
    )
    for sid in ids:
        url = (
            "https://api.steampowered.com/IPlayerService/GetProfileItemsEquipped/v1/"
            f"?steamid={sid}"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Wallora/0.2 (Steam equipped profile items)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            continue
        response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(response, dict):
            continue
        for field_name, slot in slots:
            item = response.get(field_name)
            if not isinstance(item, dict):
                continue
            movie = item.get("movie_webm") or item.get("movie_mp4") or ""
            m = re.match(r"items/(\d+)/([a-f0-9]+)\.(webm|mp4)", str(movie), re.I)
            if not m:
                continue
            name = str(item.get("item_title") or item.get("name") or "")
            ext = m.group(3).lower()
            if ext == "webm":
                _add_equipped_item(
                    found,
                    m.group(1),
                    m.group(2),
                    name=name,
                    source="api",
                    slot=slot,
                )
            else:
                appid, h = m.group(1), m.group(2).lower()
                key = _item_key(appid, h)
                found[key] = {
                    "appid": appid,
                    "hash": h,
                    "url": f"{CDN_BASES[0]}/{appid}/{h}.mp4",
                    "ext": "mp4",
                    "source_file": None,
                    "body_size": 0,
                    "name": name,
                    "source": "api",
                    "slot": slot,
                    "equipped": True,
                }
    return found


def discover_equipped_items(*, include_history: bool = True) -> dict[str, dict]:
    """
    Currently equipped profile backgrounds + previously seen equipped ones.

    Sources: localconfig.vdf, Steam Web API, local equipped_history.json.
    """
    found: dict[str, dict] = {}
    for src in (
        discover_equipped_from_localconfig(),
        discover_equipped_from_api(),
    ):
        for key, info in src.items():
            if key not in found:
                found[key] = info
            else:
                if info.get("name") and not found[key].get("name"):
                    found[key]["name"] = info["name"]
                found[key]["equipped"] = True

    # Persist newly seen equipped items into history
    history = _load_equipped_history()
    for key, info in found.items():
        hist = history.get(key) or {}
        hist.update(
            {
                "appid": info["appid"],
                "hash": info["hash"],
                "url": info.get("url") or f"{CDN_BASES[0]}/{info['appid']}/{info['hash']}.webm",
                "name": info.get("name") or hist.get("name") or "",
                "slot": info.get("slot") or hist.get("slot") or "profile_background",
                "source": info.get("source") or hist.get("source") or "equipped",
            }
        )
        history[key] = hist
    if found:
        _save_equipped_history(history)

    if include_history:
        for key, hist in history.items():
            if key in found:
                continue
            appid, h = hist.get("appid"), hist.get("hash")
            if not appid or not h:
                continue
            _add_equipped_item(
                found,
                str(appid),
                str(h),
                name=str(hist.get("name") or ""),
                source="history",
                slot=str(hist.get("slot") or "profile_background"),
            )
    return found


def _aes128_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> Optional[bytes]:
    try:
        from Cryptodome.Cipher import AES

        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES

        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    openssl = shutil.which("openssl")
    if not openssl:
        return None
    try:
        out = subprocess.check_output(
            [
                openssl, "enc", "-aes-128-cbc", "-d", "-nopad",
                "-K", key.hex(),
                "-iv", iv.hex(),
            ],
            input=data,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _decrypt_chrome_v10(blob: bytes) -> str:
    if not blob.startswith(b"v10") or len(blob) <= 3:
        return ""
    key = __import__("hashlib").pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    plain = _aes128_cbc_decrypt(key, b" " * 16, blob[3:])
    if not plain:
        return ""
    pad = plain[-1]
    if isinstance(pad, int) and 1 <= pad <= 16:
        plain = plain[:-pad]
    return plain.decode("utf-8", "ignore")


def read_steam_community_cookies() -> dict[str, str]:
    """Read steamLoginSecure from the Steam client's Chromium cookie DB."""
    cookies: dict[str, str] = {}
    for root in steam_root_candidates():
        db = root / "config/htmlcache/Default/Cookies"
        if not db.is_file():
            continue
        tmp = None
        try:
            import os
            import sqlite3
            import tempfile

            fd, tmp_name = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            tmp = Path(tmp_name)
            shutil.copy2(db, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT name, value, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%steamcommunity%'"
            ).fetchall()
            con.close()
            for name, value, enc in rows:
                if value:
                    cookies[str(name)] = str(value)
                elif enc:
                    cookies[str(name)] = _decrypt_chrome_v10(bytes(enc))
        except Exception:
            continue
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if cookies.get("steamLoginSecure"):
            return cookies
    return cookies


def discover_from_inventory(
    steamids: Optional[Iterable[str]] = None,
    *,
    timeout: float = 25.0,
) -> dict[str, dict]:
    """Owned Steam profile backgrounds via community inventory (needs Steam login cookie)."""
    now = time.monotonic()
    cached = _INVENTORY_CACHE.get("items")
    if cached is not None and now - float(_INVENTORY_CACHE.get("at") or 0) < 180:
        return dict(cached)

    found: dict[str, dict] = {}
    cookies = read_steam_community_cookies()
    token = cookies.get("steamLoginSecure") or ""
    if "||" not in token:
        return found
    ids = list(steamids) if steamids is not None else find_login_steamids()
    if not ids:
        sid = token.split("||", 1)[0]
        if sid.isdigit():
            ids = [sid]
    cookie_header = "; ".join(
        f"{k}={v}" for k, v in cookies.items() if k in ("steamLoginSecure", "sessionid")
    )

    for sid in ids:
        start = ""
        for _page in range(8):
            url = (
                f"https://steamcommunity.com/inventory/{sid}/753/6"
                f"?l=english&count=2000"
            )
            if start:
                url += f"&start_assetid={start}"
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Wallora/0.2 (Steam inventory import)",
                        "Accept": "application/json",
                        "Cookie": cookie_header,
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ):
                break
            if not isinstance(data, dict) or not data.get("success"):
                break
            for desc in data.get("descriptions") or []:
                if not isinstance(desc, dict):
                    continue
                blob = json.dumps(desc)
                name = str(desc.get("name") or "")
                tags = desc.get("tags") or []
                internals = {
                    str(t.get("internal_name") or "")
                    for t in tags
                    if isinstance(t, dict)
                }
                is_background = "item_class_3" in internals or "item_class_13" in internals
                if not is_background and "background" not in str(desc.get("type") or "").lower():
                    continue
                for m in ITEM_IMAGE_ACTION_RE.finditer(blob):
                    appid, h, ext = m.group(1), m.group(2).lower(), m.group(3).lower()
                    key = _item_key(appid, h)
                    found[key] = {
                        "appid": appid,
                        "hash": h,
                        "url": m.group(0),
                        "ext": "jpg" if ext == "jpeg" else ext,
                        "source_file": None,
                        "body_size": 0,
                        "name": name,
                        "source": "inventory",
                        "slot": "profile_background",
                        "equipped": False,
                    }
                for rx, ext in (
                    (MOVIE_WEBM_RE, "webm"),
                    (MOVIE_WEBM_RE_JSON, "webm"),
                    (MOVIE_MP4_RE, "mp4"),
                ):
                    for m in rx.finditer(blob):
                        appid, h = m.group(1), m.group(2).lower()
                        key = _item_key(appid, h)
                        if key in found and found[key].get("ext") in VIDEO_EXTS:
                            continue
                        found[key] = {
                            "appid": appid,
                            "hash": h,
                            "url": f"{CDN_BASES[0]}/{appid}/{h}.{ext}",
                            "ext": ext,
                            "source_file": None,
                            "body_size": 0,
                            "name": name,
                            "source": "inventory",
                            "slot": "mini_profile_background" if "item_class_13" in internals else "profile_background",
                            "equipped": False,
                        }
            if not data.get("more_items"):
                break
            start = str(data.get("last_assetid") or "")
            if not start:
                break
    if found:
        _INVENTORY_CACHE["items"] = found
        _INVENTORY_CACHE["at"] = time.monotonic()
    return found


def probe_video(path: Path) -> Optional[tuple[int, int]]:
    """Return (width, height) or None if unreadable."""
    if not path.is_file() or path.stat().st_size < MIN_BYTES_ANY:
        return None
    if not shutil.which("ffprobe"):
        # Fallback: size heuristic only
        return (1920, 1080) if path.stat().st_size >= MIN_BYTES_HIGH else (300, 168)
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        ).strip()
        if "x" not in out:
            return None
        w_s, h_s = out.split("x", 1)
        w, h = int(w_s), int(h_s.split("x")[0] if "x" in h_s else h_s)
        if w < 16 or h < 16:
            return None
        return w, h
    except (subprocess.CalledProcessError, ValueError, OSError, subprocess.TimeoutExpired):
        return None


def is_high_quality(path: Path, *, min_w: int = MIN_WIDTH_HIGH, min_h: int = MIN_HEIGHT_HIGH) -> bool:
    if path.stat().st_size < MIN_BYTES_HIGH:
        # Allow slightly smaller files if resolution is HD
        dim = probe_video(path)
        if not dim:
            return False
        w, h = dim
        return w >= min_w and h >= min_h
    dim = probe_video(path)
    if not dim:
        return path.stat().st_size >= MIN_BYTES_HIGH
    w, h = dim
    return w >= min_w and h >= min_h


def is_shop_preview(path: Path) -> bool:
    """True for Point Shop 300×168 tiles — not usable as a wallpaper."""
    if not path.is_file():
        return True
    size = path.stat().st_size
    if size < SHOP_PREVIEW_MAX_BYTES:
        return True
    if path.suffix.lower() in IMAGE_EXTS:
        return size < 8_000
    dim = probe_video(path)
    if not dim:
        return size < MIN_BYTES_HIGH
    return dim[0] <= SHOP_PREVIEW_MAX_W and dim[1] <= SHOP_PREVIEW_MAX_H


def _extract_webm_body(data: bytes) -> Optional[bytes]:
    i = data.find(WEBM_MAGIC)
    if i < 0:
        return None
    body = data[i:]
    if len(body) < MIN_BYTES_ANY:
        return None
    return body


def discover_from_cache(
    cache_dirs: Optional[Iterable[Path]] = None,
) -> dict[str, dict]:
    """
    Scan Steam htmlcache for profile-item webms.

    Returns dict keyed by appid_hash with url, appid, hash, source_file, body_size.
    Does **not** keep full bodies in memory — only path + size of best local copy.
    """
    if cache_dirs is None:
        cache_dirs = find_htmlcache_dirs()

    found: dict[str, dict] = {}

    for cdir in cache_dirs:
        try:
            entries = list(cdir.iterdir())
        except OSError:
            continue

        for path in entries:
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < 200 or size > 80_000_000:
                continue

            try:
                with path.open("rb") as f:
                    head = f.read(min(size, 16_000))
                    if not (
                        WEBM_MAGIC in head
                        or b"images/items/" in head
                        or b".webm" in head
                        or b"movie_webm" in head
                        or b"movie_mp4" in head
                    ):
                        continue
                    need_full = size <= 12_000_000
                    full = head
                    if need_full and size > len(head):
                        f.seek(0)
                        full = f.read()
            except OSError:
                continue

            text_head = full[:64_000].decode("utf-8", "ignore")
            for rx, ext in (
                (MOVIE_WEBM_RE, "webm"),
                (MOVIE_WEBM_RE_JSON, "webm"),
                (MOVIE_WEBM_RE_ESC, "webm"),
                (MOVIE_MP4_RE, "mp4"),
            ):
                for m in rx.finditer(text_head):
                    start = max(0, m.start() - 24)
                    if "small" in text_head[start : m.start()].lower():
                        continue
                    appid, h = m.group(1), m.group(2).lower()
                    key = _item_key(appid, h)
                    url = f"{CDN_BASES[0]}/{appid}/{h}.{ext}"
                    entry = found.get(key)
                    if entry is None:
                        found[key] = {
                            "appid": appid,
                            "hash": h,
                            "url": url,
                            "ext": ext,
                            "source_file": None,
                            "body_size": 0,
                        }
                    elif ext == "webm" and entry.get("ext") != "webm":
                        entry["ext"] = "webm"
                        entry["url"] = url

            url_matches = list(ITEM_WEBM_URL_RE.finditer(full[:16_000]))
            path_matches = list(ITEM_WEBM_PATH_RE.finditer(full[:16_000])) if not url_matches else []
            matches = url_matches or path_matches

            body = _extract_webm_body(full) if len(full) > len(head) or WEBM_MAGIC in full else None
            body_size = len(body) if body else 0

            if not matches and not body:
                continue

            # If we only have a body with URL in same file header
            for m in matches:
                appid = m.group(1).decode("ascii")
                h = m.group(2).decode("ascii")
                key = _item_key(appid, h)
                raw = m.group(0).decode("utf-8", "ignore")
                url = raw if raw.startswith("http") else f"{CDN_BASES[0]}/{appid}/{h}.webm"

                entry = found.get(key)
                if entry is None:
                    found[key] = {
                        "appid": appid,
                        "hash": h,
                        "url": url,
                        "ext": "webm",
                        "source_file": str(path) if body_size else None,
                        "body_size": body_size,
                    }
                else:
                    if body_size > int(entry.get("body_size") or 0):
                        entry["source_file"] = str(path)
                        entry["body_size"] = body_size
                    if url.startswith("http") and not str(entry.get("url", "")).startswith("http"):
                        entry["url"] = url

    return found


def _download(url: str, dest: Path, timeout: float = 45.0) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Wallora/0.2 (Steam profile wallpaper import)",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        suffix = dest.suffix.lower()
        if suffix == ".webm":
            if not data.startswith(WEBM_MAGIC) or len(data) < MIN_BYTES_ANY:
                return False
        elif suffix == ".mp4":
            if b"ftyp" not in data[:16] or len(data) < MIN_BYTES_ANY:
                return False
        elif suffix in IMAGE_EXTS:
            if len(data) < 2_000:
                return False
        elif len(data) < MIN_BYTES_ANY:
            return False
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _extract_from_cache_file(source: Path, dest: Path) -> bool:
    try:
        data = source.read_bytes()
    except OSError:
        return False
    body = _extract_webm_body(data)
    if not body:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(body)
        # Validate before commit
        if probe_video(tmp) is None and len(body) < MIN_BYTES_HIGH:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _candidate_urls(info: dict) -> list[str]:
    urls: list[str] = []
    u = info.get("url") or ""
    if isinstance(u, str) and u.startswith("http"):
        urls.append(u)
    appid, h = info["appid"], info["hash"]
    ext = _item_ext(info)
    for base in CDN_BASES:
        cand = f"{base}/{appid}/{h}.{ext}"
        if cand not in urls:
            urls.append(cand)
    return urls


def _import_item_list(
    items: dict[str, dict],
    dest: Path,
    result: SteamImportResult,
    *,
    high_only: bool,
    prefer_cdn: bool,
    force: bool,
    log: Callable[[str], None],
    equipped_mode: bool = False,
) -> None:
    """Download/extract a dict of discovered items into dest, update result."""
    ordered = sorted(
        items.items(),
        key=lambda kv: (
            1 if kv[1].get("equipped") else 0,
            int(kv[1].get("body_size") or 0),
        ),
        reverse=True,
    )

    for key, info in ordered:
        appid = info["appid"]
        h = info["hash"]
        out = _dest_path(dest, appid, h, _item_ext(info))
        if out.suffix.lower() in IMAGE_EXTS:
            existing_ok = out.exists() and out.stat().st_size >= 2_000
        else:
            existing_ok = out.exists() and probe_video(out) is not None
        # Equipped full movie_webm: keep if playable; cache scan still uses HD gate
        if equipped_mode:
            existing_hq = existing_ok
        else:
            existing_hq = existing_ok and (not high_only or not is_shop_preview(out))

        if existing_hq and not force:
            result.skipped += 1
            continue

        got = False
        from_cdn = False
        from_cache = False

        if prefer_cdn or equipped_mode:
            for url in _candidate_urls(info):
                log(f"  CDN {appid}/{h[:8]}…")
                if _download(url, out):
                    from_cdn = True
                    got = True
                    break

        if not got and info.get("source_file"):
            src = Path(info["source_file"])
            if src.is_file():
                log(f"  cache {appid}/{h[:8]}…")
                if _extract_from_cache_file(src, out):
                    from_cache = True
                    got = True

        if not got and not prefer_cdn and not equipped_mode:
            for url in _candidate_urls(info):
                if _download(url, out):
                    from_cdn = True
                    got = True
                    break

        if not got or not out.exists():
            result.failed += 1
            name = info.get("name") or f"{appid}/{h[:12]}"
            result.errors.append(f"Nie udało się: {name}")
            continue

        # Quality gate: drop Point Shop 300×168 tiles, keep real wallpapers
        if high_only and not equipped_mode and is_shop_preview(out):
            result.filtered_low_quality += 1
            try:
                if not existing_hq:
                    out.unlink(missing_ok=True)
            except OSError:
                pass
            log(f"  × podgląd sklepu (pominięto) {out.name}")
            continue

        # Soft gate for equipped: drop only if unreadable or tiny shop preview
        if equipped_mode and high_only:
            dim = probe_video(out)
            if dim and dim[0] < 400 and dim[1] < 300 and out.stat().st_size < MIN_BYTES_HIGH:
                # Likely wrongly matched small asset
                result.filtered_low_quality += 1
                try:
                    if not existing_ok:
                        out.unlink(missing_ok=True)
                except OSError:
                    pass
                log(f"  × equipped wygląda na podgląd — pominięto {out.name}")
                continue

        dim = probe_video(out)
        label = f"{dim[0]}x{dim[1]}" if dim else "?"
        name = info.get("name") or out.name
        if existing_ok and not existing_hq:
            result.upgraded += 1
            log(f"  ↑ uaktualniono {name} ({label})")
        elif from_cdn:
            result.downloaded += 1
            if equipped_mode:
                result.equipped_imported += 1
            log(f"  ✓ CDN → {name} ({label})")
        elif from_cache:
            result.extracted += 1
            if equipped_mode:
                result.equipped_imported += 1
            log(f"  ✓ cache → {name} ({label})")
        else:
            result.downloaded += 1
            if equipped_mode:
                result.equipped_imported += 1


def import_equipped_profile_wallpapers(
    dest_dir: Optional[Path] = None,
    *,
    force: bool = False,
    progress: ProgressCb = None,
    include_history: bool = True,
) -> SteamImportResult:
    """
    Import currently equipped Steam profile background(s) (+ history).

    Steam writes movie_webm paths into localconfig when you save a profile
    wallpaper; we also hit the public equipped-items API and remember every
    hash ever seen so cycling through wallpapers builds your library.
    """
    dest = Path(dest_dir) if dest_dir else STEAM_PROFILES_DIR
    dest.mkdir(parents=True, exist_ok=True)
    result = SteamImportResult(dest_dir=dest)

    def log(msg: str):
        if progress:
            progress(msg)

    items = discover_equipped_items(include_history=include_history)
    result.equipped_found = len(items)
    result.discovered = len(items)
    if not items:
        log("Brak ustawionego tła profilu Steam (localconfig/API).")
        result.errors.append(
            "Nie znaleziono ustawionego tła profilu. W Steam: Edycja profilu → "
            "tło → zapisz, potem uruchom Wallorę ponownie (auto-import)."
        )
        return result

    log(f"Ustawione / history teł profilu: {len(items)}")
    for info in items.values():
        label = info.get("name") or f"{info['appid']}/{info['hash'][:8]}"
        log(f"  • {label} [{info.get('source', '?')}]")

    _import_item_list(
        items,
        dest,
        result,
        high_only=True,
        prefer_cdn=True,
        force=force,
        log=log,
        equipped_mode=True,
    )
    log(
        f"Equipped: +{result.equipped_imported} nowych, "
        f"pominięto {result.skipped}, błędów {result.failed}"
    )
    return result


def import_steam_profile_wallpapers(
    dest_dir: Optional[Path] = None,
    *,
    quality: str = "high",
    prefer_cdn: bool = True,
    force: bool = False,
    progress: ProgressCb = None,
    include_equipped: bool = True,
    include_cache: bool = True,
    include_inventory: bool = True,
) -> SteamImportResult:
    """
    Import Steam profile backgrounds (animated + owned stills).

    quality:
      - ``high`` (default): skip Point Shop 300×168 tiles
      - ``all``: also keep small previews
    prefer_cdn:
      Try CDN first for cleaner full-res files; fall back to cache extract.
    include_equipped:
      Always try currently equipped + history first (recommended).
    include_cache:
      Also scan Steam htmlcache for shop/profile previews.
    include_inventory:
      Owned profile backgrounds via Steam client login cookie.
    """
    dest = Path(dest_dir) if dest_dir else STEAM_PROFILES_DIR
    dest.mkdir(parents=True, exist_ok=True)
    result = SteamImportResult(dest_dir=dest)
    high_only = quality != "all"

    def log(msg: str):
        if progress:
            progress(msg)

    # 1) Equipped profile wallpapers (full movie_webm hashes)
    if include_equipped:
        eq = import_equipped_profile_wallpapers(
            dest,
            force=force,
            progress=progress,
            include_history=True,
        )
        _merge_result(result, eq)

    seen_keys = {
        _item_key(i["appid"], i["hash"])
        for i in discover_equipped_items(include_history=True).values()
    }

    # 2) Owned inventory (static + animated), using Steam client cookies
    if include_inventory:
        log("Ekwipunek Steam (tła profilu)…")
        inv = discover_from_inventory()
        inv = {k: v for k, v in inv.items() if k not in seen_keys}
        result.discovered += len(inv)
        log(f"Ekwipunek: {len(inv)} teł")
        if inv:
            _import_item_list(
                inv,
                dest,
                result,
                high_only=False,
                prefer_cdn=True,
                force=force,
                log=log,
                equipped_mode=True,
            )
            seen_keys.update(inv)
        else:
            log("Brak teł w ekwipunku (zaloguj się w kliencie Steam).")

    # 3) Cache / CDN scan (Point Shop previews → HD when available)
    if include_cache:
        cache_dirs = find_htmlcache_dirs()
        if cache_dirs:
            log(f"Cache Steam: {', '.join(str(p) for p in cache_dirs)}")
        else:
            log("Nie znaleziono Steam htmlcache.")

        items = discover_from_cache(cache_dirs)
        items = {k: v for k, v in items.items() if k not in seen_keys}

        result.discovered += len(items)
        log(f"Znaleziono {len(items)} unikalnych assetów .webm w cache/URL")

        if items:
            _import_item_list(
                items,
                dest,
                result,
                high_only=high_only,
                prefer_cdn=prefer_cdn,
                force=force,
                log=log,
                equipped_mode=False,
            )
        elif not include_equipped or result.equipped_found == 0:
            result.errors.append(
                "Brak teł w cache. Ustaw tło w profilu Steam (auto-import) albo "
                "otwórz Points Shop → Backgrounds (podgląd pełnoekranowy)."
            )

    # Remove leftover low-quality files from older imports when high_only
    if high_only:
        keep_equipped = {
            _dest_path(dest, i["appid"], i["hash"], _item_ext(i)).name
            for i in discover_equipped_items(include_history=True).values()
        }
        for p in list(dest.glob("steam_*.webm")) + list(dest.glob("steam_*.mp4")):
            if p.name in keep_equipped:
                continue
            if p.is_file() and is_shop_preview(p):
                try:
                    p.unlink()
                    log(f"  usunięto stary podgląd {p.name}")
                except OSError:
                    pass

    log(result.summary())
    return result


def ensure_library_folder(config, dest_dir: Optional[Path] = None) -> Path:
    dest = Path(dest_dir) if dest_dir else STEAM_PROFILES_DIR
    dest.mkdir(parents=True, exist_ok=True)
    path = str(dest.resolve())
    folders = config.get("library_folders", []) or []
    if path not in folders:
        config.add_library_folder(path)
    return dest


def import_and_register(
    config,
    *,
    quality: str = "high",
    force: bool = False,
    progress: ProgressCb = None,
    include_equipped: bool = True,
    include_cache: bool = True,
    include_inventory: bool = True,
) -> SteamImportResult:
    dest = ensure_library_folder(config)
    return import_steam_profile_wallpapers(
        dest,
        quality=quality,
        force=force,
        progress=progress,
        include_equipped=include_equipped,
        include_cache=include_cache,
        include_inventory=include_inventory,
    )


def auto_import_equipped_quiet(config) -> SteamImportResult:
    """Background helper: equipped + history only (no full cache scan)."""
    dest = ensure_library_folder(config)
    return import_equipped_profile_wallpapers(
        dest,
        force=False,
        progress=None,
        include_history=True,
    )
