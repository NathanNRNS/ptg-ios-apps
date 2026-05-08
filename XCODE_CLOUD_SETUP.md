# Xcode Cloud Setup

Free CI/CD via Xcode Cloud (25 hrs/month free per Apple Developer account).
Replaces GitHub Actions for iOS builds — no payment required.

## One-time setup (manual, ~10 min in Xcode + ASC)

### 1. Connect repository to Xcode Cloud

In **App Store Connect** → any app → **Xcode Cloud** tab:
- Click **Get Started**
- Choose **GitHub** as the source control provider
- Authorize the GitHub OAuth app and grant access to `NathanNRNS/ptg-ios-apps`
- Apple will install the Xcode Cloud GitHub app on the repo

### 2. Create the Workflow

In Xcode Cloud, create **one workflow** (used for all 70 apps):

| Field | Value |
|-------|-------|
| Name | `Build & Distribute` |
| Description | Universal build for all PTG apps |
| Project / Workspace | `ios/App/App.xcworkspace` |
| Scheme | `App` |

**Start Conditions:**
- Trigger: **Branch Changes**
- Pattern: `build/*` (branches matching this trigger builds)

**Environment:**
- Xcode Version: **Latest Release** (auto-updates)
- macOS Version: **Latest Release**

**Actions → Archive:**
- Configuration: **Release**
- Platform: **iOS**

**Post-Actions → TestFlight Internal Testing:**
- Distribute to: **App Store Connect Internal Testing**
- Add appropriate tester groups

### 3. Save & Test

Trigger first build manually:
```bash
./scripts/xcode-cloud-trigger.sh toefl
```

This pushes branch `build/toefl` → Xcode Cloud detects it → runs `ci_scripts/ci_post_clone.sh` → builds → uploads to TestFlight.

## How `ci_post_clone.sh` works

When Xcode Cloud clones the repo:
1. Reads `APP_SLUG` from `$CI_BRANCH` (`build/toefl` → `toefl`)
2. Runs `node scripts/customize.js $APP_SLUG` → updates `capacitor.config.json`
3. Runs `npx cap sync ios` → updates iOS project web assets
4. Patches `ios/App/App.xcodeproj/project.pbxproj` → sets correct `PRODUCT_BUNDLE_IDENTIFIER`
5. Patches `ios/App/App/Info.plist` → sets correct `CFBundleDisplayName`
6. Generates icons via `@capacitor/assets`
7. Runs `pod install` for CocoaPods deps
8. Xcode Cloud then archives + uploads

## Triggering builds

```bash
# Single app
./scripts/xcode-cloud-trigger.sh toefl

# Wave 2 (all 41 new apps)
./scripts/xcode-cloud-trigger.sh --batch2

# Everything
./scripts/xcode-cloud-trigger.sh --all
```

Each invocation force-pushes branches `build/<slug>` based on current `main`.
Xcode Cloud detects each branch push and starts a parallel build.

## Cost & limits

- **Free**: 25 compute hours/month (Apple Developer account)
- Each build: ~3-5 minutes for a Capacitor WebView app
- 41 apps × 4 min = 2.7 hours per full rebuild = ~11% of free quota
- Past free tier: $14.99/mo for 100 hours, $49.99/mo for 250 hours

## Build status & logs

- App Store Connect → Apps → (any app) → Xcode Cloud → Builds
- Or: API access via App Store Connect API key (already configured)

## Compared to GitHub Actions

| Feature | GitHub Actions | Xcode Cloud |
|---------|----------------|-------------|
| macOS minutes (free) | 0 (paid only on private repos) | 25 hrs/month free |
| iOS-specific tooling | Manual (Xcode select, fastlane) | Built-in (Xcode + ASC integration) |
| Code signing | Manual (App Store Connect API key passed via env) | Automatic (Apple Developer account) |
| TestFlight upload | fastlane upload_to_testflight | Built-in post-action |
| Parallel builds | Up to 5 (free tier) / 20 (paid) | Configurable per workflow |
