#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(python3 -c "import pathlib,re; t=pathlib.Path('src/wallora/__init__.py').read_text(); print(re.search(r'__version__\\s*=\\s*\"([^\"]+)\"', t).group(1))")
BUNDLE="Wallora-${VERSION}.flatpak"
REPO_DIR="flatpak-repo"
MANIFEST="flatpak/org.wallora.Wallora.yaml"

echo "=== Wallora ${VERSION} Flatpak Build ==="

BUILDER=()
if command -v flatpak-builder >/dev/null 2>&1; then
    BUILDER=(flatpak-builder)
elif flatpak info --user org.flatpak.Builder >/dev/null 2>&1 || flatpak info org.flatpak.Builder >/dev/null 2>&1; then
    # org.flatpak.Builder uses ~/.var/app/... as user dir; point it at the host
    # user installation so it sees the already-installed GNOME SDK/Platform.
    BUILDER=(
        flatpak run
        --filesystem=home
        --share=network
        --env=FLATPAK_USER_DIR="${HOME}/.local/share/flatpak"
        --command=flatpak-builder
        org.flatpak.Builder
    )
else
    echo "Nie znaleziono flatpak-builder."
    echo ""
    echo "Zainstaluj jedno z:"
    echo "  sudo pacman -S flatpak-builder"
    echo "  flatpak install --user flathub org.flatpak.Builder"
    exit 1
fi

echo "Using: ${BUILDER[*]}"
echo "Ensuring GNOME 50 runtime/SDK..."
flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50 \
    org.freedesktop.Platform.codecs-extra//25.08-extra || true

echo ""
echo "Building into local repository ($REPO_DIR)..."
"${BUILDER[@]}" --force-clean --ccache --user \
    --repo="$REPO_DIR" build-dir "$MANIFEST"

echo ""
echo "Creating distributable bundle: $BUNDLE"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    "$REPO_DIR" "$BUNDLE" org.wallora.Wallora

ln -sfn "$BUNDLE" Wallora.flatpak

echo ""
echo "=== SUCCESS ==="
echo "Pakiet: $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"
echo ""
echo "Instalacja u znajomych:"
echo "  1. Pobierz $BUNDLE z GitHub Releases"
echo "  2. flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo"
echo "  3. flatpak install --user $BUNDLE"
echo "  4. flatpak run org.wallora.Wallora"
echo ""
echo "Lokalnie na tej maszynie:"
echo "  flatpak install --user --reinstall $BUNDLE"
echo "  flatpak run org.wallora.Wallora"
