"""KDE Plasma 6 wallpaper helpers.

Plasma draws the desktop itself. An X11 ``_NET_WM_WINDOW_TYPE_DESKTOP``
mpv window ends up *behind* plasmashell and is invisible. Static and
live wallpapers must go through ``org.kde.PlasmaShell.setWallpaper``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from gi.repository import Gio, GLib

SMART_VIDEO_PLUGIN = "luisbocanegra.smart.video.wallpaper.reborn"
WALLORA_VIDEO_PLUGIN = "org.wallora.video"
IMAGE_PLUGIN = "org.kde.image"

VIDEO_PLUGINS = (SMART_VIDEO_PLUGIN, WALLORA_VIDEO_PLUGIN)

PLASMA_BUS = "org.kde.plasmashell"
PLASMA_PATH = "/PlasmaShell"
PLASMA_IFACE = "org.kde.PlasmaShell"

# Qt / org.kde.image FillMode
_FILLMODE = {
    "stretch": 0,
    "fit": 1,
    "fill": 2,
    "tile": 3,
    "center": 6,
    "span": 2,
    "fit_blur": 0,
    "center_blur": 0,
}

_PLUGIN_METADATA = """{
    "KPackageStructure": "Plasma/Wallpaper",
    "KPlugin": {
        "Authors": [{"Name": "Wallora"}],
        "Description": "Video wallpaper from Wallora",
        "Description[pl]": "Animowana tapeta Wallory",
        "Icon": "preferences-desktop-wallpaper",
        "Id": "org.wallora.video",
        "License": "MIT",
        "Name": "Wallora Video",
        "Name[pl]": "Wallora — wideo",
        "Version": "1.0",
        "Website": "https://github.com/GorianWaco/wallora-v2"
    },
    "Keywords": ["video", "wallpaper", "wallora"],
    "X-KDE-ParentApp": "org.kde.plasmashell",
    "X-Plasma-API-Minimum-Version": "6.0"
}
"""

_PLUGIN_CONFIG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<kcfg xmlns="http://www.kde.org/standards/kcfg/1.0"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:schemaLocation="http://www.kde.org/standards/kcfg/1.0
      http://www.kde.org/standards/kcfg/1.0/kcfg.xsd">
  <kcfgfile name=""/>
  <group name="General">
    <entry name="Video" type="String">
      <label>Video file</label>
      <default></default>
    </entry>
    <entry name="Muted" type="Bool">
      <default>true</default>
    </entry>
    <entry name="Loop" type="Bool">
      <default>true</default>
    </entry>
    <entry name="FillMode" type="Int">
      <default>2</default>
    </entry>
  </group>
</kcfg>
"""

_PLUGIN_MAIN_QML = r"""import QtQuick
import QtMultimedia
import org.kde.plasma.plasmoid

WallpaperItem {
    id: root

    readonly property url videoSource: {
        const v = (root.configuration.Video || "").toString()
        if (!v)
            return ""
        if (v.indexOf("file:") === 0 || v.indexOf("http:") === 0 || v.indexOf("https:") === 0)
            return v
        return "file://" + v
    }

    Rectangle {
        anchors.fill: parent
        color: "black"
        z: -1
    }

    VideoOutput {
        id: videoOut
        anchors.fill: parent
        fillMode: {
            const m = root.configuration.FillMode
            if (m === 0)
                return VideoOutput.Stretch
            if (m === 1)
                return VideoOutput.PreserveAspectFit
            return VideoOutput.PreserveAspectCrop
        }
    }

    MediaPlayer {
        id: player
        source: root.videoSource
        videoOutput: videoOut
        autoPlay: true
        loops: root.configuration.Loop !== false ? MediaPlayer.Infinite : 1
        audioOutput: AudioOutput {
            muted: root.configuration.Muted !== false
            volume: root.configuration.Muted !== false ? 0.0 : 1.0
        }
        onSourceChanged: {
            if (source.toString() !== "")
                play()
        }
        onPlaybackStateChanged: {
            if (root.configuration.Loop !== false && playbackState === MediaPlayer.StoppedState && source.toString() !== "")
                play()
        }
    }
}
"""

