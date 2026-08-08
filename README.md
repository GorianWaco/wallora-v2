# Wallora 2 — menedżer tapet z animacjami

To jest **nowa wersja (0.2.0)** Wallory. Stara wersja (0.1.x) pozostaje nietknięta w:

```text
~/Projekty/wallora/
```

Ten katalog:

```text
~/Projekty/wallora-v2/
```

## Co nowego w v2

- 🎬 **Animowane tapety** — MP4, WebM, MKV, MOV, AVI, GIF
- 🖼️ Miniatury wideo przez **ffmpeg**
- ▶️ Podgląd wideo w UI (GTK `MediaFile`)
- 🖥️ Backendy animacji:
  - **gtk-player** — wbudowany odtwarzacz (domyślny na GNOME)
  - **mpvpaper** — Hyprland / Sway (wlroots)
  - **mpv-window** — pełnoekranowe okno mpv
  - **xwinwrap + mpv** — X11
- 🧩 Klatka plakatu (ffmpeg) ustawiana jako statyczna tapeta DE (Overview / lock screen)
- ⚙️ Preferencje: wyciszenie, pętla, backend, slideshow bez wideo

Statyczne obrazy działają jak w v1 (korekcja, skalowanie, slideshow).

## Uruchomienie

```bash
cd ~/Projekty/wallora-v2
./run
```

lub:

```bash
cd ~/Projekty/wallora-v2
PYTHONPATH=src python3 -m wallora.main
```

CLI:

```bash
./run --random                    # losowa statyczna tapeta
./run --restore-animated          # wznów animację po restarcie (autostart)
./run --stop-animated             # zatrzymaj animowaną tapetę (+ wyłącz autostart)
./run --import-steam-profiles     # tła profilu Steam → biblioteka
./run --import-steam-profiles --equipped-only  # tylko ustawione w profilu (+ historia)
./run --import-steam-profiles --force   # nadpisz już pobrane
./run --help
```

### Po restarcie systemu

Animowana tapeta to osobny proces — po reboocie znika, zostaje tylko klatka plakatu.
Od **0.2.6** przy ustawieniu animacji tworzony jest autostart:

`~/.config/autostart/org.wallora.Wallora.animated.desktop`

który uruchamia `wallora --restore-animated` przy logowaniu.  
Zatrzymanie animacji w UI usuwa ten autostart.

### Tła profilu Steam na pulpicie

Animowane tła z Point Shop nie leżą jako zwykłe pliki. Wallora:

1. Skanuje cache Steam + dociąga z CDN
2. **Domyślnie tylko HD** (odrzuca miniatury sklepu 300×168)
3. Zapisuje do `~/.cache/wallora/steam-profiles/`
4. Dodaje folder do biblioteki

```bash
./run --import-steam-profiles           # tylko HD
./run --import-steam-profiles --force   # nadpisz
./run --import-steam-profiles --all-quality  # także podglądy
```

W UI: **Import ze Steam (profil)** → filtr **Animowane 🎬** → ustaw.

**Więcej teł HD w cache:** w Steam otwórz profil z animowanym tłem albo duży podgląd w Point Shop (nie sam kafelek listy).

### GNOME (animowana tapeta)

Na GNOME Wayland natywne wideo w warstwie pulpitu nie jest wspierane przez Mutter. Wallora odpala player jako okno **X11 Desktop** (XWayland): pod innymi oknami, poza paskiem zadań, kliknięcia przechodzą na wierzch.

Jeśli nadal wygląda to jak „odtwarzacz”, użyj backendu w Preferencjach albo rozszerzenia shell (np. Hanabi / GNOME Wallpaper Engine) i wskazuj pliki z `~/.cache/wallora/steam-profiles/`.

## Zależności (CachyOS / Arch)

Wymagane (jak v1):

```bash
sudo pacman -S python-gobject python-pillow gtk4 libadwaita
```

Zalecane do animacji:

```bash
sudo pacman -S ffmpeg
# dekodery GStreamer (podgląd GTK + gtk-player)
sudo pacman -S gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav

# opcjonalnie — lepsze backendy
sudo pacman -S mpv              # backend mpv-window
sudo pacman -S mpvpaper         # Hyprland/Sway (NIE działa na GNOME/Mutter)
```

## GNOME (CachyOS)

Domyślny backend to **wbudowany odtwarzacz GTK**: pełnoekranowe okna wideo „pod spodem” + klatka plakatu w `gsettings`.

Uwagi:

- `mpvpaper` działa tylko na compositorach **wlroots** (Hyprland/Sway), nie na GNOME.
- Animacja zużywa GPU/CPU — długie 4K webm może być cięższe niż statyczna tapeta.
- Przycisk **„Zatrzymaj animację”** w sidebarze (lub `./run --stop-animated`).

## Struktura

```text
wallora-v2/
├── src/wallora/
│   ├── animated.py          # menedżer backendów animacji
│   ├── animated_player.py   # odłączony player GTK
│   ├── window.py
│   ├── library.py           # skan obrazów + wideo
│   └── ...
├── run
└── README.md
```

## Konfiguracja

Współdzielona z v1 (te same foldery biblioteki):

```text
~/.config/wallora/config.json
~/.cache/wallora/
```

Nowe klucze `animated.*` są ignorowane przez starą wersję.

## Stara wersja

```bash
cd ~/Projekty/wallora && ./run
```

## Licencja

MIT
