"""Animated (video/GIF) wallpaper backends for Wallora v2.

Backends (tried in order of suitability for the current session):
  1. mpvpaper  — Wayland wlroots (Hyprland, Sway, …)
  2. xwinwrap + mpv — classic X11
  3. built-in Gtk player window — GNOME/others (best-effort, keep-below window)
  4. mpv fullscreen fallback

Also extracts a poster frame via ffmpeg and sets it as the static DE wallpaper
so Overview / lock screen look good on GNOME.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from gi.repository import GLib

from wallora.utils import is_video_file, VIDEO_EXTENSIONS

MPV_DESKTOP_TITLE = "wallora-desktop-wallpaper"
MPV_X11_CLASS = "wallora-desktop"
PIN_MARKER = "--pin-desktop"


PID_FILE = Path(GLib.get_user_cache_dir()) / "wallora" / "animated.pid"
STATE_FILE = Path(GLib.get_user_cache_dir()) / "wallora" / "animated_state.json"
POSTER_PATH = Path(GLib.get_user_cache_dir()) / "wallora" / "animated_poster.jpg"
PIN_LOG = Path(GLib.get_user_cache_dir()) / "wallora" / "desktop_pin.log"
RESTORE_LOG = Path(GLib.get_user_cache_dir()) / "wallora" / "restore.log"
RESTORE_LOCK = Path(GLib.get_user_cache_dir()) / "wallora" / "restore.lock"


def _run(cmd: list[str], **kwargs) -> bool:
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def extract_poster_frame(video_path: Path, dest: Path | None = None) -> Optional[Path]:
    """Grab a mid/early frame from video for static wallpaper / thumbnail."""
    if dest is None:
        dest = POSTER_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not shutil.which("ffmpeg"):
        return None

    # Prefer a frame ~1s in (avoids black intro); fall back to first frame
    for seek in ("00:00:01.000", "00:00:00.100", "00:00:00.000"):
        cmd = [
            "ffmpeg", "-y",
            "-ss", seek,
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(dest),
        ]
        if _run(cmd) and dest.exists() and dest.stat().st_size > 0:
            return dest
    return None


class AnimatedWallpaperManager:
    """Start/stop animated wallpapers and track the active backend process."""

    def __init__(self):
        self.desktop = (
            os.environ.get("XDG_CURRENT_DESKTOP")
            or os.environ.get("DESKTOP_SESSION")
            or ""
        ).lower()
        self.session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        self._proc: Optional[subprocess.Popen] = None
        self._backend: Optional[str] = None
        self._current_path: Optional[Path] = None
        atexit.register(self._atexit_cleanup)

    # --- public API ---

    def available_backends(self) -> list[str]:
        backends = []
        if shutil.which("mpvpaper"):
            backends.append("mpvpaper")
        if shutil.which("xwinwrap") and shutil.which("mpv"):
            backends.append("xwinwrap+mpv")
        if shutil.which("mpv") and os.environ.get("DISPLAY"):
            backends.append("mpv-desktop")  # DESKTOP window + reliable loop
        backends.append("gtk-player")  # always available (GTK4 MediaFile)
        if shutil.which("mpv"):
            backends.append("mpv-window")
        return backends

    def preferred_backend(self) -> str:
        # wlroots family → mpvpaper is ideal (true wallpaper layer)
        if any(x in self.desktop for x in ("hyprland", "hypr", "sway", "river", "wayfire")):
            if shutil.which("mpvpaper"):
                return "mpvpaper"
        if self.session_type == "x11" and shutil.which("xwinwrap") and shutil.which("mpv"):
            return "xwinwrap+mpv"
        # GNOME / Plasma Wayland: mpv as X11 DESKTOP window — solid looping
        if shutil.which("mpv") and os.environ.get("DISPLAY"):
            return "mpv-desktop"
        return "gtk-player"

    def get_backend_label(self) -> str:
        b = self._backend or self.preferred_backend()
        labels = {
            "mpvpaper": "mpvpaper (warstwa Wayland)",
            "xwinwrap+mpv": "xwinwrap + mpv (X11)",
            "mpv-desktop": "mpv tapeta (Desktop + pętla)",
            "gtk-player": "GTK tapeta (Desktop)",
            "mpv-window": "mpv (pełny ekran — niezalecane)",
        }
        return labels.get(b, b)

    def is_active(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            if self._pid_belongs_to_current_session(self._proc.pid):
                return True
        pid = self._read_pid()
        if pid and self._pid_alive(pid) and self._pid_belongs_to_current_session(pid):
            return True
        return False

    def current_path(self) -> Optional[Path]:
        if self._current_path and self.is_active():
            return self._current_path
        state = self._read_state()
        if state and state.get("path"):
            return Path(state["path"])
        return None

    def set_animated(
        self,
        path: Path | str,
        *,
        mute: bool = True,
        loop: bool = True,
        set_poster: bool = True,
        backend: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Set video/GIF as animated wallpaper.
        Returns (success, human message).
        """
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return False, f"Plik nie istnieje: {path}"
        if not is_video_file(path) and path.suffix.lower() not in VIDEO_EXTENSIONS:
            # Allow GIF which is in both sets depending on config
            if path.suffix.lower() != ".gif":
                return False, "To nie jest plik wideo/animowany"

        # Do not cancel restore-on-login here. Full stop() runs
        # `systemctl --user disable --now` and would SIGTERM the
        # restore service that called us after login.
        self.stop(cancel_restore=False)

        poster: Optional[Path] = None
        if set_poster:
            poster = extract_poster_frame(path)
            if poster:
                self._set_static_poster(poster)

        backend = backend or self.preferred_backend()
        ok = False
        msg = ""

        if backend == "mpvpaper":
            ok, msg = self._start_mpvpaper(path, mute=mute, loop=loop)
        elif backend == "xwinwrap+mpv":
            ok, msg = self._start_xwinwrap(path, mute=mute, loop=loop)
        elif backend == "mpv-desktop":
            ok, msg = self._start_mpv_desktop(path, mute=mute, loop=loop)
        elif backend == "mpv-window":
            ok, msg = self._start_mpv_window(path, mute=mute, loop=loop)
        else:
            ok, msg = self._start_gtk_player(path, mute=mute, loop=loop)

        if not ok:
            for fb in ("mpv-desktop", "gtk-player", "mpv-window", "mpvpaper"):
                if fb == backend:
                    continue
                if fb == "mpvpaper" and not shutil.which("mpvpaper"):
                    continue
                if fb in ("mpv-window", "mpv-desktop") and not shutil.which("mpv"):
                    continue
                if fb == "mpv-desktop" and not os.environ.get("DISPLAY"):
                    continue
                if fb == "mpvpaper":
                    ok, msg = self._start_mpvpaper(path, mute=mute, loop=loop)
                elif fb == "mpv-desktop":
                    ok, msg = self._start_mpv_desktop(path, mute=mute, loop=loop)
                elif fb == "mpv-window":
                    ok, msg = self._start_mpv_window(path, mute=mute, loop=loop)
                else:
                    ok, msg = self._start_gtk_player(path, mute=mute, loop=loop)
                if ok:
                    backend = fb
                    break

        if ok:
            self._current_path = path
            self._backend = backend
            self._write_state({
                "path": str(path),
                "backend": backend,
                "mute": mute,
                "loop": loop,
                "set_poster": set_poster,
            })
            self._enable_restore_autostart()
            label = {
                "mpvpaper": "mpvpaper",
                "xwinwrap+mpv": "xwinwrap+mpv",
                "mpv-desktop": "mpv Desktop (pętla)",
                "gtk-player": "GTK Desktop",
                "mpv-window": "mpv fullscreen",
            }.get(backend, backend)
            return True, f"Animowana tapeta ustawiona ({label})"
        return False, msg or "Nie udało się uruchomić animowanej tapety"

    def restore(self) -> tuple[bool, str]:
        """
        Resume the last animated wallpaper after reboot / crash.
        Uses animated_state.json written by set_animated().
        """
        state = self._read_state()
        if not state or not state.get("path"):
            state = self._fallback_state()
        if not state or not state.get("path"):
            return False, "Brak zapisanej animowanej tapety do przywrócenia"

        backend = state.get("backend")
        if backend in (None, "auto"):
            backend = None

        # Leftover mpv from the previous login stays under user systemd
        # (start_new_session) but its XWayland is dead. A live PID is not
        # enough — the window must exist on THIS display.
        visible = self._player_visible_on_display()
        if visible:
            self._spawn_desktop_pin(MPV_DESKTOP_TITLE, duration=90.0)
            self._wait_until_desktop_window(MPV_DESKTOP_TITLE, timeout=20.0)
            return True, "Animowana tapeta już działa (ponownie przypięta do pulpitu)"

        if backend == "mpvpaper" and self.is_active():
            return True, "Animowana tapeta już działa (mpvpaper)"

        if self._read_pid() or self._find_player_pids():
            log_restore("killing leftover player from previous session")
            self.stop(cancel_restore=False)

        path = Path(state["path"]).expanduser()
        if not path.exists():
            return False, f"Plik nie istnieje (może został usunięty): {path}"

        ok, msg = self.set_animated(
            path,
            mute=bool(state.get("mute", True)),
            loop=bool(state.get("loop", True)),
            set_poster=bool(state.get("set_poster", True)),
            backend=backend,
        )
        if ok and (self._backend or backend) == "mpv-desktop":
            if not self._wait_until_desktop_window(MPV_DESKTOP_TITLE, timeout=25.0):
                log_restore("window missing after start, retrying once")
                self.stop(cancel_restore=False)
                ok, msg = self.set_animated(
                    path,
                    mute=bool(state.get("mute", True)),
                    loop=bool(state.get("loop", True)),
                    set_poster=bool(state.get("set_poster", True)),
                    backend=backend,
                )
                self._wait_until_desktop_window(MPV_DESKTOP_TITLE, timeout=25.0)
        return ok, msg

    def stop(self, *, cancel_restore: bool = True) -> bool:
        """Stop any running animated wallpaper.

        cancel_restore=True (UI / --stop-animated / static wallpaper):
        also clears saved state and removes login autostart.
        cancel_restore=False: only kill the player so a new one can start.
        """
        stopped = False
        had_persist = STATE_FILE.exists() or PID_FILE.exists()

        def _term_then_kill(pid: int) -> bool:
            """SIGTERM first (player hard-exits cleanly); SIGKILL if stuck."""
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            # animated_player uses os._exit on SIGTERM — should be instant
            for _ in range(20):  # ~1s
                if not self._pid_alive(pid):
                    return True
                time.sleep(0.05)
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            return True

        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=1)
                    except Exception:
                        pass
            except Exception:
                pass
            self._proc = None
            stopped = True

        pid = self._read_pid()
        if pid and self._pid_alive(pid):
            if _term_then_kill(pid):
                stopped = True

        # Kill leftover helpers we own (by unique title/args only)
        try:
            subprocess.run(
                ["killall", "-q", "mpvpaper"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        # Kill only our mpv wallpaper processes (unique --title)
        try:
            out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="ignore")
            for line in out.splitlines():
                if MPV_DESKTOP_TITLE in line or "wallora-animated-wallpaper" in line:
                    try:
                        pid = int(line.split(None, 1)[0])
                        if _term_then_kill(pid):
                            stopped = True
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
                elif "wallora.animated_player" in line or "animated_player.py" in line:
                    try:
                        pid = int(line.split(None, 1)[0])
                        if pid != os.getpid() and _term_then_kill(pid):
                            stopped = True
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
                elif PIN_MARKER in line and "wallora.animated" in line:
                    try:
                        pid = int(line.split(None, 1)[0])
                        if pid != os.getpid() and _term_then_kill(pid):
                            stopped = True
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
        except Exception:
            pass

        self._clear_pid()
        if cancel_restore:
            self._clear_state()
            self._disable_restore_autostart()
        self._backend = None
        self._current_path = None
        # True if we killed a process or cleared saved restore state
        return stopped or (had_persist if cancel_restore else stopped)

    # --- autostart (survive reboot) ---

    @staticmethod
    def _enable_restore_autostart():
        try:
            from wallora.config import Config
            Config().enable_animated_restore_on_login()
        except Exception as e:
            print("Animated autostart enable failed:", e)

    @staticmethod
    def _disable_restore_autostart():
        try:
            from wallora.config import Config
            Config().disable_animated_restore_on_login()
        except Exception as e:
            print("Animated autostart disable failed:", e)

    # --- backends ---

    def _mpv_opts(self, mute: bool, loop: bool) -> str:
        parts = [
            "no-osc",
            "no-input-default-bindings",
            "no-input-builtin-bindings",
            "no-input-builtin-dragging",
            "input-cursor=no",
            "input-cursor-passthrough=yes",
            "input-vo-keyboard=no",
            "cursor-autohide=no",
            "focus-on=never",
            "really-quiet",
            "hwdec=auto",
            "vo=gpu",
            "gpu-context=x11egl",
        ]
        if mute:
            parts.append("no-audio")
        if loop:
            parts.append("loop-file=inf")
        return " ".join(parts)

    @staticmethod
    def _x11_window_ids_by_title(title: str) -> list[str]:
        return AnimatedWallpaperManager._x11_window_ids_matching(
            title, MPV_X11_CLASS, MPV_DESKTOP_TITLE
        )

    @staticmethod
    def _x11_window_ids_matching(*needles: str) -> list[str]:
        if not shutil.which("xprop"):
            return []
        wanted = [n for n in needles if n]
        if not wanted:
            return []
        try:
            listing = subprocess.check_output(
                ["xprop", "-root", "_NET_CLIENT_LIST"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, OSError):
            return []
        hits: list[str] = []
        for wid in re.findall(r"0x[0-9a-fA-F]+", listing):
            try:
                info = subprocess.check_output(
                    ["xprop", "-id", wid, "WM_NAME", "_NET_WM_NAME", "WM_CLASS"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.CalledProcessError, OSError):
                continue
            if any(needle in info for needle in wanted):
                hits.append(wid)
        return hits

    @staticmethod
    def _x11_shape_input_empty(xid: int) -> bool:
        """Best-effort click-through. Isolated: a bad Xlib bind must not crash us."""
        try:
            rc = subprocess.run(
                [
                    sys.executable, "-c",
                    (
                        "import ctypes,sys\n"
                        "xid=int(sys.argv[1])\n"
                        "x11=ctypes.cdll.LoadLibrary('libX11.so.6')\n"
                        "xext=ctypes.cdll.LoadLibrary('libXext.so.6')\n"
                        "x11.XOpenDisplay.restype=ctypes.c_void_p\n"
                        "dpy=x11.XOpenDisplay(None)\n"
                        "sys.exit(1 if not dpy else 0) if False else None\n"
                        "if not dpy: raise SystemExit(1)\n"
                        "xext.XShapeCombineRectangles(dpy,xid,2,0,0,None,0,0,0)\n"
                        "x11.XSync(dpy,False)\n"
                        "x11.XCloseDisplay(dpy)\n"
                    ),
                    str(xid),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return rc.returncode == 0
        except Exception:
            return False

    @classmethod
    def _x11_apply_desktop_hints(cls, wid: str) -> bool:
        if not shutil.which("xprop"):
            return False
        cmds = [
            [
                "xprop", "-id", wid,
                "-f", "_NET_WM_WINDOW_TYPE", "32a",
                "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP",
            ],
            [
                "xprop", "-id", wid,
                "-f", "_NET_WM_STATE", "32a",
                "-set", "_NET_WM_STATE",
                "_NET_WM_STATE_BELOW, _NET_WM_STATE_STICKY, "
                "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER",
            ],
            [
                "xprop", "-id", wid,
                "-f", "_NET_WM_DESKTOP", "32c",
                "-set", "_NET_WM_DESKTOP", "4294967295",
            ],
        ]
        ok = all(_run(cmd) for cmd in cmds)
        try:
            cls._x11_shape_input_empty(int(wid, 16))
        except ValueError:
            pass
        return ok

    @classmethod
    def _x11_mark_desktop_by_title(cls, title: str, timeout: float = 5.0) -> bool:
        """Find X11 window by name and pin it as a click-through desktop layer."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for wid in cls._x11_window_ids_by_title(title):
                if cls._x11_apply_desktop_hints(wid):
                    return True
            time.sleep(0.15)
        return False

    @classmethod
    def x11_window_is_desktop(cls, title: str) -> bool:
        for wid in cls._x11_window_ids_by_title(title):
            try:
                info = subprocess.check_output(
                    ["xprop", "-id", wid, "_NET_WM_WINDOW_TYPE"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.CalledProcessError, OSError):
                continue
            if "_NET_WM_WINDOW_TYPE_DESKTOP" in info:
                return True
        return False

    @classmethod
    def _wait_until_desktop_window(cls, title: str, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls.x11_window_is_desktop(title):
                return True
            time.sleep(0.25)
        return False

    def _spawn_desktop_pin(self, title: str, duration: float = 90.0) -> None:
        """Detached helper: keeps DESKTOP hints after this process exits.

        Autostart ``--restore-animated`` used to start a daemon thread and
        exit immediately — GNOME then left mpv as a normal window.
        """
        src_root = str(Path(__file__).resolve().parent.parent)
        env = os.environ.copy()
        env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
        PIN_LOG.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "wallora.animated",
            PIN_MARKER, title, str(duration),
        ]
        try:
            log_f = open(PIN_LOG, "a", encoding="utf-8")
            subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
                env=env,
            )
        except Exception as e:
            print("desktop pin helper failed:", e)

    def _start_mpvpaper(self, path: Path, mute: bool, loop: bool) -> tuple[bool, str]:
        if not shutil.which("mpvpaper"):
            return False, "mpvpaper nie jest zainstalowany (pacman -S mpvpaper)"
        opts = self._mpv_opts(mute, loop)
        # '*' = all outputs
        cmd = ["mpvpaper", "-o", opts, "*", str(path)]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._write_pid(self._proc.pid)
            self._backend = "mpvpaper"
            return True, "mpvpaper"
        except Exception as e:
            return False, f"mpvpaper: {e}"

    def _start_xwinwrap(self, path: Path, mute: bool, loop: bool) -> tuple[bool, str]:
        if not shutil.which("xwinwrap") or not shutil.which("mpv"):
            return False, "Wymagane: xwinwrap i mpv"
        mpv_args = [
            "mpv", "-wid", "WID",
            "--really-quiet",
            "--no-osc",
            "--no-input-default-bindings",
            "--hwdec=auto",
        ]
        if mute:
            mpv_args.append("--no-audio")
        if loop:
            mpv_args.append("--loop-file=inf")
        mpv_args.append(str(path))
        # xwinwrap -fs -fdt -ni -b -nf -- mpv ...
        cmd = ["xwinwrap", "-fs", "-fdt", "-ni", "-b", "-nf", "--"] + mpv_args
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._write_pid(self._proc.pid)
            self._backend = "xwinwrap+mpv"
            return True, "xwinwrap+mpv"
        except Exception as e:
            return False, f"xwinwrap: {e}"

    def _start_mpv_desktop(self, path: Path, mute: bool, loop: bool) -> tuple[bool, str]:
        """mpv as X11/XWayland DESKTOP window with reliable infinite loop."""
        if not shutil.which("mpv"):
            return False, "mpv nie jest zainstalowany (pacman -S mpv)"
        if not os.environ.get("DISPLAY"):
            return False, "Brak DISPLAY (X11/XWayland)"

        title = MPV_DESKTOP_TITLE
        cmd = [
            "mpv",
            f"--title={title}",
            "--force-window=yes",
            "--really-quiet",
            "--no-osc",
            "--no-input-default-bindings",
            "--no-input-builtin-bindings",
            "--no-input-builtin-dragging",
            "--input-cursor=no",
            "--input-cursor-passthrough",
            "--input-vo-keyboard=no",
            "--cursor-autohide=no",
            "--focus-on=never",
            "--no-border",
            "--geometry=100%x100%+0+0",
            "--panscan=1.0",
            "--hwdec=auto",
            "--vo=gpu",
            "--gpu-context=x11egl",
            "--x11-name=wallora-desktop",
            "--stop-screensaver=no",
            "--keep-open=yes",
        ]
        if mute:
            cmd.append("--no-audio")
        # Critical: infinite file loop (Gtk MediaFile loop is broken for WebM)
        cmd.append("--loop-file=inf" if loop else "--loop-file=no")
        cmd.append(str(path))

        try:
            env = os.environ.copy()
            # Native Wayland mpv is a normal GNOME app window (steals hover/cursor).
            # Force XWayland so we can mark it DESKTOP + click-through.
            env.pop("WAYLAND_DISPLAY", None)
            env["GDK_BACKEND"] = "x11"
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            self._write_pid(self._proc.pid)
            self._backend = "mpv-desktop"
            # Must outlive this process: login restore exits right after start.
            self._spawn_desktop_pin(title, duration=90.0)
            return True, "mpv-desktop"
        except Exception as e:
            return False, f"mpv-desktop: {e}"

    def _start_mpv_window(self, path: Path, mute: bool, loop: bool) -> tuple[bool, str]:
        if not shutil.which("mpv"):
            return False, "mpv nie jest zainstalowany (pacman -S mpv)"
        cmd = [
            "mpv",
            "--title=wallora-animated-wallpaper",
            "--force-window=yes",
            "--really-quiet",
            "--no-osc",
            "--no-input-default-bindings",
            "--input-cursor=no",
            "--cursor-autohide=always",
            "--no-border",
            "--geometry=100%x100%+0+0",
            "--fs",
            "--hwdec=auto",
            "--x11-name=wallora-animated",
        ]
        if mute:
            cmd.append("--no-audio")
        if loop:
            cmd.append("--loop-file=inf")
        cmd.append(str(path))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._write_pid(self._proc.pid)
            self._backend = "mpv-window"
            return True, "mpv-window"
        except Exception as e:
            return False, f"mpv: {e}"

    def _start_gtk_player(self, path: Path, mute: bool, loop: bool) -> tuple[bool, str]:
        """
        Detached helper: fullscreen video as X11 _NET_WM_WINDOW_TYPE_DESKTOP
        (via XWayland on GNOME Wayland) so it behaves like wallpaper, not an app.
        """
        helper = Path(__file__).resolve().parent / "animated_player.py"
        if not helper.exists():
            return False, "Brak animated_player.py"

        cmd = [
            sys.executable,
            str(helper),
            "--video", str(path),
            "--backend", "x11-desktop",
            "--mute" if mute else "--no-mute",
            "--loop" if loop else "--no-loop",
        ]

        try:
            env = os.environ.copy()
            src_root = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
            # Force XWayland for DESKTOP window type (critical on GNOME Wayland)
            if env.get("DISPLAY"):
                env.pop("WAYLAND_DISPLAY", None)
                env["GDK_BACKEND"] = "x11"

            # Log to file for debugging player issues
            log_path = Path(GLib.get_user_cache_dir()) / "wallora" / "animated_player.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_f = open(log_path, "w", encoding="utf-8")

            self._proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
                env=env,
            )
            self._write_pid(self._proc.pid)
            self._backend = "gtk-player"
            return True, "gtk-player"
        except Exception as e:
            return False, f"gtk-player: {e}"

    def _set_static_poster(self, poster: Path):
        """Set poster frame as DE static wallpaper (GNOME overview etc.)."""
        try:
            from wallora.wallpaper_setter import WallpaperSetter
            setter = WallpaperSetter()
            # Poster is part of starting an animation — do not treat it as
            # "user chose a static wallpaper" (that would disable autostart).
            setter.set_wallpaper(poster, scaling="fill", stop_animated=False)
        except Exception as e:
            print("Poster set failed:", e)

    # --- pid / state ---

    def _write_pid(self, pid: int):
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(pid), encoding="utf-8")

    def _read_pid(self) -> Optional[int]:
        try:
            if PID_FILE.exists():
                return int(PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
        return None

    def _clear_pid(self):
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return self._pid_is_ours(pid)

    @staticmethod
    def _pid_environ(pid: int) -> dict[str, str]:
        env: dict[str, str] = {}
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return env
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            key_b, _, val_b = item.partition(b"=")
            try:
                env[key_b.decode()] = val_b.decode()
            except UnicodeDecodeError:
                continue
        return env

    @classmethod
    def _pid_belongs_to_current_session(cls, pid: int) -> bool:
        """False for leftover players still running after logout.

        mpv is started in a new session so user systemd keeps it across
        GNOME login. After re-login XAUTHORITY points at a new Mutter
        file and the old window is gone.
        """
        env = cls._pid_environ(pid)
        xauth = env.get("XAUTHORITY")
        if xauth and not Path(xauth).exists():
            return False
        their_d = env.get("DISPLAY")
        our_d = os.environ.get("DISPLAY")
        if their_d and our_d and their_d != our_d:
            return False
        return True

    def _player_visible_on_display(self) -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        return bool(self._x11_window_ids_by_title(MPV_DESKTOP_TITLE))

    def _find_player_pids(self) -> list[int]:
        pids: list[int] = []
        try:
            out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="ignore")
        except Exception:
            return pids
        for line in out.splitlines():
            if not any(
                marker in line
                for marker in (
                    MPV_DESKTOP_TITLE,
                    "wallora-animated-wallpaper",
                    "wallora.animated_player",
                    "animated_player.py",
                    "mpvpaper",
                )
            ):
                continue
            try:
                pid = int(line.split(None, 1)[0])
            except ValueError:
                continue
            if pid != os.getpid():
                pids.append(pid)
        return pids

    @staticmethod
    def _pid_is_ours(pid: int) -> bool:
        """Stale PIDs get reused; only treat living wallora/mpv wallpaper as active."""
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        cmd = raw.replace(b"\0", b" ").decode("utf-8", "ignore")
        markers = (
            MPV_DESKTOP_TITLE,
            "wallora-animated-wallpaper",
            "wallora.animated_player",
            "animated_player.py",
            "mpvpaper",
            "xwinwrap",
        )
        return any(marker in cmd for marker in markers)

    def _write_state(self, data: dict):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            from wallora.config import Config

            cfg = Config()
            anim = dict(cfg.get("animated", {}) or {})
            anim["last_path"] = data.get("path")
            cfg.set("animated", anim)
        except Exception:
            pass

    def _read_state(self) -> Optional[dict]:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def _fallback_state(self) -> Optional[dict]:
        """Config last_path survives a wiped cache / failed restore."""
        try:
            from wallora.config import Config

            cfg = Config()
            anim = cfg.get("animated", {}) or {}
            path = anim.get("last_path")
            if not path:
                return None
            return {
                "path": path,
                "backend": anim.get("backend") or None,
                "mute": bool(anim.get("mute", True)),
                "loop": bool(anim.get("loop", True)),
                "set_poster": bool(anim.get("set_poster", True)),
            }
        except Exception:
            return None

    def _clear_state(self):
        try:
            if STATE_FILE.exists():
                STATE_FILE.unlink()
        except Exception:
            pass
        try:
            from wallora.config import Config

            cfg = Config()
            anim = dict(cfg.get("animated", {}) or {})
            if "last_path" in anim:
                anim.pop("last_path", None)
                cfg.set("animated", anim)
        except Exception:
            pass

    def _atexit_cleanup(self):
        # Do NOT stop on exit — animated wallpaper should keep running after UI close.
        # Only clear in-memory handle.
        self._proc = None


def log_restore(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {msg}\n"
    try:
        RESTORE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RESTORE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    print(line, end="")


def acquire_restore_lock():
    """Non-blocking lock so desktop autostart + systemd do not start two players."""
    import fcntl

    RESTORE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(RESTORE_LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def maintain_x11_desktop_window(title: str, duration: float = 90.0) -> bool:
    """Keep applying DESKTOP / click-through hints for `duration` seconds."""
    PIN_LOG.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + max(1.0, duration)
    saw = False
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with PIN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} pin start title={title!r} duration={duration}\n")
    except Exception:
        pass

    while time.time() < deadline:
        if AnimatedWallpaperManager._x11_mark_desktop_by_title(title, timeout=1.2):
            if not saw:
                saw = True
                try:
                    with PIN_LOG.open("a", encoding="utf-8") as fh:
                        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} first pin ok\n")
                except Exception:
                    pass
            time.sleep(1.5)
        else:
            time.sleep(0.3)

    try:
        with PIN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} pin end saw={saw}\n"
            )
    except Exception:
        pass
    return saw


def _cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == PIN_MARKER:
        title = argv[1] if len(argv) > 1 else MPV_DESKTOP_TITLE
        try:
            duration = float(argv[2]) if len(argv) > 2 else 90.0
        except ValueError:
            duration = 90.0
        return 0 if maintain_x11_desktop_window(title, duration) else 1
    print(
        "Usage: python -m wallora.animated --pin-desktop [TITLE] [SECONDS]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
