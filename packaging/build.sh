#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <linux|macos>" >&2
  exit 1
fi

target="$1"
if [[ "$target" != "linux" && "$target" != "macos" ]]; then
  echo "Unsupported target: $target (expected 'linux' or 'macos')" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

version="$(python -c "from moviq import __version__; print(__version__)")"
if [[ -z "$version" ]]; then
  echo "Could not read moviq.__version__" >&2
  exit 1
fi

output_dir="$repo_root/release/dist/v${version}"
mkdir -p "$output_dir"

python -m pip install -r packaging/requirements-build.txt
pyinstaller packaging/moviq.spec

if [[ "$target" == "linux" ]]; then
  tar -C dist -czf "$output_dir/Moviq-${version}-linux-x64.tar.gz" Moviq

  app_dir="$repo_root/release/build/AppDir"
  rm -rf "$app_dir"
  mkdir -p "$app_dir/usr/bin" "$app_dir/usr/share/applications"
  cp -a dist/Moviq/. "$app_dir/usr/bin/"
  cp "$repo_root/assets/moviq-icon-512.png" "$app_dir/moviq.png"
  cat > "$app_dir/usr/share/applications/moviq.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Moviq
Comment=Rename movie and TV files for Jellyfin and Plex
Exec=moviq
Icon=moviq
Categories=Utility;
Terminal=false
EOF
  cp "$app_dir/usr/share/applications/moviq.desktop" "$app_dir/moviq.desktop"
  cat > "$app_dir/AppRun" <<'EOF'
#!/bin/sh
set -eu
APPDIR="$(dirname "$(readlink -f "$0")")"
exec "$APPDIR/usr/bin/moviq" "$@"
EOF
  chmod +x "$app_dir/AppRun"

  appimage_tool="$repo_root/release/build/appimagetool-x86_64.AppImage"
  curl -fL --retry 3 "https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage" -o "$appimage_tool"
  chmod +x "$appimage_tool"
  ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$appimage_tool" "$app_dir" "$output_dir/Moviq-${version}-linux-x64.AppImage"
fi

if [[ "$target" == "macos" ]]; then
  arch="$(uname -m)"
  case "$arch" in
    arm64|aarch64)
      arch_name="arm64"
      ;;
    x86_64|amd64)
      arch_name="x64"
      ;;
    *)
      echo "Unsupported macOS architecture: $arch" >&2
      exit 1
      ;;
  esac

  dmg_path="$output_dir/Moviq-${version}-macos-${arch_name}.dmg"
  for attempt in $(seq 1 5); do
    hdiutil detach -force /Volumes/Moviq >/dev/null 2>&1 || true
    rm -f "$dmg_path"
    if hdiutil create -volname "Moviq" -srcfolder "dist/Moviq.app" -format UDZO -fs HFS+ -ov -o "$dmg_path"; then
      break
    fi
    if [[ "$attempt" -lt 5 ]]; then
      sleep 10
    else
      echo "Failed to create macOS DMG after 5 attempts" >&2
      exit 1
    fi
  done
fi
