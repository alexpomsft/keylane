#!/usr/bin/env bash
# Fetch a prebuilt layer-shell-qt into vendor/layershellqt (development fallback).
#
# Prefer the distro package (Arch: `pacman -S layer-shell-qt`); Keylane finds a
# system install first. Use this only where that is not possible. The binary
# links against Qt's private Wayland APIs, so it must be built for the same Qt
# minor version as your PySide6/system Qt. This script takes the current Arch
# (x86_64) / Arch Linux ARM (aarch64) package, which matches Arch's Qt.
#
#   scripts/fetch-layershell.sh [DEST]     # default: <repo>/vendor/layershellqt
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${1:-$root/vendor/layershellqt}"

if [[ -f /usr/lib/qt6/qml/org/kde/layershell/qmldir ]]; then
    echo "layer-shell-qt is already installed system-wide; nothing to do."
    exit 0
fi

case "$(uname -m)" in
    x86_64)  repo="https://geo.mirror.pkgbuild.com/extra/os/x86_64" ;;
    aarch64) repo="http://mirror.archlinuxarm.org/aarch64/extra" ;;
    *) echo "unsupported architecture $(uname -m); build layer-shell-qt from source" >&2; exit 1 ;;
esac

for tool in curl tar; do
    command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

pkg="$(curl -fsSL "$repo/" | grep -oE 'layer-shell-qt-[0-9][^"<>]*\.pkg\.tar\.(zst|xz)' | grep -v '\.sig$' | sort -uV | tail -n1)"
[[ -n "$pkg" ]] || { echo "could not find layer-shell-qt in $repo" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "downloading $pkg"
curl -fsSL -o "$tmp/$pkg" "$repo/$pkg"
tar -C "$tmp" -xf "$tmp/$pkg" usr/lib

rm -rf "$dest"
mkdir -p "$dest"
cp -a "$tmp/usr/lib" "$dest/lib"
echo "installed into $dest"
[[ -f "$dest/lib/qt6/qml/org/kde/layershell/qmldir" ]] || { echo "unexpected package layout" >&2; exit 1; }
