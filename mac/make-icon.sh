#!/bin/bash
# Regenerate VoxDemo.icns from icon.svg.html.
#
# The icon is checked in, so this only needs running when the artwork changes.
# It renders the SVG with a headless Chrome — the one HyperFrames already
# downloaded into ~/.cache/puppeteer — so there is nothing extra to install.
set -euo pipefail
cd "$(dirname "$0")"

SRC="icon.svg.html"
PNG="${TMPDIR:-/tmp}/voxdemo-icon.png"
ICONSET="${TMPDIR:-/tmp}/VoxDemo.iconset"

CHROME="$(find "$HOME/.cache/puppeteer/chrome-headless-shell" -type f \
          -name chrome-headless-shell 2>/dev/null | sort | tail -1 || true)"
if [ -z "$CHROME" ]; then
  echo "No chrome-headless-shell in ~/.cache/puppeteer." >&2
  echo "Render $SRC at 1024x1024 with a transparent background to $PNG, then re-run." >&2
  exit 1
fi

"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --default-background-color=00000000 \
  --screenshot="$PNG" --window-size=1024,1024 \
  "file://$(pwd)/$SRC" 2>/dev/null

rm -rf "$ICONSET" && mkdir -p "$ICONSET"
sips -z 16 16     "$PNG" --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z 32 32     "$PNG" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z 32 32     "$PNG" --out "$ICONSET/icon_32x32.png"      >/dev/null
sips -z 64 64     "$PNG" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z 128 128   "$PNG" --out "$ICONSET/icon_128x128.png"    >/dev/null
sips -z 256 256   "$PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$PNG" --out "$ICONSET/icon_256x256.png"    >/dev/null
sips -z 512 512   "$PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$PNG" --out "$ICONSET/icon_512x512.png"    >/dev/null
cp "$PNG"           "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o VoxDemo.icns
echo "wrote $(pwd)/VoxDemo.icns"
