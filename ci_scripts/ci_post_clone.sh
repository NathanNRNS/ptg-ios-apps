#!/bin/sh
# Xcode Cloud post-clone script.
# Reads APP_SLUG from CI_BRANCH (build/<slug>) or CI_XCODEBUILD_ACTION env vars,
# customizes Capacitor config + Xcode project for that app, then syncs.
#
# Set up workflow in App Store Connect to trigger on `build/*` branch pattern.
# Each branch named `build/<slug>` (e.g. `build/toefl`) builds that app.

set -euo pipefail

cd "$CI_PRIMARY_REPOSITORY_PATH" || exit 1

# Derive APP_SLUG from branch name `build/<slug>` if not explicitly set
if [ -z "${APP_SLUG:-}" ]; then
    if [ -n "${CI_BRANCH:-}" ] && echo "$CI_BRANCH" | grep -q '^build/'; then
        APP_SLUG="${CI_BRANCH#build/}"
    elif [ -n "${CI_TAG:-}" ] && echo "$CI_TAG" | grep -q '^build-'; then
        APP_SLUG="${CI_TAG#build-}"
    else
        echo "APP_SLUG not set and not in branch/tag (build/<slug> or tag build-<slug>)"
        exit 1
    fi
fi

echo "==> Building app: $APP_SLUG"
echo "==> Branch: ${CI_BRANCH:-N/A}, Tag: ${CI_TAG:-N/A}"

# Install Node.js dependencies (Xcode Cloud has Node 20 preinstalled)
echo "==> npm install"
npm install --prefer-offline --no-audit

# Customize Capacitor config for this app
echo "==> Customize for $APP_SLUG"
node scripts/customize.js "$APP_SLUG"

# Extract bundle ID + display name from apps.json (Capacitor wrote them to capacitor.config.json)
BUNDLE_ID=$(node -e "console.log(require('./capacitor.config.json').appId)")
DISPLAY_NAME=$(node -e "console.log(require('./capacitor.config.json').appName)")
ASC_BUNDLE_ID=$(node -e "const a=require('./apps.json'); console.log(a['$APP_SLUG'].ascBundleId || a['$APP_SLUG'].bundleId)")

echo "==> Bundle ID: $BUNDLE_ID"
echo "==> ASC Bundle ID: $ASC_BUNDLE_ID"
echo "==> Display Name: $DISPLAY_NAME"

# Sync Capacitor (updates ios/App/App/capacitor.config.json + web assets)
echo "==> npx cap sync ios"
npx cap sync ios

# Update Xcode project bundle identifier (was set when ios/ was first generated)
PROJECT_FILE="ios/App/App.xcodeproj/project.pbxproj"
if [ -f "$PROJECT_FILE" ]; then
    # Use ASC Bundle ID for signing (matches Apple Developer portal registration)
    sed -i.bak -E "s|PRODUCT_BUNDLE_IDENTIFIER = [^;]+;|PRODUCT_BUNDLE_IDENTIFIER = $ASC_BUNDLE_ID;|g" "$PROJECT_FILE"
    rm -f "${PROJECT_FILE}.bak"
    echo "==> Updated PRODUCT_BUNDLE_IDENTIFIER to $ASC_BUNDLE_ID"
fi

# Update Info.plist display name
PLIST_FILE="ios/App/App/Info.plist"
if [ -f "$PLIST_FILE" ] && command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $DISPLAY_NAME" "$PLIST_FILE" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $DISPLAY_NAME" "$PLIST_FILE"
    echo "==> Updated CFBundleDisplayName to $DISPLAY_NAME"
fi

# Generate icon assets from icons/<slug>.png
if [ -f "icons/${APP_SLUG}.png" ]; then
    echo "==> Generating icons"
    mkdir -p resources
    cp "icons/${APP_SLUG}.png" resources/icon.png
    npx @capacitor/assets generate --ios 2>&1 || echo "  (icon gen warning, non-fatal)"
fi

# Install CocoaPods deps (Xcode Cloud has cocoapods preinstalled)
cd ios/App
echo "==> pod install"
pod install --no-repo-update || pod install
cd "$CI_PRIMARY_REPOSITORY_PATH"

echo "==> ci_post_clone.sh complete for $APP_SLUG"
