#!/bin/bash
# Build CodeReel.app. The .app is a thin shell — it launches ../server.py from the
# repo it was built in (changeable in the app's status bar).
set -euo pipefail
cd "$(dirname "$0")"

APP="build/CodeReel.app"
# --disable-sandbox: SwiftPM evaluates Package.swift inside its own sandbox-exec
# profile, and macOS refuses to apply a nested restrictive profile — the build
# dies with "sandbox-exec: sandbox_apply: Operation not permitted" before it even
# looks at the sources. This shell is sandboxed, so the flag is required here and
# harmless anywhere else.
swift build -c release --disable-sandbox
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/CodeReel "$APP/Contents/MacOS/CodeReel"
if [ -f CodeReel.icns ]; then
  cp CodeReel.icns "$APP/Contents/Resources/CodeReel.icns"
elif [ -f VoxDemo.icns ]; then
  cp VoxDemo.icns "$APP/Contents/Resources/CodeReel.icns"
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>CodeReel</string>
  <key>CFBundleDisplayName</key><string>CodeReel</string>
  <key>CFBundleExecutable</key><string>CodeReel</string>
  <key>CFBundleIdentifier</key><string>com.bornwest.codereel</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>CodeReel</string>
  <key>CFBundleShortVersionString</key><string>0.2</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>CodeReel records a short clip of your voice so it can be cloned for demo narration.</string>
</dict>
</plist>
PLIST

# Ad-hoc signature: enough for the microphone prompt. Note that re-signing resets
# the microphone grant, so macOS asks again after a rebuild.
codesign --force --sign - --identifier com.bornwest.codereel "$APP" >/dev/null

echo "built $(pwd)/$APP"
