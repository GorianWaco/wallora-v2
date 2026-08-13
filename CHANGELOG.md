# Changelog

## 0.2.7 — import tapet ze Steam (ekwipunek + wideo)

### Naprawione
- Import nie kasuje już prawdziwych teł jako „za słabe” (odrzuca tylko kafelki sklepu ~300×168)
- W GUI widać postęp importu zamiast milczenia przez kilka minut
- Pobierane są też **MP4** (nie tylko webm)
- Animowana tapeta nie jest już oknem nad pulpitem: kursor zostaje, mysz przechodzi na wylot (GNOME Wayland / XWayland)

### Dodane
- Import **ekwipunku** Steam (zalogowany klient): posiadane tła profilu
- Przycisk: „Import tapet ze Steam”

## 0.2.6 — przywracanie animowanej tapety po restarcie

### Dodane
- Autostart `org.wallora.Wallora.animated.desktop` przy ustawieniu animacji
- CLI: `wallora --restore-animated` (używane przy logowaniu)
- Stan w `~/.cache/wallora/animated_state.json` jest wznawiany po restarcie
- „Zatrzymaj animację” usuwa autostart (nie wraca po restarcie)
- Preferencje: status przywracania + przycisk testowy

## 0.2.5 — auto-import tła ustawionego w profilu Steam

### Dodane
- Odczyt **aktualnie ustawionego** tła profilu z `localconfig.vdf` + publicznego API Steam
- **Historia**: każde kolejne tło, które ustawisz w profilu, zostaje zapamiętane i w bibliotece
- **Auto-import przy starcie** Wallory (+ co 10 min gdy app jest otwarta) — bez klikania
- Preferencje: przełącznik „Auto-import tła z profilu Steam”
- CLI: `--import-steam-profiles --equipped-only`

### Jak zbierać kupione tła
1. Steam → Edycja profilu → ustaw tło → Zapisz  
2. Wallora sama ściąga je z CDN przy starcie / pollu  
3. Zmień na kolejne tło i zapisz — dołączy do historii

## 0.2.4 — brak fałszywych crashy NVIDIA przy zmianie tapety

### Poprawione
- `animated_player`: przy SIGTERM/wyjściu zatrzymuje `Gtk.MediaFile` i robi `os._exit` zamiast `Py_Exit` — omija race GStreamer gstgl + sterownik NVIDIA (`_glFenceSync` SEGV)
- System (DrKonqi / „Usługa wysypała się”) nie zgłasza już awarii przy każdym przełączeniu animowanej tapety
- `animated.stop()`: po SIGTERM czeka chwilę, potem SIGKILL jeśli proces nie zejdzie

## 0.2.3 — pętla animowanej tapety

### Poprawione
- `Gtk.MediaFile.set_loop()` nie działa dla WebM/VP9 (zatrzymuje na ostatniej klatce) — ręczny restart `seek(0)+play()`
- `app.hold()` — player nie wychodzi po pierwszym odtworzeniu (okna typu DESKTOP nie trzymają refcount GTK)
- Backend **mpv-desktop** (`--loop-file=inf`) gdy `mpv` jest zainstalowane — najpewniejsza pętla na GNOME

## 0.2.2 — jakość Steam HD + prawdziwa tapeta na GNOME

### Poprawione
- Import Steam domyślnie **tylko HD** (≥ ~720p) — odrzuca podglądy Point Shop 300×168
- Preferencja CDN (czyste 1920×1080) + weryfikacja `ffprobe`
- Usuwanie starych podglądów z `~/.cache/wallora/steam-profiles/`
- Player animacji: okno typu **Desktop** przez XWayland (keep-below, skip-taskbar, click-through) zamiast zwykłego fullscreen „odtwarzacza”
- Skalowanie wideo: `COVER` (wypełnia ekran)

### CLI
- `--import-steam-profiles` (HD)
- `--import-steam-profiles --all-quality` (także miniatury)

## 0.2.1 — import teł profilu Steam

### Dodane
- Moduł `steam_import.py` — skan cache Chromium Steama + pobieranie z CDN
- Folder docelowy: `~/.cache/wallora/steam-profiles/`
- UI: przycisk **Import ze Steam (profil)** (sidebar + Preferencje)
- CLI: `./run --import-steam-profiles` (`--force` nadpisuje istniejące)

## 0.2.0 — animowane tapety (wallora-v2)

Nowa linia kodu w `~/Projekty/wallora-v2` — **nie zastępuje** `~/Projekty/wallora` (0.1.x).

### Dodane
- Obsługa plików wideo/animowanych: `.mp4`, `.webm`, `.mkv`, `.mov`, `.avi`, `.m4v`, `.ogv`, `.gif`
- Moduł `animated.py` — wybór backendu i sterowanie procesem
- Moduł `animated_player.py` — odłączony odtwarzacz GTK (przeżywa zamknięcie UI)
- Backendy: `gtk-player`, `mpvpaper`, `mpv-window`, `xwinwrap+mpv`
- Miniatury wideo (ffmpeg) + plakat (poster frame) jako statyczna tapeta DE
- Filtr „Animowane 🎬” w bibliotece
- Preferencje: wyciszenie, pętla, poster, backend, wideo w slideshow
- CLI: `--stop-animated`

### Bez zmian względem v1
- Korekcja obrazu, skalowanie, slideshow dla statycznych tapet
- Współdzielona konfiguracja `~/.config/wallora/`

## 0.1.0

- Pierwsza wersja (tylko obrazy statyczne) — `~/Projekty/wallora`
