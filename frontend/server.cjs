// Minimal static server for the built Vite SPA (runs under Lambda Web Adapter).
// .cjs because the frontend package.json sets "type": "module" (ESM) for Vite,
// but this tiny server uses CommonJS require().
const express = require('express');
const path = require('path');

const app = express();
const dist = path.join(__dirname, 'dist');

// Vite fingerprints everything under /assets (content hash in the filename), so those files
// never change and CloudFront may keep them for a year. Without this, express's default
// `max-age=0` made CloudFront re-fetch every chunk from this Lambda on every page load, and a
// single visit could exhaust the account's Lambda concurrency (429s seen on dev, 2026-10-17).
app.use(express.static(dist, {
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('index.html')) {
      res.setHeader('Cache-Control', 'no-cache');
    } else if (filePath.includes(`${path.sep}assets${path.sep}`)) {
      res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    }
  },
}));

// SPA fallback
app.get('*', (_req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.sendFile(path.join(dist, 'index.html'));
});

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`radal frontend listening on ${port}`));
