// Package browser UI and public runtime assets.
const path = require('node:path')
const fs = require('node:fs')
const Babel = require('@babel/standalone')

const root = path.resolve(__dirname, '..')
const live = process.argv.includes('--live')
const output = path.join(root, '.local-data', live ? 'web-app' : 'vercel-preview')
const assets = path.join(output, 'public')
fs.mkdirSync(assets, { recursive: true })
for (const [destination, source] of Object.entries({
  'index.html': 'web/index.html',
  'workspace.css': 'electron/workspace.css',
  'react.js': 'node_modules/react/umd/react.production.min.js',
  'react-dom.js': 'node_modules/react-dom/umd/react-dom.production.min.js',
  'marked.js': 'node_modules/marked/lib/marked.umd.js',
  'purify.js': 'node_modules/dompurify/dist/purify.min.js',
})) {
  fs.copyFileSync(path.join(root, source), path.join(assets, destination))
}
const entry = ['electron/HomePanel.jsx', 'electron/WorkspacePanel.jsx', live ? 'web/app.jsx' : 'web/preview.jsx']
  .map(source => fs.readFileSync(path.join(root, source), 'utf8')).join('\n')
fs.writeFileSync(path.join(assets, 'app.js'), Babel.transform(entry, { presets: ['react'] }).code)
if (!live) fs.writeFileSync(path.join(output, 'vercel.json'), JSON.stringify({
  framework: null,
  outputDirectory: 'public',
  headers: [{ source: '/(.*)', headers: [{
    key: 'Content-Security-Policy',
    value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  }] }],
}))
if (!live) fs.writeFileSync(path.join(output, '.vercelignore'), '**\n!vercel.json\n!public/\n!public/**\n')
console.log(output)