_PLUGIN_CONFIG_QML = r"""import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_Video: videoField.text
    property bool cfg_Muted
    property bool cfg_Loop
    property int cfg_FillMode

    Kirigami.FormLayout {
        QQC2.TextField {
            id: videoField
            Kirigami.FormData.label: "Video"
        }
        QQC2.CheckBox {
            id: muteBox
            Kirigami.FormData.label: "Mute"
            checked: cfg_Muted
            onToggled: cfg_Muted = checked
        }
        QQC2.CheckBox {
            id: loopBox
            Kirigami.FormData.label: "Loop"
            checked: cfg_Loop
            onToggled: cfg_Loop = checked
        }
    }
}
"""


def is_kde() -> bool:
    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or ""
    ).lower()
    return "kde" in desktop or "plasma" in desktop


def fill_mode(scaling: str | None) -> int:
    key = (scaling or "fill").lower()
    return _FILLMODE.get(key, 2)


def _file_uri(path: Path | str) -> str:
    path = Path(path).expanduser().resolve()
    return path.as_uri()


def _proxy() -> Optional[Gio.DBusProxy]:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            PLASMA_BUS,
            PLASMA_PATH,
            PLASMA_IFACE,
            None,
        )
    except Exception as e:
        print("Plasma D-Bus proxy failed:", e)
        return None


def plasmashell_available() -> bool:
    proxy = _proxy()
    if proxy is None:
        return False
    try:
        proxy.call_sync("wallpaper", GLib.Variant("(u)", (0,)), Gio.DBusCallFlags.NONE, 2000, None)
        return True
    except Exception:
        return False


def wallpaper_config(screen: int = 0) -> dict[str, Any]:
    proxy = _proxy()
    if proxy is None:
        return {}
    try:
        res = proxy.call_sync(
            "wallpaper",
            GLib.Variant("(u)", (int(screen),)),
            Gio.DBusCallFlags.NONE,
            4000,
            None,
        )
        data = res.unpack()[0]
        return dict(data) if data else {}
    except Exception:
        return {}


def screen_indices() -> list[int]:
    found: list[int] = []
    for i in range(8):
        cfg = wallpaper_config(i)
        if not cfg:
            break
        found.append(i)
    return found or [0]


def _set_wallpaper(plugin: str, params: dict[str, GLib.Variant], screens: list[int] | None = None) -> bool:
    proxy = _proxy()
    if proxy is None:
        return False
    ok = False
    for screen in screens or screen_indices():
        try:
            proxy.call_sync(
                "setWallpaper",
                GLib.Variant("(sa{sv}u)", (plugin, params, int(screen))),
                Gio.DBusCallFlags.NONE,
                8000,
                None,
            )
            ok = True
        except Exception as e:
            print(f"Plasma setWallpaper screen {screen} failed:", e)
    return ok


def unique_image_copy(src: Path, cache: Path) -> Path:
    """Plasma ignores a wallpaper change if the path (not bytes) is unchanged."""
    import hashlib

    src = Path(src).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1()
    try:
        st = src.stat()
        digest.update(str(st.st_size).encode())
        digest.update(str(int(st.st_mtime_ns)).encode())
        with src.open("rb") as fh:
            digest.update(fh.read(256 * 1024))
    except OSError:
        digest.update(str(src).encode())
    suffix = src.suffix.lower() if src.suffix else ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}:
        suffix = ".jpg"
    dest = cache / f"kde_{digest.hexdigest()[:14]}{suffix}"
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)
    for old in cache.glob("kde_*"):
        if old == dest:
            continue
        try:
            if old.stat().st_mtime < dest.stat().st_mtime - 3600:
                old.unlink()
        except OSError:
            pass
    return dest


def set_image(path: Path | str, scaling: str = "fill", *, force_animation: bool = False) -> bool:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return False
    uri = _file_uri(path)
    params = {
        "Image": GLib.Variant("s", uri),
        "FillMode": GLib.Variant("i", fill_mode(scaling)),
        "PreviewImage": GLib.Variant("s", ""),
        "ForceImageAnimation": GLib.Variant("b", bool(force_animation)),
    }
    if not _set_wallpaper(IMAGE_PLUGIN, params):
        if not _set_image_cli(path, scaling) and not _set_image_script(path, scaling):
            return False
    cfg = wallpaper_config(0)
    image = str(cfg.get("Image") or "")
    return path.name in image or uri in image or bool(cfg)


