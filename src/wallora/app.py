"""Main Adwaita application for Wallora."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from wallora import APP_ID, __version__
from wallora.window import WalloraWindow


class WalloraApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window: WalloraWindow | None = None

        self.connect("activate", self.on_activate)

        # Actions
        self.create_action("quit", self.on_quit, ["<primary>q"])
        self.create_action("about", self.on_about)
        self.create_action("preferences", self.on_preferences, ["<primary>comma"])

    def on_activate(self, app):
        if not self.window:
            self.window = WalloraWindow(application=self)
        self.window.present()

    def on_quit(self, *args):
        if self.window:
            self.window.save_state()
            if hasattr(self.window, 'slideshow') and self.window.slideshow.is_running:
                self.window.slideshow.stop()
        self.quit()

    def on_about(self, *args):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name="Wallora 2",
            application_icon=APP_ID,
            developer_name="GorianWaco",
            version=__version__,
            comments="Tapety animowane + kopia ulubionych poza systemem + Steam",
            website="https://github.com/GorianWaco/wallora-v2",
            issue_url="https://github.com/GorianWaco/wallora-v2/issues",
            license_type=Gtk.License.MIT_X11,
            developers=["GorianWaco"],
            copyright="© 2026",
        )
        about.present()

    def on_preferences(self, *args):
        if self.window:
            self.window.show_preferences()

    def create_action(self, name: str, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)
