const {
  app,
  dialog,
  shell,
  BrowserWindow,
  globalShortcut,
  screen,
  ipcMain,
  session,
  Tray,
  Menu,
  nativeImage,
} = require('electron')
const { WebSocketServer } = require('ws')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const { randomBytes, randomUUID } = require('crypto')

const bridgeToken = randomBytes(32).toString('hex')
const transcriptRequests = new Map()
const PRODUCT_NAME = 'Context to Action by Ipsita'
const projectRoot = path.resolve(__dirname, '..')
const dataDirectory = path.resolve(projectRoot, process.env.CONTEXT_AGENT_DATA_DIR || '.local-data')
const bridgePort = Number(process.env.CONTEXT_AGENT_BRIDGE_PORT || 0)
const MAX_TRANSCRIPT_BYTES = 1024 * 1024

app.setName(PRODUCT_NAME)
const rendererProfile = path.join(dataDirectory, 'electron-profile')
fs.mkdirSync(rendererProfile, { recursive: true, mode: 0o700 })
app.setPath('userData', rendererProfile)
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
app.commandLine.appendSwitch('enable-features', 'SharedArrayBuffer')

let orbWindow = null
let settingsWindow = null
let liveCallWindow = null
let mainCallWindow = null
let tray = null
let pythonProc = null
let wss = null
let isQuitting = false
const wsClients = new Set()

// App state — drives tray label
let appState = 'paused' // 'listening' | 'paused'

// ── Tray icon ─────────────────────────────────────────────────────────────────

function makeProductIcon(size, tray = false) {
  const pixels = Buffer.alloc(size * size * 4)
  const ink = tray ? [0, 0, 0] : [95, 61, 40]
  const paper = [255, 235, 210]
  function paint(x, y, color, alpha = 255) {
    const offset = (y * size + x) * 4
    pixels[offset] = color[2]
    pixels[offset + 1] = color[1]
    pixels[offset + 2] = color[0]
    pixels[offset + 3] = alpha
  }
  function stroke(ax, ay, bx, by, width, x, y) {
    const vx = bx - ax, vy = by - ay
    const t = Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
    return Math.hypot(x - ax - t * vx, y - ay - t * vy) < width
  }
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const px = (x + .5) / size, py = (y + .5) / size
      const edge = Math.hypot(Math.max(Math.abs(px - .5) - .26, 0), Math.max(Math.abs(py - .5) - .26, 0))
      if (!tray && edge < .15) paint(x, y, paper)
      const mark = stroke(.29, .64, .47, .47, .035, px, py) || stroke(.47, .47, .68, .47, .035, px, py)
        || stroke(.59, .38, .68, .47, .035, px, py) || stroke(.68, .47, .59, .56, .035, px, py)
        || Math.hypot(px - .29, py - .64) < .062
      if (mark) paint(x, y, ink)
    }
  }
  const image = nativeImage.createFromBitmap(pixels, { width: size, height: size })
  if (tray) image.setTemplateImage(true)
  return image
}

function buildTrayMenu() {
  const stateLabel = appState === 'listening' ? 'Listening' : 'Paused'
  const stateMeta  = appState === 'listening'
    ? 'Microphone capture requested'
    : 'Microphone off'

  return Menu.buildFromTemplate([
    { label: `${PRODUCT_NAME} — ${stateLabel}`, enabled: false },
    { label: stateMeta, enabled: false },
    { type: 'separator' },
    {
      label: appState === 'listening' ? 'Stop listening' : 'Start listening',
      accelerator: 'Alt+Shift+P',
      click: togglePause,
    },
    {
      label: 'Try a sample alert',
      accelerator: 'Alt+Space',
      click: () => sendToPython({ type: 'inject_canned' }),
    },
    {
      label: 'Finish session',
      accelerator: 'Command+Shift+E',
      click: () => sendToPython({ type: 'end_call' }),
    },
    { type: 'separator' },
    {
      label: 'Settings…',
      click: openSettings,
    },
    {
      label: 'Open workspace…',
      accelerator: 'Command+Shift+M',
      click: openMainCall,
    },
    { type: 'separator' },
    {
      label: `Quit ${PRODUCT_NAME}`,
      accelerator: 'Command+Q',
      click: () => app.quit(),
    },
  ])
}

