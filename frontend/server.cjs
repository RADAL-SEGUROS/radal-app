// Minimal static server for the built Vite SPA (runs under Lambda Web Adapter).
// .cjs because the frontend package.json sets "type": "module" (ESM) for Vite,
// but this tiny server uses CommonJS require().
const express = require('express');
const path = require('path');

const app = express();
const dist = path.join(__dirname, 'dist');

app.use(express.static(dist, {
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('index.html')) res.setHeader('Cache-Control', 'no-cache');
  },
}));

// SPA fallback
app.get('*', (_req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.sendFile(path.join(dist, 'index.html'));
});

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`radal frontend listening on ${port}`));
