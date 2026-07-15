#!/usr/bin/env node
// Customize capacitor.config.json for a specific app
// Usage: node customize.js <slug>
const fs = require('fs');
const path = require('path');

const slug = process.argv[2];
if (!slug) { console.error('Usage: node customize.js <slug>'); process.exit(1); }

const appsFile = path.join(__dirname, '..', 'apps.json');
const apps = JSON.parse(fs.readFileSync(appsFile, 'utf8'));
const app = apps[slug];
if (!app) { console.error(`App "${slug}" not found`); process.exit(1); }

const config = {
  appId: app.bundleId || `com.practicetestgeeks.${slug.replace(/-/g, '')}`,
  appName: app.ascName || app.name,
  webDir: 'dist',
  server: {
    url: app.url,
    cleartext: false,
    allowNavigation: ['practicetestgeeks.com', '*.practicetestgeeks.com']
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#1a73e8',
      showSpinner: true,
      spinnerColor: '#ffffff',
      iosSpinnerStyle: 'large'
    }
  }
};

fs.writeFileSync(
  path.join(__dirname, '..', 'capacitor.config.json'),
  JSON.stringify(config, null, 2)
);

// Update dist/index.html placeholders
const htmlPath = path.join(__dirname, '..', 'dist', 'index.html');
if (fs.existsSync(htmlPath)) {
  let html = fs.readFileSync(htmlPath, 'utf8');
  const displayName = app.ascName || app.name;
  html = html
    .replace(/APP_NAME_PLACEHOLDER/g, displayName)
    .replace(/WEBVIEW_URL_PLACEHOLDER/g, app.url)
    .replace(/APP_ID_PLACEHOLDER/g, app.ascAppId || '');
  fs.writeFileSync(htmlPath, html);
}

// Patch the native Info.plist's CFBundleDisplayName. `npx cap add ios` no-ops
// once ios/ already exists in the checkout (it's committed to git), and
// `cap sync` never touches this key — so without this, every build ships
// whatever display name was baked in when ios/ was first generated.
const plistPath = path.join(__dirname, '..', 'ios', 'App', 'App', 'Info.plist');
if (fs.existsSync(plistPath)) {
  let plist = fs.readFileSync(plistPath, 'utf8');
  const displayName = (app.ascName || app.name)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const before = plist;
  plist = plist.replace(
    /(<key>CFBundleDisplayName<\/key>\s*<string>)[^<]*(<\/string>)/,
    `$1${displayName}$2`
  );
  if (plist === before) {
    console.error('WARNING: CFBundleDisplayName key not found/replaced in Info.plist');
  } else {
    fs.writeFileSync(plistPath, plist);
    console.log(`Patched CFBundleDisplayName -> "${displayName}"`);
  }
} else {
  console.error('WARNING: Info.plist not found yet (expected before cap sync)');
}

console.log(`Customized for: ${app.ascName || app.name}`);
console.log(`Bundle ID: ${config.appId}`);
console.log(`URL: ${app.url}`);
