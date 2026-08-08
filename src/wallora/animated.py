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


PID_FILE = Path(GLib.get_user_cache_dir()) / "wallora" / "animated.pid"
STATE_FILE = Path(GLib.get_user_cache_dir()) / "wallora" / "animated_state.json"
POSTER_PATH = Path(GLib.get_user_cache_dir()) / "wallora" / "animated_poster.jpg"


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
            return True
        # Also check PID file (daemon from previous run / detached)
        pid = self._read_pid()
        if pid and self._pid_alive(pid):
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

        self.stop()

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
        if self.is_active():
            return True, "Animowana tapeta już działa"

        state = self._read_state()
        if not state or not state.get("path"):
            return False, "Brak zapisanej animowanej tapety do przywrócenia"

        path = Path(state["path"]).expanduser()
        if not path.exists():
            return False, f"Plik nie istnieje (może został usunięty): {path}"

        backend = state.get("backend")
        if backend in (None, "auto"):
            backend = None

        return self.set_animated(
            path,
            mute=bool(state.get("mute", True)),
            loop=bool(state.get("loop", True)),
            set_poster=bool(state.get("set_poster", True)),
            backend=backend,
        )

    def stop(self) -> bool:
        """Stop any running animated wallpaper and cancel restore-on-login."""
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
        except Exception:
            pass

        self._clear_pid()
        self._clear_state()
        self._disable_restore_autostart()
        self._backend = None
        self._current_path = None
        # True if we killed a process or cleared saved restore state
        return stopped or had_persist

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
            "input-cursor=no",
            "cursor-autohide=always",
            "really-quiet",
            "hwdec=auto",
            "vo=gpu",
        ]
        if mute:
            parts.append("no-audio")
        if loop:
            parts.append("loop-file=inf")
        return " ".join(parts)

    @staticmethod
    def _x11_mark_desktop_by_title(title: str, timeout: float = 5.0) -> bool:
        """Find X11 window by WM_NAME and set DESKTOP + below + skip-taskbar."""
        try:
            from Xlib import X, display as xdisplay
        except Exception:
            return False

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                dpy = xdisplay.Display()
                root = dpy.screen().root
                net_client = dpy.intern_atom("_NET_CLIENT_LIST")
                wm_name = dpy.intern_atom("WM_NAME")
                atom_type = dpy.intern_atom("ATOM")
                cardinal = dpy.intern_atom("CARDINAL")

                prop = root.get_full_property(net_client, X.AnyPropertyType)
                if not prop:
                    time.sleep(0.15)
                    continue

                for xid in prop.value:
                    try:
                        win = dpy.create_resource_object("window", xid)
                        name_prop = win.get_full_property(wm_name, X.AnyPropertyType)
                        if not name_prop:
                            continue
                        name = name_prop.value
                        if isinstance(name, bytes):
                            name = name.decode("utf-8", "ignore")
                        if title not in str(name):
                            continue

                        def atom(n: str):
                            return dpy.intern_atom(n)

                        win.change_property(
                            atom("_NET_WM_WINDOW_TYPE"),
                            atom_type,
                            32,
                            [atom("_NET_WM_WINDOW_TYPE_DESKTOP")],
                        )
                        win.change_property(
                            atom("_NET_WM_STATE"),
                            atom_type,
                            32,
                            [
                                atom("_NET_WM_STATE_BELOW"),
                                atom("_NET_WM_STATE_STICKY"),
                                atom("_NET_WM_STATE_SKIP_TASKBAR"),
                                atom("_NET_WM_STATE_SKIP_PAGER"),
                            ],
                        )
                        win.change_property(
                            atom("_NET_WM_DESKTOP"),
                            cardinal,
                            32,
                            [0xFFFFFFFF],
                        )
                        win.configure(stack_mode=X.Below)
                        dpy.sync()
                        return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.15)
        return False

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
            "--input-cursor=no",
            "--cursor-autohide=always",
            "--no-border",
            "--geometry=100%x100%+0+0",
            "--panscan=1.0",
            "--hwdec=auto",
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
            # Prefer X11 for DESKTOP window type
            env.setdefault("GDK_BACKEND", "x11")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            self._write_pid(self._proc.pid)
            self._backend = "mpv-desktop"

            def apply_desktop():
                ok = self._x11_mark_desktop_by_title(title, timeout=6.0)
                # Re-apply a few times (GNOME may reset state on map)
                if ok:
                    time.sleep(0.8)
                    self._x11_mark_desktop_by_title(title, timeout=2.0)
                    time.sleep(1.5)
                    self._x11_mark_desktop_by_title(title, timeout=2.0)

            threading.Thread(target=apply_desktop, daemon=True).start()
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
            setter.set_wallpaper(poster, scaling="fill")
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
            return True
        except OSError:
            return False

    def _write_state(self, data: dict):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _read_state(self) -> Optional[dict]:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def _clear_state(self):
        try:
            if STATE_FILE.exists():
                STATE_FILE.unlink()
        except Exception:
            pass

    def _atexit_cleanup(self):
        # Do NOT stop on exit — animated wallpaper should keep running after UI close.
        # Only clear in-memory handle.
        self._proc = None
