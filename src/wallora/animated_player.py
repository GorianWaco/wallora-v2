#!/usr/bin/env python3
"""Detached video wallpaper player for Wallora.

On GNOME Wayland we force **XWayland** and set:
  - ``_NET_WM_WINDOW_TYPE_DESKTOP``
  - below / sticky / skip-taskbar / skip-pager
  - empty input region (click-through)

so the video sits as a desktop layer instead of a normal fullscreen app.

Usage:
  python -m wallora.animated_player --video /path/to/file.webm --mute --loop
"""
from __future__ import annotations

import argparse
import os
import signal
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wallora desktop wallpaper player")
    parser.add_argument("--video", required=True)
    # Defaults: mute + loop (wallpaper). Explicit --no-* from parent overrides.
    parser.add_argument("--mute", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--backend",
        choices=("auto", "x11-desktop", "wayland"),
        default="auto",
    )
    args = parser.parse_args(argv)

    video_path = os.path.abspath(args.video)
    if not os.path.isfile(video_path):
        print(f"File not found: {video_path}", file=sys.stderr)
        return 1

    # Prefer X11/XWayland so we can set DESKTOP window type (GNOME Wayland).
    if args.backend in ("auto", "x11-desktop") and os.environ.get("DISPLAY"):
        os.environ.pop("WAYLAND_DISPLAY", None)
        os.environ["GDK_BACKEND"] = "x11"

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    app = Adw.Application(application_id="org.wallora.WallpaperPlayer")
    windows: list[Gtk.Window] = []
    media_streams: list = []
    # DESKTOP-type windows are ignored by Gtk.Application's "has open windows"
    # refcount — without hold() the app exits after the first stream ends.
    app.hold()

    def _hard_exit(code: int = 0) -> None:
        """Exit without NVIDIA/GStreamer GL teardown race (SEGV on Py_Exit).

        Gtk.MediaFile uses gstgl; on NVIDIA the GL context thread often
        still touches fences while Python runs atexit → SIGSEGV and
        "Usługa wysypała się" / DrKonqi noise even though playback was fine.
        """
        for stream in list(media_streams):
            try:
                stream.pause()
            except Exception:
                pass
            try:
                # Drop the file so GStreamer may release sooner
                if hasattr(stream, "set_file"):
                    stream.set_file(None)
            except Exception:
                pass
        for w in list(windows):
            try:
                w.set_child(None)
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        try:
            app.release()
        except Exception:
            pass
        # Skip CPython/GI/NVIDIA atexit destructors — process is done.
        os._exit(code)

    def _x11_set_desktop(win: Gtk.Window) -> bool:
        """Apply EWMH desktop wallpaper hints via python-xlib or xprop."""
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
        except Exception as e:
            print(f"X11 deps missing: {e}", file=sys.stderr)
            return False

        surface = win.get_surface()
        if surface is None or not isinstance(surface, GdkX11.X11Surface):
            return False

        try:
            xid = int(GdkX11.X11Surface.get_xid(surface))
        except Exception:
            try:
                xid = surface.get_xid()  # type: ignore[attr-defined]
            except Exception as e:
                print(f"no xid: {e}", file=sys.stderr)
                return False

        if not xid:
            return False

        try:
            from Xlib import X, display as xdisplay
        except Exception:
            return _x11_desktop_via_xprop(xid)

        try:
            dpy = xdisplay.Display()
            xwin = dpy.create_resource_object("window", xid)

            def atom(name: str):
                return dpy.intern_atom(name)

            xwin.change_property(
                atom("_NET_WM_WINDOW_TYPE"),
                atom("ATOM"),
                32,
                [atom("_NET_WM_WINDOW_TYPE_DESKTOP")],
            )
            xwin.change_property(
                atom("_NET_WM_STATE"),
                atom("ATOM"),
                32,
                [
                    atom("_NET_WM_STATE_BELOW"),
                    atom("_NET_WM_STATE_STICKY"),
                    atom("_NET_WM_STATE_SKIP_TASKBAR"),
                    atom("_NET_WM_STATE_SKIP_PAGER"),
                ],
            )
            xwin.change_property(
                atom("_NET_WM_DESKTOP"),
                atom("CARDINAL"),
                32,
                [0xFFFFFFFF],
            )
            xwin.configure(stack_mode=X.Below)
            dpy.sync()
            return True
        except Exception as e:
            print(f"X11 desktop props failed: {e}", file=sys.stderr)
            return _x11_desktop_via_xprop(xid)

    def _x11_desktop_via_xprop(xid: int) -> bool:
        """Fallback when python-xlib is missing."""
        import shutil
        import subprocess

        if not shutil.which("xprop"):
            return False
        wid = hex(xid)
        cmds = [
            ["xprop", "-id", wid, "-f", "_NET_WM_WINDOW_TYPE", "32a",
             "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP"],
            ["xprop", "-id", wid, "-f", "_NET_WM_STATE", "32a",
             "-set", "_NET_WM_STATE",
             "_NET_WM_STATE_BELOW, _NET_WM_STATE_STICKY, "
             "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER"],
        ]
        ok = True
        for cmd in cmds:
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                ok = False
        try:
            from wallora.animated import AnimatedWallpaperManager

            AnimatedWallpaperManager._x11_shape_input_empty(xid)
        except Exception:
            pass
        return ok

    def _click_through(win: Gtk.Window) -> None:
        try:
            surface = win.get_surface()
            if surface is None:
                return
            import cairo
            surface.set_input_region(cairo.Region())
        except Exception:
            pass

    def _maintain(win: Gtk.Window):
        _x11_set_desktop(win)
        _click_through(win)
        return True  # reschedule

    def on_activate(application: Adw.Application):
        display = Gdk.Display.get_default()
        if not display:
            application.quit()
            return

        monitors = display.get_monitors()
        n = monitors.get_n_items()
        if n == 0:
            application.quit()
            return

        file = Gio.File.new_for_path(video_path)

        for i in range(n):
            mon = monitors.get_item(i)
            if not isinstance(mon, Gdk.Monitor):
                continue

            media = Gtk.MediaFile.new_for_file(file)
            media_streams.append(media)
            # Note: Gtk.MediaFile.set_loop() is broken for many WebM/VP9 streams
            # (timestamp freezes at duration, ended stays False, playing stays True).
            # We always force-loop via seek watchdog below.
            try:
                media.set_loop(False)  # avoid broken native loop path
            except Exception:
                pass
            try:
                media.set_muted(bool(args.mute))
            except Exception:
                pass

            # Gtk set_loop is broken for WebM/VP9 (freezes at last frame).
            # Detect "no progress" and seek+play. Timestamps are microseconds.
            loop_state = {"last_ts": -1, "stuck": 0, "max_ts": 0}

            def _restart_stream(stream=media, state=loop_state):
                try:
                    stream.seek(0)
                except Exception:
                    pass
                try:
                    stream.play()
                except Exception:
                    pass
                state["last_ts"] = -1
                state["stuck"] = 0
                state["max_ts"] = 0
                return False

            def _on_ended(stream, *_args):
                if args.loop:
                    GLib.idle_add(_restart_stream)
                    GLib.timeout_add(40, _restart_stream)

            try:
                media.connect("notify::ended", _on_ended)
            except Exception:
                pass

            def _watchdog(stream=media, state=loop_state):
                if not args.loop:
                    return False
                try:
                    ts = int(stream.get_timestamp() or 0)
                    dur = int(stream.get_duration() or 0)
                    playing = bool(stream.get_playing())
                    ended = bool(stream.get_ended())

                    if ts > state["last_ts"]:
                        state["stuck"] = 0
                        state["last_ts"] = ts
                        state["max_ts"] = max(state["max_ts"], ts)
                        return True

                    # Timestamp not advancing
                    state["stuck"] += 1
                    if state["stuck"] < 3:
                        return True

                    progressed = state["max_ts"] > max(1_000_000, int(dur * 0.3) if dur else 1_000_000)
                    at_end = dur > 0 and ts >= max(0, dur - 200_000)
                    at_zero_after = ts < 200_000 and progressed

                    if ended or at_end or at_zero_after or not playing:
                        _restart_stream(stream, state)
                    elif state["stuck"] >= 8:
                        # Hard freeze mid-stream
                        _restart_stream(stream, state)
                except Exception:
                    pass
                return True

            GLib.timeout_add(250, _watchdog)

            picture = Gtk.Picture.new_for_paintable(media)
            try:
                picture.set_content_fit(Gtk.ContentFit.COVER)
            except Exception:
                pass
            picture.set_hexpand(True)
            picture.set_vexpand(True)

            win = Gtk.Window(application=application, title="Desktop")
            win.set_decorated(False)
            win.set_resizable(True)
            win.set_deletable(False)
            win.set_child(picture)
            try:
                win.set_focusable(False)
                win.set_can_focus(False)
                win.set_focus_on_click(False)
            except Exception:
                pass

            def present_on_monitor(w=win, m=mon, stream=media):
                # Prefer sized window over Gtk fullscreen — fullscreen forces
                # _NET_WM_STATE_FULLSCREEN which fights DESKTOP type on GNOME.
                try:
                    geo = m.get_geometry()
                    w.set_default_size(max(geo.width, 320), max(geo.height, 200))
                    try:
                        w.set_size_request(geo.width, geo.height)
                    except Exception:
                        pass
                except Exception:
                    w.set_default_size(1920, 1080)
                w.present()

                def place_and_desktop():
                    try:
                        geo = m.get_geometry()
                        # Move to monitor origin (XWayland root coords)
                        surface = w.get_surface()
                        if surface is not None:
                            try:
                                surface.set_geometry(geo.x, geo.y, geo.width, geo.height)
                            except Exception:
                                pass
                        w.set_default_size(geo.width, geo.height)
                    except Exception:
                        pass
                    ok = _x11_set_desktop(w)
                    _click_through(w)
                    try:
                        # Native loop is unreliable — watchdog handles looping
                        stream.set_loop(False)
                    except Exception:
                        pass
                    try:
                        stream.play()
                    except Exception:
                        pass
                    if ok:
                        GLib.timeout_add(1200, lambda: _maintain(w))
                    return False

                GLib.timeout_add(150, place_and_desktop)
                GLib.timeout_add(500, place_and_desktop)
                return False

            GLib.idle_add(present_on_monitor)
            windows.append(win)

    def handle_signal(signum, frame):
        # Must not call app.quit() → Py_Exit: NVIDIA gstgl SEGV on teardown.
        _hard_exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    app.connect("activate", on_activate)
    # Also hold via register flags
    try:
        app.set_inactivity_timeout(0)
    except Exception:
        pass
    # app.run() normally returns into sys.exit → same GL teardown crash.
    # Always hard-exit after the main loop ends.
    try:
        app.run(None)
    finally:
        _hard_exit(0)
    return 0  # unreachable


if __name__ == "__main__":
    # main() hard-exits; this is only for type-checkers / odd paths
    raise SystemExit(main())
