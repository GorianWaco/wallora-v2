"""Main window for Wallora — advanced wallpaper manager."""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from pathlib import Path
from typing import Optional

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from wallora.animated import AnimatedWallpaperManager
from wallora.config import Config
from wallora.library import Library
from wallora.models import WallpaperItem
from wallora.processor import ImageProcessor
from wallora.slideshow import Slideshow
from wallora.utils import SUPPORTED_EXTENSIONS, format_duration
from wallora.wallpaper_setter import WallpaperSetter


class WalloraWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Wallora 2 — animowane tapety")
        self.set_default_size(1280, 820)

        self.connect("destroy", self._on_destroy)

        self.config = Config()
        self.library = Library(self.config)
        self.processor = ImageProcessor()
        self.setter = WallpaperSetter()
        self.slideshow = Slideshow(self.config)
        self.animated = AnimatedWallpaperManager()

        self._current_item: Optional[WallpaperItem] = None
        self._current_adjustments = self.config.get("adjustments", {}).copy()
        self._current_scaling = self.config.get("default_scaling", "fit")
        self._preview_video: Optional[Gtk.Video] = None
        self._preview_media = None

        self._thumbnails: dict[str, Gtk.Picture] = {}
        self._list_rows: dict[str, Gtk.ListBoxRow] = {}

        # Default scaling options (populated in _build_ui)
        self._scaling_options = [
            ("Wypełnij (fill)", "fill"),
            ("Dopasuj (fit)", "fit"),
            ("Dopasuj + rozmyte tło", "fit_blur"),
            ("Rozciągnij (stretch)", "stretch"),
            ("Wyśrodkuj", "center"),
            ("Wyśrodkuj + rozmyte tło", "center_blur"),
            ("Kafelkuj (tile)", "tile"),
            ("Rozciągnij na wszystkie (span)", "span"),
        ]

        self._build_ui()
        self._connect_signals()
        self._restore_state()
        self._initial_scan()

        # Start slideshow if it was enabled
        if self.config.get("slideshow.enabled"):
            GLib.idle_add(self._start_slideshow_from_config)

        # Auto-import Steam profile wallpaper currently equipped in Steam
        if self.config.get("steam.auto_import_equipped", True):
            GLib.timeout_add_seconds(2, self._schedule_auto_steam_equipped)

    def _build_ui(self):
        self.set_content(Adw.ToastOverlay())

        # Main layout using AdwToolbarView + header
        toolbar = Adw.ToolbarView()
        self.get_content().set_child(toolbar)

        # Header bar
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        # Left: menu + add folder
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Preferencje", "app.preferences")
        menu.append("O programie", "app.about")
        menu.append("Wyjdź", "app.quit")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        add_btn = Gtk.Button(label="Dodaj folder", icon_name="folder-new-symbolic")
        add_btn.connect("clicked", self._on_add_folder)
        header.pack_start(add_btn)

        # Search
        self.search_entry = Gtk.SearchEntry(placeholder_text="Szukaj tapet...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.set_title_widget(self.search_entry)

        # Split view: sidebar + main content
        self.split = Adw.OverlaySplitView()
        self.split.set_sidebar_width_fraction(0.33)  # wider sidebar so long sliders + spinbuttons fit comfortably
        toolbar.set_content(self.split)

        # === SIDEBAR ===
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.set_margin_top(12)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(8)

        # Library header
        lib_label = Gtk.Label(label="Biblioteka", xalign=0)
        lib_label.add_css_class("heading")
        sidebar.append(lib_label)

        # Folders list
        self.folders_list = Gtk.ListBox()
        self.folders_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.folders_list.add_css_class("boxed-list")
        sidebar.append(self.folders_list)

        # Add folder quick button again
        add2 = Gtk.Button(label="Dodaj folder biblioteki", icon_name="list-add-symbolic")
        add2.connect("clicked", self._on_add_folder)
        sidebar.append(add2)

        steam_btn = Gtk.Button(
            label="Import ze Steam (profil)",
            icon_name="folder-download-symbolic",
        )
        steam_btn.set_tooltip_text(
            "Pobierz tło ustawione w profilu Steam (CDN) + cache sklepu. "
            "Program i tak robi to automatycznie przy starcie."
        )
        steam_btn.connect("clicked", self._on_import_steam)
        steam_btn.set_margin_top(4)
        self._steam_import_btn = steam_btn
        sidebar.append(steam_btn)

        Gtk.Separator().set_margin_top(12)
        sidebar.append(Gtk.Separator())

        # Filters
        filter_label = Gtk.Label(label="Filtry", xalign=0, margin_top=8)
        filter_label.add_css_class("heading")
        sidebar.append(filter_label)

        self.filter_all = Gtk.ToggleButton(label="Wszystkie", active=True)
        self.filter_fav = Gtk.ToggleButton(label="Ulubione ⭐")
        self.filter_recent = Gtk.ToggleButton(label="Ostatnie")
        self.filter_animated = Gtk.ToggleButton(label="Animowane 🎬")

        self.filter_all.connect("toggled", lambda b: self._refresh_grid() if b.get_active() else None)
        self.filter_fav.connect("toggled", lambda b: self._refresh_grid() if b.get_active() else None)
        self.filter_recent.connect("toggled", lambda b: self._refresh_grid() if b.get_active() else None)
        self.filter_animated.connect("toggled", lambda b: self._refresh_grid() if b.get_active() else None)

        # Make them radio-like
        self.filter_all.set_group(self.filter_fav)
        self.filter_recent.set_group(self.filter_fav)
        self.filter_animated.set_group(self.filter_fav)

        fb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        fb.append(self.filter_all)
        fb.append(self.filter_fav)
        fb.append(self.filter_recent)
        fb.append(self.filter_animated)
        sidebar.append(fb)

        # Korekcja obrazu - under filters on the left as requested
        Gtk.Separator().set_margin_top(12)
        sidebar.append(Gtk.Separator())

        corr_label = Gtk.Label(label="Korekcja obrazu", xalign=0, margin_top=8)
        corr_label.add_css_class("heading")
        sidebar.append(corr_label)

        corr_scroll = Gtk.ScrolledWindow()
        corr_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        corr_scroll.set_vexpand(True)

        corr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        corr_scroll.set_child(corr_box)

        self.sliders = {}
        adjustments = [
            ("brightness", "Jasność", 0.5, 1.5, 0.01),
            ("contrast", "Kontrast", 0.5, 1.8, 0.01),
            ("saturation", "Nasycenie", 0.0, 2.0, 0.01),
            ("hue", "Odcień", -180, 180, 5),
            ("gamma", "Gamma", 0.2, 3.0, 0.05),
            ("sharpness", "Wyostrzenie", 0.0, 2.5, 0.05),
            ("temperature", "Ciepło barw", -80, 80, 1),
            ("blur", "Rozmycie", 0.0, 8.0, 0.1),
            ("vignette", "Winieta", 0.0, 1.0, 0.05),
            ("edge_blur", "Rozmycie krawędzi", 0.0, 40.0, 1.0),
            ("edge_tint", "Kolor rozmytych krawędzi", -100, 100, 5),
            ("bg_blur", "Rozmycie tła", 0.0, 1.0, 0.01),
            ("bg_expand", "Rozszerzenie tła", 0.0, 0.8, 0.01),
            ("bg_fade", "Miejsce startu rozmycia tła", 0.0, 0.4, 0.01),
        ]

        for key, label, lo, hi, step in adjustments:
            # Each correction: name on top, long slider + editable value
            item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            lbl = Gtk.Label(label=label, xalign=0)

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            if key in ("temperature", "hue", "vignette", "edge_tint", "edge_blur"):
                default = self._current_adjustments.get(key, 0)
            elif key == "bg_fade":
                default = self._current_adjustments.get(key, 0.1)
            elif key in ("gamma", "bg_blur", "bg_expand"):
                default = self._current_adjustments.get(key, 1.0 if key == "gamma" else 0.5 if key == "bg_blur" else 0.0)
            else:
                default = self._current_adjustments.get(key, 1.0)

            adj = Gtk.Adjustment(
                value=default,
                lower=lo,
                upper=hi,
                step_increment=step,
                page_increment=step * 10
            )

            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
            scale.set_hexpand(True)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.set_size_request(340, -1)   # even longer sliders (roughly doubled effective length)
            scale.connect("value-changed", self._on_adjustment_changed, key)

            digits = 2 if step < 1 else 0
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.1, digits=digits)
            spin.set_width_chars(6)
            spin.set_numeric(True)

            hbox.append(scale)
            hbox.append(spin)

            item_box.append(lbl)
            item_box.append(hbox)
            corr_box.append(item_box)
            self.sliders[key] = scale

        # Quick buttons for corrections
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=8)
        reset_btn = Gtk.Button(label="Przywróć oryginalne", icon_name="edit-undo-symbolic")
        reset_btn.set_tooltip_text("Przywróć wszystkie ustawienia korekcji obrazu do wartości oryginalnych")
        reset_btn.connect("clicked", self._on_reset_adjustments)

        fav_btn = Gtk.Button(label="★ Ulubiona", icon_name="starred-symbolic")
        fav_btn.connect("clicked", self._on_toggle_favorite)

        btn_box.append(reset_btn)
        btn_box.append(fav_btn)
        corr_box.append(btn_box)

        sidebar.append(corr_scroll)

        # Backend info
        info = Gtk.Label(
            label=f"Statyczne: {self.setter.get_backend_name()}\nAnimowane: {self.animated.get_backend_label()}",
            xalign=0,
        )
        info.add_css_class("dim-label")
        info.set_margin_top(16)
        info.set_wrap(True)
        sidebar.append(info)

        # Stop animated wallpaper
        stop_anim_btn = Gtk.Button(label="Zatrzymaj animację", icon_name="media-playback-stop-symbolic")
        stop_anim_btn.set_tooltip_text("Wyłącza aktualną animowaną tapetę pulpitu")
        stop_anim_btn.connect("clicked", self._on_stop_animated)
        stop_anim_btn.set_margin_top(8)
        sidebar.append(stop_anim_btn)

        self.split.set_sidebar(sidebar)

        # === MAIN CONTENT ===
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.split.set_content(main_box)

        # Top controls bar
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.set_margin_top(6)

        # Scaling
        scale_label = Gtk.Label(label="Skalowanie:")

        # Display names + internal mode keys
        self._scaling_options = [
            ("Wypełnij (fill)", "fill"),
            ("Dopasuj (fit)", "fit"),
            ("Dopasuj + rozmyte tło", "fit_blur"),          # great for ultrawide
            ("Rozciągnij (stretch)", "stretch"),
            ("Wyśrodkuj", "center"),
            ("Wyśrodkuj + rozmyte tło", "center_blur"),    # great for ultrawide
            ("Kafelkuj (tile)", "tile"),
            ("Rozciągnij na wszystkie (span)", "span"),
        ]

        display_names = [name for name, _ in self._scaling_options]
        self.scale_combo = Gtk.DropDown.new_from_strings(display_names)
        self.scale_combo.set_selected(1)  # default: Dopasuj (fit)
        self.scale_combo.connect("notify::selected", self._on_scaling_changed)

        controls.append(scale_label)
        controls.append(self.scale_combo)

        # Direct interval time change - grouped with scaling for easy access
        int_label = Gtk.Label(label="  |  co")
        self.ss_interval_spin = Gtk.SpinButton.new_with_range(0.1, 480, 0.5)
        self.ss_interval_spin.set_value(self.config.get("slideshow.interval_seconds", 300) / 60.0)
        self.ss_interval_spin.set_digits(1)
        self.ss_interval_spin.set_width_chars(4)
        self.ss_interval_spin.set_tooltip_text("Czas po jakim tapeta ma się zmienić automatycznie")
        self.ss_interval_spin.connect("value-changed", self._on_quick_interval_changed)
        min_l = Gtk.Label(label="min")
        gear = Gtk.Button(icon_name="preferences-system-symbolic")
        gear.set_tooltip_text("Więcej ustawień zmiany tapet (źródło, losowość, presety)")
        gear.connect("clicked", self._show_interval_popover)

        controls.append(int_label)
        controls.append(self.ss_interval_spin)
        controls.append(min_l)
        controls.append(gear)

        hint = Gtk.Label(label="→ Wybierz obraz po lewej, edytuj i kliknij duży przycisk pod podglądem")
        hint.add_css_class("dim-label")
        hint.set_margin_start(16)
        controls.append(hint)

        # Spacer
        controls.append(Gtk.Box(hexpand=True))

        # === SLIDESHOW CONTROLS (much richer) ===
        self.slideshow_toggle = Gtk.ToggleButton(label="Automatyczna zmiana", icon_name="media-playlist-repeat-symbolic")
        self.slideshow_toggle.connect("toggled", self._on_slideshow_toggled)
        controls.append(self.slideshow_toggle)

        # Playback buttons
        self.ss_prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic")
        self.ss_prev_btn.set_tooltip_text("Poprzednia tapeta")
        self.ss_prev_btn.connect("clicked", self._on_slideshow_prev)
        self.ss_prev_btn.set_sensitive(False)

        self.ss_play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.ss_play_btn.set_tooltip_text("Wznów / Wstrzymaj")
        self.ss_play_btn.connect("clicked", self._on_slideshow_playpause)

        self.ss_next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.ss_next_btn.set_tooltip_text("Następna tapeta")
        self.ss_next_btn.connect("clicked", self._on_slideshow_next)
        self.ss_next_btn.set_sensitive(False)

        ss_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        ss_box.add_css_class("linked")
        ss_box.append(self.ss_prev_btn)
        ss_box.append(self.ss_play_btn)
        ss_box.append(self.ss_next_btn)
        controls.append(ss_box)

        # Small status for slideshow
        self.ss_status_label = Gtk.Label()
        self.ss_status_label.add_css_class("dim-label")
        self.ss_status_label.set_margin_start(8)
        controls.append(self.ss_status_label)

        main_box.append(controls)

        # Content area: vertical list (left) + inspector/preview (right) (paned)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(780)   # Give more space to the bigger inspector + preview
        main_box.append(paned)

        # Vertical list of image previews (one under another on the left, as requested)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_size_request(260, -1)  # narrower for list
        list_scroll.set_hexpand(False)
        list_scroll.set_vexpand(True)

        self.thumbnail_list = Gtk.ListBox()
        self.thumbnail_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.thumbnail_list.set_activate_on_single_click(True)
        self.thumbnail_list.connect("row-selected", self._on_thumbnail_row_selected)

        list_scroll.set_child(self.thumbnail_list)
        paned.set_start_child(list_scroll)

        # === PREVIEW AREA (right) ===
        preview_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview_area.set_margin_start(12)
        preview_area.set_margin_end(12)
        preview_area.set_margin_top(6)
        preview_area.set_hexpand(True)
        preview_area.set_vexpand(True)

        # Preview header
        prev_label = Gtk.Label(label="Podgląd edytowanej tapety", xalign=0)
        prev_label.add_css_class("heading")
        preview_area.append(prev_label)

        # BIG PREVIEW (static image or video stack)
        self.preview_frame = Gtk.Frame()
        self.preview_frame.set_size_request(600, 380)
        self.preview_frame.set_hexpand(True)
        self.preview_frame.set_vexpand(True)

        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.preview_picture = Gtk.Picture()
        self.preview_picture.set_can_shrink(True)
        self.preview_picture.set_keep_aspect_ratio(True)
        self.preview_stack.add_named(self.preview_picture, "image")

        self.preview_video = Gtk.Video()
        self.preview_video.set_autoplay(True)
        try:
            self.preview_video.set_property("controls", True)
        except Exception:
            pass
        self.preview_stack.add_named(self.preview_video, "video")

        self.preview_frame.set_child(self.preview_stack)
        preview_area.append(self.preview_frame)

        self.file_label = Gtk.Label(label="Wybierz tapetę z biblioteki", wrap=True, xalign=0)
        self.file_label.add_css_class("dim-label")
        preview_area.append(self.file_label)

        # === PROMINENT ACTION BUTTONS ===
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=4)
        actions_box.set_homogeneous(True)

        # Main "Set wallpaper" button
        self.apply_btn = Gtk.Button(label="Ustaw jako tapetę")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.add_css_class("pill")
        self.apply_btn.set_tooltip_text(
            "Przetwórz i ustaw jako tapetę (obrazy) lub uruchom animację (wideo/GIF)"
        )
        self.apply_btn.connect("clicked", self._on_apply_clicked)
        self.apply_btn.set_sensitive(False)

        # Save button (new)
        self.save_btn = Gtk.Button(label="Zapisz jako...")
        self.save_btn.set_tooltip_text("Zapisz przetworzoną wersję na dysk")
        self.save_btn.connect("clicked", self._on_save_clicked)
        self.save_btn.set_sensitive(False)

        actions_box.append(self.apply_btn)
        actions_box.append(self.save_btn)
        preview_area.append(actions_box)

        # Small secondary
        secondary_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=2)
        apply_raw_btn = Gtk.Button(label="Ustaw oryginał")
        apply_raw_btn.set_tooltip_text("Ustaw oryginalny plik bez żadnych zmian")
        apply_raw_btn.connect("clicked", self._on_apply_raw_clicked)
        secondary_box.append(apply_raw_btn)
        preview_area.append(secondary_box)

        # Status
        self.status_label = Gtk.Label(label="", wrap=True)
        self.status_label.add_css_class("dim-label")
        preview_area.append(self.status_label)

        paned.set_end_child(preview_area)

        # Bottom bar
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_margin_start(12)
        bottom.set_margin_bottom(8)
        self.count_label = Gtk.Label(label="0 tapet")
        self.count_label.add_css_class("dim-label")
        bottom.append(self.count_label)

        # Clear cache
        clear_btn = Gtk.Button(label="Wyczyść cache miniatur", icon_name="user-trash-symbolic")
        clear_btn.connect("clicked", self._on_clear_cache)
        bottom.append(clear_btn)

        main_box.append(bottom)

    def _connect_signals(self):
        self.connect("close-request", self._on_close)

    def _restore_state(self):
        w = self.config.get("window_width", 1280)
        h = self.config.get("window_height", 820)
        self.set_default_size(w, h)
        # Default to full screen as requested
        self.fullscreen()

        # Restore scaling
        scaling = self.config.get("default_scaling", "fit")
        # Find index by internal key
        idx = 1
        for i, (_, key) in enumerate(self._scaling_options):
            if key == scaling:
                idx = i
                break
        self.scale_combo.set_selected(idx)
        self._current_scaling = scaling

        # Restore adjustments
        self._current_adjustments = self.config.get("adjustments", self._current_adjustments).copy()
        for k, scale in self.sliders.items():
            if k in ("temperature", "hue", "vignette", "edge_tint", "edge_blur"):
                val = self._current_adjustments.get(k, 0)
            elif k == "bg_fade":
                val = self._current_adjustments.get(k, 0.1)
            elif k in ("gamma", "bg_blur", "bg_expand"):
                val = self._current_adjustments.get(k, 1.0 if k == "gamma" else 0.5 if k == "bg_blur" else 0.0)
            else:
                val = self._current_adjustments.get(k, 1.0)
            scale.set_value(val)

        # Initial interval widgets
        self._refresh_interval_controls()

    def save_state(self):
        w, h = self.get_default_size()
        self.config.set("window_width", w)
        self.config.set("window_height", h)
        self.config.set("default_scaling", self._current_scaling)
        self.config.set("adjustments", self._current_adjustments)

    def _on_close(self, *args):
        self.save_state()
        if not self.slideshow.is_running:
            return False

        # Ask user whether to keep automatic changing running in background
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Automatyczna zmiana tapet jest aktywna",
            body="Czy chcesz, aby automatyczna zmiana tapet nadal działała w tle po zamknięciu okna?",
        )
        dialog.add_response("stop", "Zatrzymaj")
        dialog.add_response("keep", "Zostaw w tle")
        dialog.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("keep")
        dialog.set_close_response("stop")

        dialog.connect("response", self._on_close_dialog_response)
        dialog.present()
        return True  # prevent immediate close while dialog is shown

    def _on_close_dialog_response(self, dialog, response):
        dialog.close()
        if response == "keep":
            # Hide window but keep slideshow running in background
            self.hide()
        else:
            # Stop slideshow and actually close the window
            self.slideshow.stop()
            app = self.get_application()
            if app:
                app.release()
            GLib.idle_add(self.close)

    def _on_destroy(self, *args):
        """Clean up app reference when window is really destroyed."""
        app = self.get_application()
        if app:
            app.window = None

    # === Library ===

    def _initial_scan(self):
        folders = self.config.get("library_folders", [])
        if not folders:
            # Seed with common locations
            defaults = [
                str(Path.home() / "Obrazy"),
                str(Path.home() / "Pulpit"),
                str(Path.home() / "Pobrane"),
            ]
            for d in defaults:
                if Path(d).is_dir():
                    self.config.add_library_folder(d)
                    folders.append(d)

        self._refresh_folders_list()
        self.library.scan(on_finished=self._on_scan_finished)

    def _refresh_folders_list(self):
        # Clear
        while (child := self.folders_list.get_first_child()):
            self.folders_list.remove(child)

        for f in self.library.get_folders():
            row = Adw.ActionRow(title=Path(f).name, subtitle=f)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, path=f: self._remove_folder(path))
            row.add_suffix(rm)
            self.folders_list.append(row)

    def _on_add_folder(self, *_):
        dialog = Gtk.FileDialog(title="Wybierz folder z tapetami")
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                if self.library.add_folder(path):
                    self._refresh_folders_list()
                    self.library.scan(on_finished=self._on_scan_finished)
        except Exception as e:
            print("Folder selection error:", e)

    def _remove_folder(self, path: str):
        self.library.remove_folder(path)
        self._refresh_folders_list()
        self.library.scan(on_finished=self._on_scan_finished)

    def _schedule_auto_steam_equipped(self):
        """Kick off quiet equipped-import; reschedule poll if configured."""
        self._auto_import_steam_equipped(quiet=True)
        interval = int(self.config.get("steam.poll_interval_seconds", 600) or 0)
        if interval > 0 and self.config.get("steam.auto_import_equipped", True):
            GLib.timeout_add_seconds(max(60, interval), self._poll_steam_equipped)
        return False  # one-shot from initial timeout

    def _poll_steam_equipped(self):
        if not self.config.get("steam.auto_import_equipped", True):
            return False
        self._auto_import_steam_equipped(quiet=True)
        return True  # keep polling

    def _auto_import_steam_equipped(self, *, quiet: bool = True):
        """Background: download currently equipped Steam profile wallpaper."""
        if getattr(self, "_steam_import_busy", False):
            return
        if getattr(self, "_steam_auto_busy", False):
            return
        self._steam_auto_busy = True

        def worker():
            try:
                from wallora.steam_import import auto_import_equipped_quiet

                result = auto_import_equipped_quiet(self.config)

                def done():
                    self._steam_auto_busy = False
                    if result.imported > 0:
                        self._refresh_folders_list()
                        self.library.scan(on_finished=self._on_scan_finished)
                        if not quiet:
                            self._show_toast(
                                f"Steam: pobrano tło profilu (+{result.imported})"
                            )
                        else:
                            self._show_toast(
                                f"Steam: nowe tło profilu w bibliotece (+{result.imported})"
                            )
                    return False

                GLib.idle_add(done)
            except Exception as e:
                def fail():
                    self._steam_auto_busy = False
                    if not quiet:
                        self._show_toast(f"Steam auto-import: {e}")
                    return False

                GLib.idle_add(fail)

        threading.Thread(target=worker, daemon=True).start()

    def _on_import_steam(self, *_):
        """Import animated Steam profile backgrounds into the library."""
        if getattr(self, "_steam_import_busy", False):
            self._show_toast("Import ze Steam już trwa…")
            return

        self._steam_import_busy = True
        if hasattr(self, "_steam_import_btn"):
            self._steam_import_btn.set_sensitive(False)
        self._show_toast("Import teł profilu Steam…")

        def worker():
            try:
                from wallora.steam_import import import_and_register

                result = import_and_register(self.config, quality="high", force=False)

                def done():
                    self._steam_import_busy = False
                    if hasattr(self, "_steam_import_btn"):
                        self._steam_import_btn.set_sensitive(True)
                    self._refresh_folders_list()
                    self.library.scan(on_finished=self._on_scan_finished)
                    if result.imported or result.skipped or result.equipped_found:
                        msg = (
                            f"Steam: +{result.imported} "
                            f"(profil {result.equipped_found}, "
                            f"pomiń {result.skipped}, podglądy −{result.filtered_low_quality})"
                        )
                        self._show_toast(msg)
                        if result.imported and hasattr(self, "filter_animated"):
                            self.filter_animated.set_active(True)
                    elif result.filtered_low_quality and not result.imported:
                        self._show_toast(
                            "Steam: tylko podglądy 300×168 w cache. "
                            "Ustaw tło w profilu Steam — Wallora ściągnie je sama."
                        )
                    elif result.errors:
                        self._show_toast(result.errors[0][:120])
                    else:
                        self._show_toast("Steam: nic do zaimportowania")
                    return False

                GLib.idle_add(done)
            except Exception as e:
                def fail():
                    self._steam_import_busy = False
                    if hasattr(self, "_steam_import_btn"):
                        self._steam_import_btn.set_sensitive(True)
                    self._show_toast(f"Import Steam: błąd — {e}")
                    return False

                GLib.idle_add(fail)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_finished(self, items):
        self._refresh_grid()

    def _refresh_grid(self):
        # Remove all rows from vertical list
        while row := self.thumbnail_list.get_row_at_index(0):
            self.thumbnail_list.remove(row)

        self._list_rows = {}
        self._thumbnails.clear()  # reuse for list rows too

        search = self.search_entry.get_text().strip()
        only_fav = self.filter_fav.get_active()
        only_recent = self.filter_recent.get_active()
        only_animated = self.filter_animated.get_active()

        items = self.library.get_filtered(search, only_favorites=only_fav, only_recent=only_recent)
        if only_animated:
            items = [i for i in items if i.is_animated]

        for item in items:
            row = self._create_list_row(item)
            self.thumbnail_list.append(row)
            self._list_rows[str(item.path)] = row

        n_anim = sum(1 for i in items if i.is_animated)
        if n_anim:
            self.count_label.set_text(f"{len(items)} tapet ({n_anim} animowanych)")
        else:
            self.count_label.set_text(f"{len(items)} tapet")

        # Re-select current item if still in list (after filter/search change)
        # or select first one so that apply button is enabled
        if self._current_item:
            current_path = str(self._current_item.path)
            if current_path in self._list_rows:
                self.thumbnail_list.select_row(self._list_rows[current_path])
                return
        # Auto-select first to enable buttons and show preview
        if items:
            first_row = self.thumbnail_list.get_row_at_index(0)
            if first_row:
                self.thumbnail_list.select_row(first_row)

    def _create_list_row(self, item: WallpaperItem) -> Gtk.ListBoxRow:
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)
        row_box.set_halign(Gtk.Align.CENTER)

        # Small thumbnail on left (no filename, as requested)
        pic = Gtk.Picture()
        pic.set_can_shrink(True)
        pic.set_keep_aspect_ratio(True)
        pic.set_size_request(180, 100)  # larger for vertical list

        size = self.config.thumbnail_size
        pixbuf = self.library.get_thumbnail(item, size, self._on_thumbnail_ready)
        if pixbuf:
            tex = Gdk.Texture.new_for_pixbuf(pixbuf)
            pic.set_paintable(tex)

        self._thumbnails[str(item.path)] = pic

        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badge_box.set_halign(Gtk.Align.CENTER)

        if item.is_animated:
            badge = Gtk.Label(label="🎬")
            badge.set_tooltip_text("Animowana tapeta (wideo/GIF)")
            badge_box.append(badge)

        if self.config.is_favorite(str(item.path)):
            star = Gtk.Label(label="★")
            star.add_css_class("accent")
            badge_box.append(star)

        if badge_box.get_first_child():
            row_box.append(badge_box)

        row_box.append(pic)

        row = Gtk.ListBoxRow()
        row.set_child(row_box)
        row.item = item  # attach for selection
        return row

    def _on_thumbnail_ready(self, item: WallpaperItem, pixbuf):
        pic = self._thumbnails.get(str(item.path))
        if pic:
            tex = Gdk.Texture.new_for_pixbuf(pixbuf)
            pic.set_paintable(tex)

    # === Selection & Preview ===

    def _on_thumbnail_row_selected(self, listbox, row):
        try:
            if row and hasattr(row, "item"):
                self._select_item(row.item)
        except Exception as e:
            print("Selection error:", e)
            self._show_toast("Nie udało się załadować obrazu")

    def _select_item(self, item: WallpaperItem):
        self._current_item = item
        kind = "🎬 animacja" if item.is_animated else "🖼️ obraz"
        self.file_label.set_text(f"{kind}  ·  {item.path}")

        # Enable action buttons
        self.apply_btn.set_sensitive(True)
        self.save_btn.set_sensitive(not item.is_animated)

        if item.is_animated:
            self._show_video_preview(item)
            self.status_label.set_text(
                "Animowana tapeta — korekcja obrazu nie dotyczy wideo. Kliknij „Ustaw jako tapetę”."
            )
            return

        self._clear_video_preview()
        self.preview_stack.set_visible_child_name("image")

        # Load into processor
        try:
            if self.processor.load(item.path):
                self._update_preview()
        except Exception as e:
            print("Load error:", e)

        # Helpful hint
        self.status_label.set_text("Suwaki zmieniają podgląd na żywo. Użyj 'Ustaw jako tapetę' lub 'Zapisz jako...'")
        GLib.timeout_add_seconds(5, lambda: (self.status_label.set_text("") or False))

    def _show_video_preview(self, item: WallpaperItem):
        """Show Gtk.Video preview for animated media."""
        try:
            self._clear_video_preview()
            media = Gtk.MediaFile.new_for_filename(str(item.path))
            try:
                media.set_loop(True)
            except Exception:
                pass
            try:
                media.set_muted(True)
            except Exception:
                pass
            self.preview_video.set_media_stream(media)
            self._preview_media = media
            self.preview_stack.set_visible_child_name("video")
            try:
                media.play()
            except Exception:
                pass
        except Exception as e:
            print("Video preview error:", e)
            # Fallback: show thumbnail in picture widget
            self.preview_stack.set_visible_child_name("image")
            pixbuf = self.library.get_thumbnail(item, 640, None)
            if pixbuf:
                tex = Gdk.Texture.new_for_pixbuf(pixbuf)
                self.preview_picture.set_paintable(tex)
            self.status_label.set_text(f"Podgląd wideo niedostępny ({e}); miniatura OK")

    def _clear_video_preview(self):
        try:
            if self._preview_media is not None:
                try:
                    self._preview_media.pause()
                except Exception:
                    pass
                self._preview_media = None
            self.preview_video.set_media_stream(None)
        except Exception:
            pass

    def _update_preview(self):
        if not self._current_item:
            return
        if self._current_item.is_animated:
            return
        if not self.processor.has_image:
            return

        try:
            alloc_w = max(600, self.preview_frame.get_allocated_width() or 900)
            alloc_h = max(400, self.preview_frame.get_allocated_height() or 600)

            if self._current_scaling in ("fit_blur", "center_blur"):
                # For blurred-sides modes: generate preview at the widget's size
                # so it always fills the preview height (no black bars top/bottom in UI).
                # Blur only on sides if source aspect differs.
                preview_img = self.processor.process_for_wallpaper(
                    self._current_adjustments,
                    scaling=self._current_scaling,
                    target_size=(alloc_w, alloc_h),
                    multi_monitor=False
                )
            else:
                # Normal modes: generate larger for quality
                preview_max = (int(alloc_w * 1.2), int(alloc_h * 1.2))
                preview_img = self.processor.get_preview(
                    self._current_adjustments,
                    preview_max
                )

            if preview_img is None:
                return

            # Most reliable cross-version way for live previews
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                preview_img.save(tmp.name, "PNG")
                tex = Gdk.Texture.new_from_file(Gio.File.new_for_path(tmp.name))
                self.preview_picture.set_paintable(tex)
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
        except Exception as e:
            # Don't spam console on every slider move
            self.status_label.set_text("Błąd generowania podglądu")

    def _pil_to_bytes(self, img) -> bytes:
        import io
        b = io.BytesIO()
        img.save(b, format="PNG")
        return b.getvalue()

    def _on_adjustment_changed(self, scale, key):
        val = scale.get_value()
        self._current_adjustments[key] = val
        self.config.set("adjustments", self._current_adjustments)

        # Live update preview
        GLib.idle_add(self._update_preview)

    def _on_scaling_changed(self, combo, _pspec):
        idx = combo.get_selected()
        if 0 <= idx < len(self._scaling_options):
            _, internal = self._scaling_options[idx]
            self._current_scaling = internal
        else:
            self._current_scaling = "fit"
        # Update preview so user sees how the crop/letterbox will roughly look
        GLib.idle_add(self._update_preview)

    def _on_reset_adjustments(self, *_):
        """Reset all image correction settings to original neutral values."""
        neutral = {
            "brightness": 1.0,
            "contrast": 1.0,
            "saturation": 1.0,
            "hue": 0,
            "gamma": 1.0,
            "sharpness": 1.0,
            "temperature": 0,
            "blur": 0.0,
            "vignette": 0.0,
            "edge_blur": 0.0,
            "edge_tint": 0,
            "bg_blur": 0.5,
            "bg_expand": 0.0,
            "bg_fade": 0.1,
        }
        self._current_adjustments = neutral.copy()
        # Also update the saved config defaults
        self.config.reset_adjustments()
        for k, scale in self.sliders.items():
            val = self._current_adjustments.get(k, 1.0)
            if k in ("temperature", "hue", "vignette", "edge_tint", "edge_blur", "bg_fade"):
                val = self._current_adjustments.get(k, 0 if k != "bg_fade" else 0.1)
            elif k in ("gamma", "bg_blur", "bg_expand"):
                val = self._current_adjustments.get(k, 1.0 if k == "gamma" else 0.5 if k == "bg_blur" else 0.0)
            scale.set_value(val)
        self._update_preview()

    def _on_toggle_favorite(self, *_):
        if not self._current_item:
            return
        p = str(self._current_item.path)
        if self.config.is_favorite(p):
            self.config.remove_favorite(p)
        else:
            self.config.add_favorite(p)

        self._refresh_grid()
        # Re-select if needed
        if self._current_item:
            self.file_label.set_text(p)

    # === Apply ===

    def _on_apply_clicked(self, *_):
        if not self._current_item:
            self._show_toast("Wybierz tapetę")
            return

        self._apply_current()

    def _on_apply_raw_clicked(self, *_):
        """Apply original image without any processing (or video as-is)."""
        if not self._current_item:
            self._show_toast("Wybierz tapetę")
            return

        if self._current_item.is_animated:
            self._apply_animated()
            return

        try:
            ok = self.setter.set_wallpaper(self._current_item.path, scaling=self._current_scaling)
            if ok:
                self.config.add_recent(str(self._current_item.path))
                self._show_toast(f"Ustawiono oryginał: {self._current_item.name}")
            else:
                self._show_toast("Nie udało się ustawić tapety")
        except Exception as e:
            self._show_toast(f"Błąd: {e}")

    def _on_save_clicked(self, *_):
        """Save the currently edited (processed) wallpaper to a file chosen by user."""
        if not self._current_item or not self.processor.has_image:
            self._show_toast("Najpierw wybierz tapetę")
            return

        dialog = Gtk.FileDialog(
            title="Zapisz przetworzoną tapetę",
            initial_name=f"{self._current_item.path.stem}_edytowana.jpg"
        )

        # Suggest jpg by default
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter()
        f.set_name("Obrazy JPEG")
        f.add_mime_type("image/jpeg")
        filters.append(f)
        f2 = Gtk.FileFilter()
        f2.set_name("Obrazy PNG")
        f2.add_mime_type("image/png")
        filters.append(f2)
        dialog.set_filters(filters)

        dialog.save(self, None, self._on_save_dialog_finish)

    def _on_save_dialog_finish(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if not file:
                return

            save_path = file.get_path()
            if not save_path:
                return

            # Process at high quality using current monitor size or good default
            processed = self.processor.process_for_wallpaper(
                self._current_adjustments,
                scaling=self._current_scaling,
                multi_monitor=(self._current_scaling == "span")
            )

            if processed is None:
                self._show_toast("Nie udało się przetworzyć obrazu")
                return

            # Choose format based on extension
            ext = Path(save_path).suffix.lower()
            if ext == ".png":
                processed.save(save_path, "PNG", optimize=True)
            else:
                processed.save(save_path, "JPEG", quality=95)

            self._show_toast(f"Zapisano: {Path(save_path).name}")
        except Exception as e:
            print("Save error:", e)
            self._show_toast(f"Błąd zapisu: {e}")

    def _apply_current(self, show_toast=True):
        if not self._current_item:
            self._show_toast("Wybierz tapetę")
            return False

        if self._current_item.is_animated:
            return self._apply_animated(show_toast=show_toast)

        if not self.processor.has_image:
            self._show_toast("Wybierz tapetę")
            return False

        scaling = self._current_scaling

        # Process full resolution
        processed = self.processor.process_for_wallpaper(
            self._current_adjustments,
            scaling=scaling,
            multi_monitor=(scaling == "span"),
        )
        if processed is None:
            self._show_toast("Błąd przetwarzania obrazu")
            return False

        # Save processed to cache
        out_path = self.processor.save_processed(processed)

        # Set it as wallpaper (also stops any running animation)
        ok = self.setter.set_wallpaper(out_path, scaling=scaling)

        if ok:
            self.config.add_recent(str(self._current_item.path))
            if show_toast:
                backend = self.setter.get_backend_name()
                self._show_toast(f"Ustawiono jako tapetę ({backend})")
            return True
        else:
            self._show_toast("Nie udało się ustawić tapety")
            return False

    def _apply_animated(self, show_toast=True) -> bool:
        if not self._current_item:
            return False

        anim_cfg = self.config.get("animated", {}) or {}
        mute = bool(anim_cfg.get("mute", True))
        loop = bool(anim_cfg.get("loop", True))
        set_poster = bool(anim_cfg.get("set_poster", True))
        backend = anim_cfg.get("backend", "auto")
        if backend == "auto":
            backend = None

        ok, msg = self.animated.set_animated(
            self._current_item.path,
            mute=mute,
            loop=loop,
            set_poster=set_poster,
            backend=backend,
        )
        if ok:
            self.config.add_recent(str(self._current_item.path))
            if show_toast:
                self._show_toast(msg)
            return True
        if show_toast:
            self._show_toast(msg)
        return False

    def _on_stop_animated(self, *_):
        if self.animated.stop():
            self._show_toast("Zatrzymano animowaną tapetę")
        else:
            self._show_toast("Brak aktywnej animowanej tapety")

    # === Slideshow ===

    def _on_slideshow_toggled(self, btn):
        enabled = btn.get_active()
        self.config.set("slideshow.enabled", enabled)

        if enabled:
            self._start_slideshow()
        else:
            self.slideshow.stop()
            app = self.get_application()
            if app:
                app.release()
            self.ss_prev_btn.set_sensitive(False)
            self.ss_next_btn.set_sensitive(False)
            self.ss_play_btn.set_icon_name("media-playback-start-symbolic")
            self.ss_status_label.set_text("")
            self._refresh_interval_controls()
            self._show_toast("Automatyczna zmiana wyłączona")

    def _start_slideshow(self):
        only_fav = self.config.get("slideshow.only_favorites", False)
        only_recent = self.config.get("slideshow.only_recent", False)

        items = self.library.get_filtered(
            only_favorites=only_fav,
            only_recent=only_recent
        )
        if not items:
            items = self.library.items
        if not items:
            self._show_toast("Brak tapet w bibliotece")
            self.slideshow_toggle.set_active(False)
            return

        paths = [str(i.path) for i in items]

        # Optionally exclude videos from slideshow (default: yes)
        if not self.config.get("animated.include_in_slideshow", False):
            paths = [str(i.path) for i in items if not i.is_animated]
            if not paths:
                self._show_toast("Brak statycznych tapet do slideshow (wykluczono wideo)")
                self.slideshow_toggle.set_active(False)
                return

        def do_set():
            # Pick next and apply without showing many toasts
            if not paths:
                return
            import random
            chosen = random.choice(paths) if self.config.get("slideshow.random") else paths[0]
            for it in self.library.items:
                if str(it.path) == chosen:
                    self._current_item = it
                    if it.is_animated:
                        self._apply_animated(show_toast=False)
                        GLib.idle_add(lambda: self._show_video_preview(it) or False)
                    elif self.processor.load(it.path):
                        self._apply_current(show_toast=False)
                        GLib.idle_add(self._update_preview)
                    break
            GLib.idle_add(self._update_slideshow_status)

        interval = self.config.get("slideshow.interval_seconds", 300)
        self.slideshow.start(paths, do_set, interval)

        # Update UI
        self.ss_prev_btn.set_sensitive(True)
        self.ss_next_btn.set_sensitive(True)
        self.ss_play_btn.set_icon_name("media-playback-pause-symbolic")
        self._show_toast(f"Automatyczna zmiana włączona ({format_duration(interval)}). Zamknięcie okna nie przerwie jej działania.")

        # Keep the application alive even if window is closed
        app = self.get_application()
        if app:
            app.hold()
        self._refresh_interval_controls()
        self._update_slideshow_status()

    def _on_slideshow_prev(self, *_):
        if self.slideshow.is_running:
            self.slideshow.advance_prev()
            self._update_slideshow_status()

    def _on_slideshow_next(self, *_):
        if self.slideshow.is_running:
            self.slideshow.advance_next()
            self._update_slideshow_status()

    def _on_slideshow_playpause(self, *_):
        if self.slideshow.is_running:
            self.slideshow.stop()
            self.ss_play_btn.set_icon_name("media-playback-start-symbolic")
            self.ss_status_label.set_text("Wstrzymano")
        else:
            # Restart slideshow from current library state
            self.slideshow_toggle.set_active(True)

    def _update_slideshow_status(self):
        if not self.slideshow.is_running:
            self.ss_status_label.set_text("")
            return
        length = self.slideshow.get_playlist_length()
        idx = self.slideshow.get_current_index() + 1
        interval = self.config.get("slideshow.interval_seconds", 300)
        self.ss_status_label.set_text(f"{idx}/{length} • co {format_duration(interval)}")

    def _on_quick_interval_changed(self, spin):
        """Directly change the slideshow interval from the toolbar spinbutton."""
        mins = spin.get_value()
        secs = max(5, int(mins * 60))

        self.config.set("slideshow.interval_seconds", secs)

        if self.slideshow.is_running:
            self.slideshow.update_interval(secs)

        self._update_slideshow_status()

    def _refresh_interval_controls(self):
        """Sync the spin and any other interval displays with current config."""
        secs = self.config.get("slideshow.interval_seconds", 300)
        if hasattr(self, "ss_interval_spin"):
            self.ss_interval_spin.set_value(secs / 60.0)

    def _start_slideshow_from_config(self):
        self.slideshow_toggle.set_active(True)
        self._start_slideshow()

    def _show_interval_popover(self, btn):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(14)
        box.set_margin_end(14)

        title = Gtk.Label(label="Automatyczna zmiana tapet", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        # Interval
        int_label = Gtk.Label(label="Interwał", xalign=0)
        box.append(int_label)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10, 3600, 10)
        scale.set_value(self.config.get("slideshow.interval_seconds", 300))
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.BOTTOM)
        scale.set_size_request(260, -1)

        def format_cb(scale, val):
            return format_duration(int(val))
        scale.set_format_value_func(format_cb)
        box.append(scale)

        # Quick presets
        presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for secs, lbl in [(30, "30s"), (60, "1 min"), (300, "5 min"), (900, "15 min"), (1800, "30 min")]:
            b = Gtk.Button(label=lbl)
            b.connect("clicked", lambda _b, s=secs: (scale.set_value(s), None))
            presets_box.append(b)
        box.append(presets_box)

        # Source
        source_label = Gtk.Label(label="Źródło tapet", xalign=0, margin_top=6)
        box.append(source_label)

        source_store = Gtk.StringList.new(["Wszystkie", "Tylko ulubione", "Ostatnie"])
        source_row = Gtk.DropDown(model=source_store)
        current_src = 0 if not self.config.get("slideshow.only_favorites") else 1
        if self.config.get("slideshow.only_recent", False):
            current_src = 2
        source_row.set_selected(current_src)
        box.append(source_row)

        # Options
        random_chk = Gtk.CheckButton(label="Losowa kolejność")
        random_chk.set_active(self.config.get("slideshow.random", True))
        box.append(random_chk)

        avoid_repeat = Gtk.CheckButton(label="Unikaj powtarzania niedawno użytych")
        avoid_repeat.set_active(self.config.get("slideshow.avoid_recent", True))
        box.append(avoid_repeat)

        apply_btn = Gtk.Button(label="Zastosuj zmiany")
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_margin_top(6)

        def apply():
            secs = int(scale.get_value())
            self.config.set("slideshow.interval_seconds", secs)

            src_idx = source_row.get_selected()
            only_fav = src_idx == 1
            only_recent = src_idx == 2
            self.config.set("slideshow.only_favorites", only_fav)
            self.config.set("slideshow.only_recent", only_recent)
            self.config.set("slideshow.random", random_chk.get_active())
            self.config.set("slideshow.avoid_recent", avoid_repeat.get_active())

            self._refresh_interval_controls()

            if self.slideshow.is_running:
                self.slideshow.update_interval(secs)
                # Restart with new source if needed
                self.slideshow.stop()
                self._start_slideshow()

            pop.popdown()

        apply_btn.connect("clicked", lambda *_: apply())
        box.append(apply_btn)

        pop.set_child(box)
        pop.set_parent(btn)
        pop.popup()

    # === Misc ===

    def _on_search_changed(self, *_):
        self._refresh_grid()

    def _on_clear_cache(self, *_):
        self.library.clear_cache()
        self._show_toast("Cache miniatur wyczyszczony")
        # Rebuild grid to regenerate thumbs
        self._refresh_grid()

    def _show_toast(self, text: str):
        toast = Adw.Toast(title=text)
        toast.set_timeout(2)
        overlay = self.get_content()
        if isinstance(overlay, Adw.ToastOverlay):
            overlay.add_toast(toast)

    def show_preferences(self):
        win = Adw.PreferencesWindow(transient_for=self)
        win.set_title("Preferencje Wallora")

        page = Adw.PreferencesPage(title="Ogólne")

        # Scaling default
        group = Adw.PreferencesGroup(title="Domyślne ustawienia")

        scaling_row = Adw.ComboRow(title="Skalowanie", subtitle="Używane przy szybkim zastosowaniu")
        # Use the same nice names as main combo
        display_names = [name for name, _ in self._scaling_options]
        scaling_row.set_model(Gtk.StringList.new(display_names))

        # Find current index
        current = self.config.get("default_scaling", "fit")
        sel_idx = 1
        for i, (_, k) in enumerate(self._scaling_options):
            if k == current:
                sel_idx = i
                break
        scaling_row.set_selected(sel_idx)

        def on_scaling_change(row, _pspec):
            idx = row.get_selected()
            if 0 <= idx < len(self._scaling_options):
                _, key = self._scaling_options[idx]
                self.config.set("default_scaling", key)
            else:
                self.config.set("default_scaling", "fit")

        scaling_row.connect("notify::selected", on_scaling_change)
        group.add(scaling_row)

        page.add(group)

        # === On login ===
        login_group = Adw.PreferencesGroup(
            title="Przy logowaniu",
            description="Animowana tapeta jest wznawiana automatycznie po restarcie "
            "(gdy jest aktywna). „Zatrzymaj animację” wyłącza to przywracanie.",
        )

        anim_restore_row = Adw.ActionRow(
            title="Przywracanie animowanej tapety",
            subtitle=(
                "Włączone — ostatnia animacja wróci po restarcie"
                if self.config.is_animated_restore_on_login_enabled()
                else "Wyłączone — ustaw animowaną tapetę, żeby włączyć"
            ),
        )
        anim_status = Gtk.Label(
            label="aktywne" if self.config.is_animated_restore_on_login_enabled() else "nieaktywne"
        )
        anim_status.add_css_class("dim-label")
        anim_restore_row.add_suffix(anim_status)
        login_group.add(anim_restore_row)

        random_login_row = Adw.SwitchRow(
            title="Zmień tapetę losowo przy logowaniu",
            subtitle="Losowa tapeta statyczna przy starcie sesji "
            "(animowana ma priorytet, jeśli jest zapisana)"
        )
        random_login_row.set_active(self.config.is_random_on_login_enabled())

        def on_random_login_changed(row, _pspec):
            enabled = row.get_active()
            if enabled:
                if self.config.enable_random_on_login():
                    self._show_toast("Autostart dla losowej tapety włączony")
                else:
                    self._show_toast("Nie udało się utworzyć autostartu")
                    row.set_active(False)
            else:
                self.config.disable_random_on_login()
                self._show_toast("Autostart wyłączony")

        random_login_row.connect("notify::active", on_random_login_changed)
        login_group.add(random_login_row)

        # Quick test button
        test_btn = Gtk.Button(label="Ustaw losową tapetę teraz")
        test_btn.connect("clicked", lambda *_: self._test_random_now())
        login_group.add(test_btn)

        restore_test_btn = Gtk.Button(label="Przywróć animowaną tapetę teraz")
        restore_test_btn.set_tooltip_text("Test: wallora --restore-animated")
        restore_test_btn.connect("clicked", self._on_test_restore_animated)
        login_group.add(restore_test_btn)

        page.add(login_group)

        # Library
        lib_group = Adw.PreferencesGroup(title="Biblioteka")
        clear_btn = Gtk.Button(label="Wyczyść cache miniatur")
        clear_btn.connect("clicked", self._on_clear_cache)
        lib_group.add(clear_btn)

        steam_auto_row = Adw.SwitchRow(
            title="Auto-import tła z profilu Steam",
            subtitle="Przy starcie i co kilka minut: ściąga aktualnie ustawione "
            "tło profilu (localconfig/API + CDN). Każde kolejne ustawione tło "
            "zostaje w historii biblioteki.",
        )
        steam_auto_row.set_active(bool(self.config.get("steam.auto_import_equipped", True)))

        def on_steam_auto(row, _p):
            self.config.set("steam.auto_import_equipped", row.get_active())

        steam_auto_row.connect("notify::active", on_steam_auto)
        lib_group.add(steam_auto_row)

        steam_import_btn = Gtk.Button(label="Import animowanych teł profilu Steam")
        steam_import_btn.set_tooltip_text(
            "Ustawione tło profilu + skan cache Steam (htmlcache) → "
            "~/.cache/wallora/steam-profiles/"
        )
        steam_import_btn.connect("clicked", self._on_import_steam)
        lib_group.add(steam_import_btn)
        page.add(lib_group)

        # === Animated wallpapers ===
        anim_group = Adw.PreferencesGroup(
            title="Animowane tapety",
            description=f"Aktywny backend: {self.animated.get_backend_label()}. "
            f"Dostępne: {', '.join(self.animated.available_backends())}",
        )

        mute_row = Adw.SwitchRow(
            title="Wycisz dźwięk",
            subtitle="Polecane — wideo bez audio na pulpicie",
        )
        mute_row.set_active(bool(self.config.get("animated.mute", True)))

        def on_mute(row, _p):
            self.config.set("animated.mute", row.get_active())

        mute_row.connect("notify::active", on_mute)
        anim_group.add(mute_row)

        loop_row = Adw.SwitchRow(
            title="Zapętlaj wideo",
            subtitle="Odtwarzaj w pętli od początku",
        )
        loop_row.set_active(bool(self.config.get("animated.loop", True)))

        def on_loop(row, _p):
            self.config.set("animated.loop", row.get_active())

        loop_row.connect("notify::active", on_loop)
        anim_group.add(loop_row)

        poster_row = Adw.SwitchRow(
            title="Ustaw klatkę jako statyczną tapetę",
            subtitle="Na GNOME poprawia wygląd Overview / ekranu blokady (ffmpeg)",
        )
        poster_row.set_active(bool(self.config.get("animated.set_poster", True)))

        def on_poster(row, _p):
            self.config.set("animated.set_poster", row.get_active())

        poster_row.connect("notify::active", on_poster)
        anim_group.add(poster_row)

        ss_anim_row = Adw.SwitchRow(
            title="Uwzględniaj wideo w automatycznej zmianie",
            subtitle="Domyślnie slideshow używa tylko statycznych obrazów",
        )
        ss_anim_row.set_active(bool(self.config.get("animated.include_in_slideshow", False)))

        def on_ss_anim(row, _p):
            self.config.set("animated.include_in_slideshow", row.get_active())

        ss_anim_row.connect("notify::active", on_ss_anim)
        anim_group.add(ss_anim_row)

        backend_row = Adw.ComboRow(
            title="Backend animacji",
            subtitle="auto wybiera najlepszy dla Twojego pulpitu",
        )
        backend_ids = ["auto", "mpv-desktop", "gtk-player", "mpvpaper", "mpv-window", "xwinwrap+mpv"]
        backend_labels = [
            "Automatyczny (zalecane)",
            "mpv tapeta Desktop + pętla (GNOME)",
            "GTK tapeta Desktop",
            "mpvpaper (Hyprland/Sway)",
            "mpv fullscreen (niezalecane)",
            "xwinwrap + mpv (X11)",
        ]
        backend_row.set_model(Gtk.StringList.new(backend_labels))
        cur_backend = self.config.get("animated.backend", "auto")
        try:
            backend_row.set_selected(backend_ids.index(cur_backend))
        except ValueError:
            backend_row.set_selected(0)

        def on_backend(row, _p):
            idx = row.get_selected()
            if 0 <= idx < len(backend_ids):
                self.config.set("animated.backend", backend_ids[idx])

        backend_row.connect("notify::selected", on_backend)
        anim_group.add(backend_row)

        page.add(anim_group)

        win.add(page)
        win.present()

    def _update_count(self):
        # Called externally if needed
        pass

    def _test_random_now(self):
        try:
            from wallora.main import apply_random_wallpaper
            apply_random_wallpaper(silent=False)
            self._show_toast("Ustawiono losową tapetę")
        except Exception as e:
            self._show_toast(f"Błąd: {e}")

    def _on_test_restore_animated(self, *_):
        ok, msg = self.animated.restore()
        self._show_toast(msg)
