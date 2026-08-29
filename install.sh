#!/usr/bin/env bash
# Instalator Wallory 2 (Flatpak) z GitHub Releases.
#
# Znajomi:
#   curl -fsSL https://raw.githubusercontent.com/GorianWaco/wallora-v2/main/install.sh | bash
#
# Z katalogu projektu / po pobraniu skryptu:
#   ./install.sh
#   ./install.sh --local Wallora-0.2.12.flatpak
#   ./install.sh --uninstall

set -euo pipefail

REPO="GorianWaco/wallora-v2"
APP_ID="org.wallora.Wallora"
FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"
CODECS_EXTRA="org.freedesktop.Platform.codecs-extra//25.08-extra"

usage() {
    cat <<EOF
Użycie: $(basename "$0") [opcje]

  (bez opcji)     pobierz najnowszy Flatpak z GitHuba i zainstaluj
  --local PLIK    zainstaluj istniejący plik .flatpak
  --uninstall     odinstaluj Wallorę
  -h, --help      ta pomoc

Przykład dla znajomych:
  curl -fsSL https://raw.githubusercontent.com/GorianWaco/wallora-v2/main/install.sh | bash
EOF
}

say()  { printf '%s\n' "$*"; }
info() { printf '==> %s\n' "$*"; }
die()  { printf 'Błąd: %s\n' "$*" >&2; exit 1; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Brak polecenia: $1"
}

install_flatpak_pkg() {
    info "Instaluję Flatpak…"
    if [ "$(id -u)" -eq 0 ]; then
        SUDO=()
    elif command -v sudo >/dev/null 2>&1; then
        SUDO=(sudo)
    else
        die "Nie mam sudo — zainstaluj ręcznie pakiet „flatpak”, potem uruchom ten skrypt ponownie."
    fi

    if command -v pacman >/dev/null 2>&1; then
        "${SUDO[@]}" pacman -S --noconfirm --needed flatpak
    elif command -v dnf >/dev/null 2>&1; then
        "${SUDO[@]}" dnf install -y flatpak
    elif command -v apt-get >/dev/null 2>&1; then
        "${SUDO[@]}" apt-get update -y
        "${SUDO[@]}" apt-get install -y flatpak
    elif command -v zypper >/dev/null 2>&1; then
        "${SUDO[@]}" zypper --non-interactive install flatpak
    else
        die "Nie znam tej dystrybucji. Zainstaluj Flatpak i odpal skrypt jeszcze raz."
    fi
}

ensure_flatpak() {
    if command -v flatpak >/dev/null 2>&1; then
        return
    fi
    install_flatpak_pkg
    command -v flatpak >/dev/null 2>&1 || die "Flatpak nadal nie jest w PATH (wyloguj się i zaloguj, potem spróbuj ponownie)."
}

ensure_flathub() {
    if flatpak remotes 2>/dev/null | awk '{print $1}' | grep -qx flathub; then
        return
    fi
    info "Dodaję Flathub (runtime GNOME)…"
    flatpak remote-add --if-not-exists --user flathub "$FLATHUB_REPO"
}

latest_flatpak_url() {
    local json url
    need_cmd curl
    json=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest") || \
        die "Nie udało się odczytać GitHub Releases."
    url=$(printf '%s' "$json" | grep -oE "https://github.com/${REPO}/releases/download/[^\"[:space:]]+\\.flatpak" | head -n1)
    [ -n "$url" ] || die "W najnowszym release nie ma pliku .flatpak."
    printf '%s\n' "$url"
}

download_flatpak() {
    local url dest
    url=$(latest_flatpak_url)
    dest="$1/$(basename "$url")"
    info "Pobieram $(basename "$url")…"
    curl -fL --progress-bar -o "$dest" "$url"
    printf '%s\n' "$dest"
}

install_bundle() {
    local bundle=$1
    local leftover="${HOME}/.local/share/flatpak/app/${APP_ID}"
    [ -f "$bundle" ] || die "Nie ma pliku: $bundle"

    if flatpak info --user "$APP_ID" >/dev/null 2>&1; then
        info "Znaleziono poprzednią instalację — podmieniam na tę z paczki…"
        flatpak uninstall --user -y "$APP_ID" >/dev/null 2>&1 || true
    fi
    # Przerwana instalacja zostawia niepusty katalog i blokuje kolejną.
    if [ -e "$leftover" ]; then
        rm -rf "$leftover"
    fi

    info "Instaluję Wallorę (pierwszy raz dociągnie GNOME ~400 MB)…"
    flatpak install --user -y "$bundle"
}

install_codecs() {
    info "Dokładam kodeki wideo (animowane tapety)…"
    flatpak install --user -y flathub "$CODECS_EXTRA" || \
        say "Uwaga: nie udało się dołożyć $CODECS_EXTRA — statyczne tapety i tak działają."
}

uninstall_app() {
    ensure_flatpak
    if flatpak info --user "$APP_ID" >/dev/null 2>&1; then
        info "Odinstalowuję $APP_ID…"
        flatpak uninstall --user -y "$APP_ID"
        say "Gotowe."
    else
        say "Wallora nie jest zainstalowana (user)."
    fi
}

MODE=install
LOCAL_BUNDLE=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --uninstall) MODE=uninstall; shift ;;
        --local)
            [ $# -ge 2 ] || die "--local wymaga ścieżki do pliku .flatpak"
            LOCAL_BUNDLE=$2
            shift 2
            ;;
        *)
            die "Nieznana opcja: $1 (zobacz --help)"
            ;;
    esac
done

if [ "$MODE" = uninstall ]; then
    uninstall_app
    exit 0
fi

say "=== Instalator Wallory 2 ==="
ensure_flatpak
ensure_flathub

TMP=
cleanup() {
    if [ -n "${TMP:-}" ]; then
        rm -rf "$TMP"
    fi
}
trap cleanup EXIT

if [ -n "$LOCAL_BUNDLE" ]; then
    BUNDLE=$LOCAL_BUNDLE
else
    TMP=$(mktemp -d /tmp/wallora-install.XXXXXX)
    BUNDLE=$(download_flatpak "$TMP")
fi

install_bundle "$BUNDLE"
install_codecs

say ""
say "=== Gotowe ==="
say "Uruchom z menu aplikacji (Wallora 2) albo:"
say "  flatpak run $APP_ID"
say ""
say "Aktualizacja: odpal ten sam skrypt jeszcze raz."
