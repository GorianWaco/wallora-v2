#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Wallora Flatpak Build ==="

if ! command -v flatpak-builder &> /dev/null; then
    echo "flatpak-builder not found."
    echo ""
    echo "Please install it using your package manager:"
    echo "  Arch / Manjaro:     sudo pacman -S flatpak-builder"
    echo "  Fedora:             sudo dnf install flatpak-builder"
    echo "  Ubuntu / Debian:    sudo apt install flatpak-builder"
    echo "  openSUSE:           sudo zypper install flatpak-builder"
    echo ""
    echo "After installing, run this script again."
    exit 1
fi

# Make sure we have the runtime (will download if missing)
echo "Ensuring GNOME runtime is available..."
flatpak install --user -y flathub org.gnome.Platform//47 org.gnome.Sdk//47 || true

REPO_DIR="flatpak-repo"
BUNDLE="Wallora.flatpak"

echo ""
echo "Building Flatpak into local repository..."
flatpak-builder --force-clean --repo=$REPO_DIR build-dir flatpak/org.wallora.Wallora.yaml

echo ""
echo "Creating distributable bundle: $BUNDLE"
flatpak build-bundle --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo \
    $REPO_DIR $BUNDLE org.wallora.Wallora

echo ""
echo "=== SUCCESS ==="
echo "Bundle ready: $BUNDLE ($(du -h $BUNDLE | cut -f1))"
echo ""
echo "Wysyłka do znajomych:"
echo "  - Wyślij plik: $BUNDLE"
echo "  - Opcjonalnie dołącz plik: INSTALL-FOR-FRIENDS.txt"
echo ""
echo "Znajomi instalują poleceniem:"
echo "  flatpak install --user $BUNDLE"
echo ""
echo "Uruchomienie:"
echo "  flatpak run org.wallora.Wallora"
echo ""
echo "Lokalna instalacja na tej maszynie:"
echo "  flatpak install --user $BUNDLE"