def _fill_mode_cli(scaling: str) -> str:
    return {
        "stretch": "stretch",
        "fit": "preserveAspectFit",
        "fill": "preserveAspectCrop",
        "center": "pad",
        "tile": "tile",
        "span": "preserveAspectCrop",
        "fit_blur": "stretch",
        "center_blur": "stretch",
    }.get((scaling or "fill").lower(), "preserveAspectCrop")


def _set_image_cli(path: Path, scaling: str) -> bool:
    tool = shutil.which("plasma-apply-wallpaperimage")
    if not tool:
        return False
    cmd = [tool, "-f", _fill_mode_cli(scaling), str(path)]
    try:
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        return rc.returncode == 0
    except Exception:
        return False


def _evaluate_script(script: str) -> bool:
    proxy = _proxy()
    if proxy is not None:
        try:
            proxy.call_sync(
                "evaluateScript",
                GLib.Variant("(s)", (script,)),
                Gio.DBusCallFlags.NONE,
                8000,
                None,
            )
            return True
        except Exception as e:
            print("Plasma evaluateScript failed:", e)
    for bin_name in ("qdbus6", "qdbus"):
        exe = shutil.which(bin_name)
        if not exe:
            continue
        try:
            rc = subprocess.run(
                [exe, PLASMA_BUS, PLASMA_PATH, f"{PLASMA_IFACE}.evaluateScript", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
            )
            if rc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _set_image_script(path: Path, scaling: str) -> bool:
    uri = _file_uri(path)
    mode = fill_mode(scaling)
    script = f"""
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {{
    var d = allDesktops[i];
    d.wallpaperPlugin = "{IMAGE_PLUGIN}";
    d.currentConfigGroup = Array("Wallpaper", "{IMAGE_PLUGIN}", "General");
    d.writeConfig("FillMode", "{mode}");
    d.writeConfig("PreviewImage", "");
    d.writeConfig("Image", "{uri}");
}}
"""
    return _evaluate_script(script)


def plugin_installed(plugin_id: str) -> bool:
    name = plugin_id
    roots = [
        Path.home() / ".local/share/plasma/wallpapers" / name,
        Path("/usr/share/plasma/wallpapers") / name,
        Path("/usr/local/share/plasma/wallpapers") / name,
    ]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.insert(0, Path(xdg) / "plasma/wallpapers" / name)
    return any((root / "metadata.json").is_file() for root in roots)


def install_wallora_video_plugin() -> Path:
    dest = Path.home() / ".local/share/plasma/wallpapers" / WALLORA_VIDEO_PLUGIN
    (dest / "contents" / "ui").mkdir(parents=True, exist_ok=True)
    (dest / "contents" / "config").mkdir(parents=True, exist_ok=True)
    files = {
        dest / "metadata.json": _PLUGIN_METADATA,
        dest / "contents" / "config" / "main.xml": _PLUGIN_CONFIG_XML,
        dest / "contents" / "ui" / "main.qml": _PLUGIN_MAIN_QML,
        dest / "contents" / "ui" / "config.qml": _PLUGIN_CONFIG_QML,
    }
    for path, content in files.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != content:
            path.write_text(content, encoding="utf-8")
    _refresh_sycoca()
    return dest


def _refresh_sycoca() -> None:
    exe = shutil.which("kbuildsycoca6") or shutil.which("kbuildsycoca5")
    if not exe:
        return
    try:
        subprocess.run([exe, "--noincremental"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass


def _smart_video_params(path: Path, *, mute: bool, loop: bool, scaling: str) -> dict[str, GLib.Variant]:
    uri = _file_uri(path)
    payload = json.dumps(
        [
            {
                "filename": uri,
                "enabled": True,
                "duration": 0,
                "customDuration": 0,
                "playbackRate": 0.0,
                "alternativePlaybackRate": 0.0,
                "loop": bool(loop),
            }
        ]
    )
    return {
        "VideoUrls": GLib.Variant("s", payload),
        "FillMode": GLib.Variant("i", fill_mode(scaling)),
        "MuteMode": GLib.Variant("i", 5 if mute else 4),
        "PauseMode": GLib.Variant("i", 0),
        "Volume": GLib.Variant("d", 0.0 if mute else 1.0),
        "ResumeLastVideo": GLib.Variant("b", False),
        "LastVideo": GLib.Variant("s", ""),
        "RandomMode": GLib.Variant("b", False),
    }


def _wallora_video_params(path: Path, *, mute: bool, loop: bool, scaling: str) -> dict[str, GLib.Variant]:
    return {
        "Video": GLib.Variant("s", _file_uri(path)),
        "Muted": GLib.Variant("b", bool(mute)),
        "Loop": GLib.Variant("b", bool(loop)),
        "FillMode": GLib.Variant("i", fill_mode(scaling)),
    }


def _video_matches(cfg: dict[str, Any], path: Path) -> bool:
    blob = json.dumps(cfg, default=str)
    return path.name in blob or str(path) in blob or _file_uri(path) in blob


def set_video(path: Path | str, *, mute: bool = True, loop: bool = True, scaling: str = "fill") -> tuple[bool, str]:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return False, f"Plik nie istnieje: {path}"

    ext = path.suffix.lower()
    if ext in {".gif", ".webp"}:
        if set_image(path, scaling=scaling, force_animation=True):
            return True, IMAGE_PLUGIN

    if plugin_installed(SMART_VIDEO_PLUGIN):
        if _set_wallpaper(SMART_VIDEO_PLUGIN, _smart_video_params(path, mute=mute, loop=loop, scaling=scaling)):
            cfg = wallpaper_config(0)
            if cfg.get("wallpaperPlugin") == SMART_VIDEO_PLUGIN and _video_matches(cfg, path):
                return True, SMART_VIDEO_PLUGIN

    try:
        install_wallora_video_plugin()
    except Exception as e:
        print("Wallora video plugin install failed:", e)

    if _set_wallpaper(WALLORA_VIDEO_PLUGIN, _wallora_video_params(path, mute=mute, loop=loop, scaling=scaling)):
        cfg = wallpaper_config(0)
        if cfg.get("wallpaperPlugin") == WALLORA_VIDEO_PLUGIN and _video_matches(cfg, path):
            return True, WALLORA_VIDEO_PLUGIN
        # Plugin may not be registered yet; Smart Video already tried.

    if plugin_installed(SMART_VIDEO_PLUGIN):
        return False, "Plasma przyjęła wtyczkę wideo, ale nie przełączyła tapety"
    return False, "Brak działającej wtyczki wideo Plasma (zainstaluj Smart Video Wallpaper albo zrestartuj sesję)"


def is_video_wallpaper_active(path: Path | str | None = None) -> bool:
    cfg = wallpaper_config(0)
    plugin = str(cfg.get("wallpaperPlugin") or "")
    if plugin not in VIDEO_PLUGINS:
        return False
    if path is None:
        video = str(cfg.get("Video") or cfg.get("VideoUrls") or "")
        return bool(video) and video not in ("[]", "")
    return _video_matches(cfg, Path(path))


def current_video_path() -> Optional[Path]:
    cfg = wallpaper_config(0)
    plugin = str(cfg.get("wallpaperPlugin") or "")
    if plugin == WALLORA_VIDEO_PLUGIN:
        raw = str(cfg.get("Video") or "")
        if raw.startswith("file://"):
            raw = raw[7:]
        if raw:
            p = Path(raw)
            return p if p.exists() else p
    if plugin == SMART_VIDEO_PLUGIN:
        raw = str(cfg.get("VideoUrls") or "[]")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            items = []
        if items and isinstance(items, list):
            filename = str(items[0].get("filename") or "")
            if filename.startswith("file://"):
                filename = filename[7:]
            if filename:
                return Path(filename)
    return None


def set_lock_screen_image(path: Path | str) -> bool:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return False
    uri = _file_uri(path)
    tool = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
    if not tool:
        return False
    base = [tool, "--file", "kscreenlockerrc"]
    cmds = [
        base + ["--group", "Greeter", "--key", "WallpaperPlugin", IMAGE_PLUGIN],
        base
        + [
            "--group", "Greeter",
            "--group", "Wallpaper",
            "--group", IMAGE_PLUGIN,
            "--group", "General",
            "--key", "Image",
            uri,
        ],
    ]
    ok = True
    for cmd in cmds:
        try:
            rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            ok = ok and rc.returncode == 0
        except Exception:
            ok = False
    return ok
