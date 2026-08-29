# Changelog

## 0.2.12 — tapeta po przelogowaniu, nie zombie z poprzedniej sesji

### Naprawione
- `mpv` startuje w nowej sesji procesowej, więc **przeżywa wylogowanie**. Po kolejnym logowaniu restore widział żywy PID, pisał „już działa” i **nie odpalał odtwarzacza** — okno było na martwym XWayland (`saw=False` w `desktop_pin.log`)
- Restore wymaga okna na **bieżącym** `DISPLAY` / istniejącego `XAUTHORITY`. Stary proces jest zabijany i tapeta startuje od nowa
- Przy końcu sesji graficznej `ExecStop=wallora --release-session` gasi odtwarzacz, ale zostawia autostart na następne logowanie

## 0.2.11 — restore nie wyłącza sam siebie po logowaniu

### Naprawione
- `wallora --restore-animated` wołał `set_animated()` → `stop()` → `systemctl disable --now` i **zabijał własną usługę** zanim mpv zdążył wystartować. Stan tapety i autostart znikały przy każdym restarcie
- Plakat (`set_wallpaper`) też wywoływał pełny `stop()` i kasował przywracanie
- Usługa startowała z `WantedBy=default.target` (za wcześnie, bez `DISPLAY`/`WAYLAND_DISPLAY`). Teraz `WantedBy=graphical-session.target` i restore importuje środowisko z menedżera użytkownika / gnome-shell
- Status „przywracanie aktywne” nie kłamie już, gdy plik `.service` leży na dysku, ale `systemctl is-enabled` jest `disabled`

## 0.2.10 — restore po logowaniu na GNOME 50

### Naprawione
- GNOME 50 pomijało `org.wallora.Wallora.animated.desktop`, bo miało `X-GNOME-Autostart-Phase` (traktowane jako usługa sesji, której gnome-session już nie startuje). systemd-xdg-autostart-generator dopisywał wtedy `NotShowIn=GNOME` — tapeta nie wracała po zalogowaniu
- Autostart jest zwykłą aplikacją XDG + własna usługa `wallora-restore-animated.service` (`WantedBy=default.target`)
- Restore zapisuje `~/.cache/wallora/restore.log` i nie odpala dwóch odtwarzaczy naraz
- Stary PID z poprzedniej sesji nie blokuje restore (sprawdzane jest, czy proces to naprawdę tapeta Wallory)

## 0.2.9 — kopia ulubionych poza systemem

### Dodane
- Folder kopii ulubionych (drugi dysk / Nextcloud / Dokumenty / pendrive) — tapety przeżywają reinstalację
- Preferencje: wybór folderu, auto-kopiowanie przy ★, „Skopiuj obecne ulubione”, „Przywróć z folderu”
- Przycisk w sidebarze: „Zapisz kopię ulubionej…”
- Indeks `wallora-favorites.json` w folderze kopii + `CZYTAJ-MNIE.txt`
- Po dodaniu tego folderu do biblioteki ulubione wracają same
- CLI: `--export-favorites [FOLDER]`, `--restore-favorites [FOLDER]`

## 0.2.8 — restore po restarcie znowu jako tapeta, nie okno

### Naprawione
- Po logowaniu animacja nie zostaje zwykłym oknem mpv: `wallora --restore-animated` wychodził zanim wątek zdążył ustawić `_NET_WM_WINDOW_TYPE_DESKTOP`
- Przypinanie okna jest teraz **osobnym procesem** (`python -m wallora.animated --pin-desktop`) — przeżywa koniec restore i ponawia wskazówki przez ~90 s (GNOME/XWayland resetuje je przy starcie)
- Restore czeka na GNOME Shell + XWayland/EWMH zanim odpali mpv
- Jeśli player już działa jako zwykłe okno, restore przypina go ponownie zamiast nic nie robić

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
