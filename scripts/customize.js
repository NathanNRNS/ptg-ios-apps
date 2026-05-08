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
  appId: app.ascBundleId || app.bundleId || `com.practicetestgeeks.${slug.replace(/-/g, '')}`,
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

console.log(`Customized for: ${app.ascName || app.name}`);
console.log(`Bundle ID: ${config.appId}`);
console.log(`URL: ${app.url}`);