function createTray() {
  tray = new Tray(nativeImage.createEmpty())
  tray.setTitle('Context')
  tray.setToolTip(PRODUCT_NAME)
  tray.setContextMenu(buildTrayMenu())
  tray.on('click', () => tray.popUpContextMenu())
  console.log('[tray] created')
}

function refreshTrayMenu() {
  if (tray) tray.setContextMenu(buildTrayMenu())
}

function togglePause() {
  appState = appState === 'listening' ? 'paused' : 'listening'
  sendToPython({ type: appState === 'paused' ? 'pause' : 'resume' })
  if (orbWindow) orbWindow.webContents.send('chief-state', appState)
  if (mainCallWindow) mainCallWindow.webContents.send('chief-state', appState)
  refreshTrayMenu()
  console.log(`[workspace] microphone state -> ${appState}`)
}

// ── Settings window ───────────────────────────────────────────────────────────

function openSettings() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.focus()
    return
  }
  settingsWindow = new BrowserWindow({
    width: 640,
    height: 640,
    frame: false,
    resizable: false,
    alwaysOnTop: false,
    focusable: true,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  settingsWindow.loadFile(path.join(__dirname, 'settings.html'))
  settingsWindow.on('closed', () => { settingsWindow = null })
}

// ── Live Call window ─────────────────────────────────────────────────────────

function openLiveCall() {
  if (liveCallWindow && !liveCallWindow.isDestroyed()) {
    liveCallWindow.show()
    liveCallWindow.focus()
    return
  }
  liveCallWindow = new BrowserWindow({
    width: 480,
    height: 620,
    frame: false,
    resizable: false,
    alwaysOnTop: false,
    focusable: true,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  liveCallWindow.loadFile(path.join(__dirname, 'live-call.html'))
  liveCallWindow.on('closed', () => { liveCallWindow = null })
}

// ── Main Call window ─────────────────────────────────────────────────────────

function openMainCall() {
  if (mainCallWindow && !mainCallWindow.isDestroyed()) {
    mainCallWindow.show()
    mainCallWindow.focus()
    return
  }
  mainCallWindow = new BrowserWindow({
    title: PRODUCT_NAME,
    width: 880,
    height: 740,
    frame: false,
    resizable: true,
    minWidth: 420,
    minHeight: 520,
    alwaysOnTop: false,
    focusable: true,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  mainCallWindow.webContents.once('did-finish-load', () => console.log('[workspace] window loaded'))
  mainCallWindow.loadFile(path.join(__dirname, 'main-call.html')).catch(error => {
    console.error('[workspace] could not load:', error.message)
  })
  // The Main Call window is treated as Chief's "I'm listening" indicator —
  // if the user closes it, Chief is no longer overhearing, so the app
  // quits. (Cleanup-driven close paths set isQuitting first so this
  // handler doesn't recurse into quit twice.)
  mainCallWindow.on('closed', () => {
    mainCallWindow = null
    if (!isQuitting) {
      isQuitting = true
      app.quit()
    }
  })
}

// ── Orb window ────────────────────────────────────────────────────────────────

const ORB_PILL = { w: 160, h: 52 }
// Initial placeholder before the renderer measures the actual card. Make it
// generous so a tall whisper isn't clipped during the ~1 frame before
// fitOrb arrives. The window is transparent — extra empty space is invisible.
const ORB_CARD = { w: 320, h: 480 }
// Window must always be at least as large as the pill so the orb itself
// never gets clipped on the way back.
const ORB_MIN = { w: 160, h: 52 }
const ORB_MAX = { w: 520, h: 600 }

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }

function resizeOrb(size) {
  if (!orbWindow) return
  const w = clamp(Math.round(size.w), ORB_MIN.w, ORB_MAX.w)
  const h = clamp(Math.round(size.h), ORB_MIN.h, ORB_MAX.h)
  const [x, y] = orbWindow.getPosition()
  const [cw, ch] = orbWindow.getSize()
  // Keep bottom-right corner fixed
  const nx = x + cw - w
  const ny = y + ch - h
  orbWindow.setSize(w, h)
  orbWindow.setPosition(nx, ny)
}

function createOrb() {
  const display = screen.getPrimaryDisplay()
  const { width, height } = display.workArea
  const W = 160
  const H = 52

  orbWindow = new BrowserWindow({
    width: W,
    height: H,
    x: width - W - 24,
    y: height - H - 24,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    focusable: false,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  orbWindow.setAlwaysOnTop(true, 'screen-saver')
  orbWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  orbWindow.loadFile(path.join(__dirname, 'orb.html'))

  orbWindow.webContents.on('console-message', (_e, level, message) => {
    const tag = ['v', 'log', 'warn', 'err'][level] || 'log'
    console.log(`[orb:${tag}] ${message}`)
  })
}

// ── Python child process ──────────────────────────────────────────────────────

function startPython(port) {
  const repo = path.resolve(__dirname, '..')
  const venvPy = path.join(repo, '.venv', 'bin', 'python')
  const py = fs.existsSync(venvPy) ? venvPy : 'python3'
  const script = path.join('scripts', 'live_loop.py')

  pythonProc = spawn(py, [script], {
    cwd: repo,
    env: { ...process.env, PYTHONUNBUFFERED: '1', CONTEXT_AGENT_DESKTOP: '1', CONTEXT_AGENT_BRIDGE_TOKEN: bridgeToken, CONTEXT_AGENT_BRIDGE_PORT: String(port),
      CONTEXT_AGENT_DATA_DIR: dataDirectory },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  pythonProc.stdout.on('data', (d) => process.stdout.write(`[py] ${d}`))
  pythonProc.stderr.on('data', (d) => process.stderr.write(`[py-err] ${d}`))
  pythonProc.on('exit', (code, sig) => {
    console.log(`[py] exited code=${code} sig=${sig}`)
  })
}

// ── WebSocket server ──────────────────────────────────────────────────────────

function startWsServer() {
  wss = new WebSocketServer({ host: '127.0.0.1', port: bridgePort, maxPayload: 16 * 1024 * 1024 })
  wss.on('connection', (ws, request) => {
    if (request.url !== '/?token=' + bridgeToken) {
      ws.close(1008, 'Unauthorized')
      return
    }
    wsClients.add(ws)
    if (appState === 'listening') ws.send(JSON.stringify({ type: 'resume' }))
    console.log(`[ws] client connected (${wsClients.size} total)`)
    ws.on('message', (data) => {
      let event
      try { event = JSON.parse(data.toString()) } catch { return }
      if (event.type === 'transcript_result') {
        transcriptRequests.get(event.id)?.(event)
        return
      }
      if (!orbWindow) return
      if (event.type === 'whisper') {
        resizeOrb(ORB_CARD)
        // Forward content + optional provenance so the orb can render
        // a dim `↳ memory · …` line under the whisper text.
        orbWindow.webContents.send('whisper', {
          content: event.content,
          provenance: event.provenance || null,
        })
      } else if (event.type === 'action_card') {
        resizeOrb(ORB_CARD)
        orbWindow.webContents.send('action_card', event)
      } else if (event.type === 'email_outcome') {
        orbWindow.webContents.send('email-outcome', event)
      } else if (event.type === 'mute_mic') {
        orbWindow.webContents.send('mute-mic')
      } else if (event.type === 'unmute_mic') {
        orbWindow.webContents.send('unmute-mic')
      } else if (event.type === 'memories_list') {
        if (settingsWindow && !settingsWindow.isDestroyed()) {
          settingsWindow.webContents.send('memories_list', event)
        }
      } else if (event.type === 'voice_status') {
        if (settingsWindow && !settingsWindow.isDestroyed()) {
          settingsWindow.webContents.send('voice_status', event)
        }
      } else if (event.type === 'counterparty_status') {
        if (settingsWindow && !settingsWindow.isDestroyed()) {
          settingsWindow.webContents.send('counterparty_status', event)
        }
      } else if (event.type === 'retrieval_status') {
        if (settingsWindow && !settingsWindow.isDestroyed()) {
          settingsWindow.webContents.send('retrieval_status', event)
        }
      } else if (event.type === 'phone_status') {
        // Auto-pop the Side Call window the first time a side call goes
        // non-idle, so the user doesn't have to remember to open it.
        if (['dialing', 'ringing', 'connected'].includes(event.state)) {
          if (!liveCallWindow || liveCallWindow.isDestroyed()) openLiveCall()
        }
        if (liveCallWindow && !liveCallWindow.isDestroyed()) {
          liveCallWindow.webContents.send('phone_status', event)
        }
      } else if (event.type === 'main_call_status') {
        // Auto-pop the Main Call dashboard on the first utterance of a new
        // conversation, so the user doesn't have to remember to open it.
        if (event.state === 'active') {
          if (!mainCallWindow || mainCallWindow.isDestroyed()) openMainCall()
        }
        if (mainCallWindow && !mainCallWindow.isDestroyed()) {
          mainCallWindow.webContents.send('main_call_status', event)
        }
      }
    })
    ws.on('close', () => {
      wsClients.delete(ws)
      console.log(`[ws] client disconnected (${wsClients.size} remaining)`)
    })
    ws.on('error', (e) => console.warn('[ws] error', e.message))
  })
  wss.on('error', error => console.warn('[ws] listener unavailable:', error.code || error.name))
  wss.on('listening', () => {
    const { port } = wss.address()
    console.log(`[workspace] Local bridge ready on port ${port}`)
    startPython(port)
  })
}

function sendToPython(event) {
  const msg = JSON.stringify(event)
  let delivered = 0
  for (const ws of wsClients) {
    if (ws.readyState === 1) { ws.send(msg); delivered++ }
  }
  return delivered > 0
}

// ── IPC ───────────────────────────────────────────────────────────────────────

function transcriptRequest(operation, fields = {}) {
  return new Promise((resolve) => {
    const id = randomUUID()
    const finish = (reply) => {
      clearTimeout(timeout)
      transcriptRequests.delete(id)
      resolve(reply)
    }
    const timeout = setTimeout(
      () => finish({ ok: false, error: 'The local worker did not respond.' }),
      ['fact_check', 'approve_saved', 'chat'].includes(operation) ? 45000 : 8000,
    )
    transcriptRequests.set(id, finish)
    if (!sendToPython({ type: 'transcripts', id, operation, ...fields })) {
      finish({ ok: false, error: 'The local worker is starting. Try again shortly.' })
    }
  })
}

ipcMain.handle('context-feedback', (event, fields) => {
  if (event.sender !== mainCallWindow?.webContents || !fields ||
      typeof fields.rating !== 'string' || typeof fields.correction !== 'string' ||
      fields.correction.length > 1000 || !Array.isArray(fields.sourceIds) ||
      fields.sourceIds.length > 8 || !fields.sourceIds.every(id => typeof id === 'string' && /^[a-f0-9]{64}$/.test(id))) {
    return { ok: false, error: 'Enter valid feedback.' }
  }
  if (!['chat', 'alert', 'insight'].includes(fields.kind) ||
      typeof fields.reference !== 'string' || !/^[a-f0-9-]{36}$/.test(fields.reference)) {
    return { ok: false, error: 'This feedback needs a valid event.' }
  }
  return transcriptRequest('feedback', {
    kind: fields.kind, reference: fields.reference,
    rating: fields.rating, correction: fields.correction, source_ids: fields.sourceIds,
    preference: fields.preference,
  })
})

ipcMain.handle('review-email-draft', (event, fields) => {
  if (event.sender !== mainCallWindow?.webContents || !fields ||
      typeof fields.subject !== 'string' || fields.subject.length > 200 ||
      typeof fields.body !== 'string' || fields.body.length > 8000 ||
      !Array.isArray(fields.sourceIds) ||
      fields.sourceIds.length > 8 || !fields.sourceIds.every(id => typeof id === 'string' && /^[a-f0-9]{64}$/.test(id))) {
    return { ok: false, error: 'Enter a valid draft and subject.' }
  }
  return transcriptRequest('email_draft', { subject: fields.subject, body: fields.body, source_ids: fields.sourceIds })
})

ipcMain.handle('grounded-chat', (event, question, useModel) => {
  if (event.sender !== mainCallWindow?.webContents || typeof question !== 'string' || question.length > 2000) {
    return { ok: false, error: 'Enter a question up to 2,000 characters.' }
  }
  return transcriptRequest('chat', { question, use_model: useModel === true })
})

ipcMain.handle('workspace-records', (event, operation, fields = {}) => {
  if (event.sender !== mainCallWindow?.webContents || !fields || typeof fields !== 'object') {
    return { ok: false, error: 'Open the workspace.' }
  }
  const uuid = value => typeof value === 'string' && /^[a-f0-9-]{36}$/.test(value)
  if (operation === 'workspace') return transcriptRequest(operation)
  if (operation === 'fact_check' && uuid(fields.insight_id) && /^[a-f0-9]{64}$/.test(fields.source_id)) {
    return transcriptRequest(operation, { source_id: fields.source_id, insight_id: fields.insight_id })
  }
  if (operation === 'review_saved' && uuid(fields.action_id) &&
      [['recipient', 254], ['subject', 200], ['body', 8000]].every(([key, max]) =>
        typeof fields[key] === 'string' && fields[key].length <= max)) {
    const { action_id, recipient, subject, body } = fields
    return transcriptRequest(operation, { action_id, recipient, subject, body })
  }
  if (['cancel_review', 'approve_saved'].includes(operation) && uuid(fields.action_id) &&
      typeof fields.token === 'string' && /^[a-f0-9]{32}$/.test(fields.token)) {
    return transcriptRequest(operation, { action_id: fields.action_id, token: fields.token })
  }
  return { ok: false, error: 'This request is invalid.' }
})

ipcMain.handle('open-source-link', async (event, url) => {
  if (event.sender !== mainCallWindow?.webContents || typeof url !== 'string') return
  const target = new URL(url)
  if (['https:', 'http:'].includes(target.protocol)) await shell.openExternal(target.href)
})

ipcMain.handle('transcripts', async (event, operation, sourceId) => {
  if (event.sender !== mainCallWindow?.webContents) return { ok: false, error: 'Open the learning workspace.' }
  if (operation === 'list') return transcriptRequest(operation)
  if (['preview', 'delete', 'retry_analysis'].includes(operation)) {
    if (typeof sourceId !== 'string' || !/^[a-f0-9]{64}$/.test(sourceId)) {
      return { ok: false, error: 'Invalid transcript source.' }
    }
    return transcriptRequest(operation, { source_id: sourceId })
  }
  if (operation !== 'import') return { ok: false, error: 'Unknown transcript operation.' }

  try {
    const selection = await dialog.showOpenDialog(mainCallWindow, {
      properties: ['openFile'],
      filters: [{ name: 'Transcripts', extensions: ['txt', 'md'] }],
    })
    if (selection.canceled) return { ok: true, cancelled: true }
    const filename = selection.filePaths[0]
    const file = await fs.promises.open(filename, 'r')
    try {
      const info = await file.stat()
      if (!info.isFile()) return { ok: false, error: 'Choose a regular text file.' }
      const buffer = Buffer.alloc(MAX_TRANSCRIPT_BYTES + 1)
      let total = 0
      while (total < buffer.length) {
        const { bytesRead } = await file.read(buffer, total, buffer.length - total)
        if (!bytesRead) break
        total += bytesRead
      }
      if (total > MAX_TRANSCRIPT_BYTES) return { ok: false, error: 'Transcript exceeds 1 MB.' }
      const text = new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, total))
      return transcriptRequest(operation, { filename: path.basename(filename), text })
    } finally {
      await file.close()
    }
  } catch {
    return { ok: false, error: 'Could not read the transcript. Choose a UTF-8 text file.' }
  }
})

ipcMain.on('stt-partial', (_e, text) => {
  sendToPython({ type: 'stt_partial', text })
})
ipcMain.on('stt-final', (_e, text) => {
  sendToPython({ type: 'stt_final', text })
})
ipcMain.on('audio-utterance', (event, payload) => {
  if (event.sender !== orbWindow?.webContents || appState !== 'listening') return
  sendToPython({ type: 'audio_utterance', ...payload })
})
for (const operation of ['approve', 'discard']) {
  ipcMain.on(operation + '-action', (event, approvalId) => {
    if (event.sender !== orbWindow?.webContents || typeof approvalId !== 'string') return
    sendToPython({ type: operation === 'approve' ? 'approve_action' : 'dismiss_action', approval_id: approvalId })
  })
}
ipcMain.on('toggle-listening', (event) => {
  if (event.sender === mainCallWindow?.webContents) togglePause()
})
ipcMain.on('open-settings', () => openSettings())
ipcMain.on('orb-move', (_e, dx, dy) => {
  if (!orbWindow) return
  const [x, y] = orbWindow.getPosition()
  orbWindow.setPosition(x + dx, y + dy)
})
ipcMain.on('close-settings', () => { if (settingsWindow) settingsWindow.close() })
ipcMain.on('list-memories', (_e, limit) => {
  sendToPython({ type: 'list_memories', limit: limit || 50 })
})
ipcMain.on('voice-status-request', () => {
  sendToPython({ type: 'voice_status_request' })
})
ipcMain.on('enroll-voice', (_e, payload) => {
  sendToPython({ type: 'enroll_voice', ...payload })
})
ipcMain.on('clear-voice', () => {
  sendToPython({ type: 'clear_voice' })
})
ipcMain.on('set-counterparty', (_e, hint) => {
  sendToPython({ type: 'set_counterparty', hint: hint || '' })
})
ipcMain.on('counterparty-status-request', () => {
  sendToPython({ type: 'counterparty_status_request' })
})
ipcMain.on('retrieval-status-request', () => {
  sendToPython({ type: 'retrieval_status_request' })
})
ipcMain.on('phone-status-request', () => {
  sendToPython({ type: 'phone_status_request' })
})
ipcMain.on('phone-hangup', () => {
  sendToPython({ type: 'phone_hangup' })
})
ipcMain.on('phone-reset', () => {
  sendToPython({ type: 'phone_reset' })
})
ipcMain.on('open-live-call', () => openLiveCall())
ipcMain.on('close-live-call', () => { if (liveCallWindow) liveCallWindow.close() })
ipcMain.on('open-main-call', () => openMainCall())
ipcMain.on('close-main-call', () => { if (mainCallWindow) mainCallWindow.close() })
ipcMain.on('main-call-status-request', () => {
  sendToPython({ type: 'main_call_status_request' })
})
ipcMain.on('main-call-end', () => {
  sendToPython({ type: 'main_call_end' })
})
ipcMain.on('main-call-reset', () => {
  sendToPython({ type: 'main_call_reset' })
})
ipcMain.on('orb-collapsed', () => resizeOrb(ORB_PILL))
ipcMain.on('orb-fit', (_e, { width, height }) => {
  if (!width || !height) return
  resizeOrb({ w: width, h: height })
})
ipcMain.on('dismiss-card', () => {
  if (orbWindow) orbWindow.webContents.send('dismiss')
})

// Config read/write — .env in project root
const { ipcMain: _ipc } = require('electron')
const ENV_PATH = path.resolve(__dirname, '..', '.env')

function readEnv() {
  try {
    const lines = fs.readFileSync(ENV_PATH, 'utf8').split('\n')
    const out = {}
    for (const line of lines) {
      const m = line.match(/^([A-Z_]+)=(.*)$/)
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '')
    }
    return out
  } catch { return {} }
}

function writeEnvKey(key, value) {
  let content = ''
  try { content = fs.readFileSync(ENV_PATH, 'utf8') } catch {}
  const re = new RegExp(`^${key}=.*$`, 'm')
  if (re.test(content)) {
    content = content.replace(re, () => `${key}=${value}`)
  } else {
    content = content.trimEnd() + `\n${key}=${value}\n`
  }
  fs.writeFileSync(ENV_PATH, content, { mode: 0o600 })
}

const publicSettings = new Set(['USER_NAME', 'DEFAULT_COUNTERPARTY'])
const providerSettings = new Set([
  'ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY', 'MOSS_PROJECT_ID', 'MOSS_PROJECT_KEY',
  'SUPERMEMORY_API_KEY', 'TAVILY_API_KEY', 'AGENTMAIL_API_KEY', 'AGENTMAIL_INBOX_ID',
  'CONTEXT_AGENT_TEST_RECIPIENT', 'LANGSMITH_API_KEY',
])
ipcMain.handle('get-config', event => {
  if (event.sender !== settingsWindow?.webContents) return {}
  const config = readEnv()
  return Object.fromEntries([...publicSettings, ...providerSettings].map(key => [
    key, publicSettings.has(key) ? config[key] || '' : Boolean(config[key]),
  ]))
})
ipcMain.handle('set-config-key', (event, key, value) => {
  if (event.sender !== settingsWindow?.webContents || !(publicSettings.has(key) || providerSettings.has(key)) ||
      typeof value !== 'string' || /[\r\n\0]/.test(value)) return false
  writeEnvKey(key, value)
  return true
})
ipcMain.handle('get-app-state', () => ({ state: appState }))

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((sender, permission, callback, details) => {
    const activeOrb = sender === orbWindow?.webContents && appState === 'listening'
    const voiceSettings = sender === settingsWindow?.webContents
    callback(permission === 'media' && details.mediaTypes?.every(type => type === 'audio') &&
      Boolean(activeOrb || voiceSettings))
  })

  // Keep app alive when all windows close (tray-only mode)
  // Show Chief in the Dock alongside the tray icon. The Main Call window is
  // the primary surface now, so Dock presence is what the user expects.
  try { app.setActivationPolicy('regular') } catch (e) { console.warn('[dock] setActivationPolicy failed:', e.message) }
  if (app.dock) {
    try {
      app.dock.setIcon(makeProductIcon(256))
      console.log('[dock] icon set')
    } catch (e) {
      console.warn('[dock] setIcon failed:', e.message)
    }
    // Some macOS versions need an explicit show() after a policy change,
    // even when the app was already registered. Idempotent if already shown.
    try {
      const r = app.dock.show()
      if (r && typeof r.then === 'function') {
        r.then(() => console.log('[dock] shown')).catch((e) => console.warn('[dock] show error:', e.message))
      }
    } catch (e) { console.warn('[dock] show threw:', e.message) }
  } else {
    console.warn('[dock] app.dock not available — non-macOS platform?')
  }

  startWsServer()
  createOrb()
  createTray()
  // The Main Call dashboard is Chief's "I'm listening" indicator. Open it
  // at launch; the user closing it shuts the whole app down.
  openMainCall()

  // ⌥Space — force whisper (demo fallback)
  globalShortcut.register('Alt+Space', () => {
    const sent = sendToPython({ type: 'inject_canned' })
    console.log(`[fallback] inject_canned sent=${sent}`)
  })

  // ⌥⇧P — pause / resume
  globalShortcut.register('Alt+Shift+P', togglePause)

  // ⌘⇧K — open settings
  globalShortcut.register('Command+Shift+K', openSettings)

  // ⌘⇧L — open Side Call dashboard
  globalShortcut.register('Command+Shift+L', openLiveCall)

  // ⌘⇧M — open Main Call dashboard
  globalShortcut.register('Command+Shift+M', openMainCall)

  // ⌘⇧E — end the current conversation now; fires Phase 6 hang-up flush.
  // For stage: replaces waiting for the 90s idle timer so the queued
  // action cards replay on cue.
  globalShortcut.register('Command+Shift+E', () => {
    const sent = sendToPython({ type: 'end_call' })
    console.log(`[stage] end_call sent=${sent}`)
  })

  // Legacy dev shortcuts
  globalShortcut.register('CommandOrControl+Shift+T', () => {
    sendToPython({ type: 'inject_canned' })
  })
  globalShortcut.register('CommandOrControl+Shift+O', () => {
    if (!orbWindow) return
    orbWindow.isVisible() ? orbWindow.hide() : orbWindow.show()
  })
})

app.on('window-all-closed', () => {
  // Don't quit on window close — tray keeps the app alive
})

// Dock-icon click: bring the Main Call window to the front (it should
// already be open, since closing it quits the app). If it isn't, recreate.
app.on('activate', () => {
  if (!mainCallWindow || mainCallWindow.isDestroyed()) {
    openMainCall()
  } else {
    mainCallWindow.show()
    mainCallWindow.focus()
  }
})

app.on('before-quit', () => {
  // Any path that initiates a normal quit (Cmd+Q, tray "Quit") sets
  // this flag before windows close, so the Main Call window's `closed`
  // handler doesn't fire a second app.quit().
  isQuitting = true
})

app.on('will-quit', () => {
  if (pythonProc) { try { pythonProc.kill() } catch {} }
  if (wss) wss.close()
  globalShortcut.unregisterAll()
})
