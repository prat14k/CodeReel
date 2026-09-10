#!/bin/bash
# Build VoxDemo.app. The .app is a thin shell — it launches ../server.py from the
# repo it was built in (changeable in the app's status bar).
set -euo pipefail
cd "$(dirname "$0")"

APP="build/VoxDemo.app"
swift build -c release
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/VoxDemo "$APP/Contents/MacOS/VoxDemo"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>VoxDemo</string>
  <key>CFBundleDisplayName</key><string>VoxDemo</string>
  <key>CFBundleExecutable</key><string>VoxDemo</string>
  <key>CFBundleIdentifier</key><string>com.bornwest.voxdemo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>VoxDemo records a short clip of your voice so it can be cloned for demo narration.</string>
</dict>
</plist>
PLIST

# Ad-hoc signature: enough for the microphone prompt. Note that re-signing resets
# the microphone grant, so macOS asks again after a rebuild.
codesign --force --sign - --identifier com.bornwest.voxdemo "$APP" >/dev/null

echo "built $(pwd)/$APP"
