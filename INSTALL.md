# Instalacja Wallory 2

Wallora 2 to menedżer tapet na Linuxa (statyczne + animowane: MP4, WebM, GIF…).
Najprościej zainstalować ją jako **Flatpak** z GitHub Releases.

Pakiet: https://github.com/GorianWaco/wallora-v2/releases/latest

---

## 1. Jedna komenda (zalecane)

W terminalu:

```bash
curl -fsSL https://raw.githubusercontent.com/GorianWaco/wallora-v2/main/install.sh | bash
```

Skrypt:

1. sprawdzi, czy masz Flatpak (w razie potrzeby zainstaluje)
2. doda Flathub (środowisko GNOME)
3. pobierze najnowszy `Wallora-*.flatpak`
4. zainstaluje Wallorę dla Twojego użytkownika
5. dołoży kodeki do animowanych tapet

Przy **pierwszym razie** Flatpak dociągnie runtime GNOME (~400 MB). Potem odpalasz z menu (**Wallora 2**) albo:

```bash
flatpak run org.wallora.Wallora
```

Aktualizacja — ta sama komenda jeszcze raz.

Odinstalowanie:

```bash
curl -fsSL https://raw.githubusercontent.com/GorianWaco/wallora-v2/main/install.sh | bash -s -- --uninstall
```

---

## 2. Instalacja ręczna (plik .flatpak)

### Krok 1 — Flatpak

| Dystrybucja | Polecenie |
|---|---|
| Arch / CachyOS / Manjaro | `sudo pacman -S flatpak` |
| Fedora | `sudo dnf install flatpak` |
| Ubuntu / Debian | `sudo apt install flatpak` |
| openSUSE | `sudo zypper install flatpak` |

Po instalacji Flatpaka **wyloguj się i zaloguj** (albo zrestartuj), żeby aplikacje pojawiły się w menu.

### Krok 2 — Flathub

```bash
flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

### Krok 3 — pobierz paczkę

Wejdź na:

https://github.com/GorianWaco/wallora-v2/releases/latest

i pobierz plik **`Wallora-0.2.12.flatpak`** (nazwa wersji może być nowsza).

### Krok 4 — zainstaluj

W katalogu z pobranym plikiem:

```bash
flatpak install --user Wallora-0.2.12.flatpak
```

albo ze skryptu, bez pobierania z sieci:

```bash
./install.sh --local Wallora-0.2.12.flatpak
```

### Krok 5 — uruchom

Menu aplikacji → **Wallora 2**, albo:

```bash
flatpak run org.wallora.Wallora
```

### Kodeki wideo (animowane tapety)

Jeśli animacja się nie odtwarza:

```bash
flatpak install --user flathub org.freedesktop.Platform.codecs-extra//25.08-extra
```

Skrypt `install.sh` robi to sam.

---

## 3. Z źródeł (dla deweloperów)

```bash
git clone https://github.com/GorianWaco/wallora-v2.git
cd wallora-v2
./run
```

Zależności (Arch / CachyOS):

```bash
sudo pacman -S python-gobject python-pillow gtk4 libadwaita ffmpeg \
  gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav mpv
```

Opcjonalnie na Hyprland/Sway: `mpvpaper`.

Złożenie własnego Flatpaka:

```bash
./build-flatpak.sh
```

Powstanie `Wallora-<wersja>.flatpak`.

---

## Aktualizacja i odinstalowanie

| Co | Jak |
|---|---|
| Aktualizacja | ten sam `install.sh` albo nowszy `.flatpak` |
| Odinstalowanie | `./install.sh --uninstall` albo `flatpak uninstall --user org.wallora.Wallora` |
| Dane użytkownika | zostają w `~/.config/wallora/` i `~/.var/app/org.wallora.Wallora/` |

---

## Problemy

**„Nie znaleziono polecenia flatpak”**  
Zainstaluj Flatpak z tabeli w kroku 1 i wyloguj się.

**Pierwsza instalacja trwa długo**  
Normalne — dociągany jest GNOME Platform (~400 MB, raz).

**Animowana tapeta nie gra**  
Dołóż kodeki (`codecs-extra`, komenda wyżej). Na GNOME Wayland Wallora używa okna X11 Desktop (XWayland), nie warstwy Mutter.

**Aplikacja nie widać w menu**  
`flatpak run org.wallora.Wallora` albo wyloguj się po pierwszej instalacji Flatpaka.
