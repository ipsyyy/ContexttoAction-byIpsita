// Orb renderer — captures mic, runs simple energy-based VAD, ships completed
// utterances as base64-encoded PCM16 to the Python backend, where ElevenLabs
// Scribe handles transcription.

// ── UI elements ──────────────────────────────────────────────────────────────
const whisperCard  = document.getElementById('whisper-card')
const whisperEyebrow = document.getElementById('whisper-eyebrow')
const whisperPayload = document.getElementById('whisper-payload')
const whisperProvenance = document.getElementById('whisper-provenance')
const orbHalo      = document.getElementById('framer-orb-halo')
const actionCard   = document.getElementById('action-card')
const actionKind   = document.getElementById('action-kind-label')
const actionTitle  = document.getElementById('action-title')
const actionSummary = document.getElementById('action-summary')
const actionPreview = document.getElementById('action-preview')
const actionFoot   = document.getElementById('action-foot')
const statusWrap   = document.getElementById('status-wrap')
const statusLabel  = document.getElementById('status-label')
const ringContainer = document.getElementById('ring-container')
const orbCore      = document.getElementById('framer-orb-core')
const orbGlow      = document.getElementById('framer-orb-glow')

let collapseTimer = null
let isPaused = true
let micMuted = false
let statusDebounce = null

// ── Status ring ───────────────────────────────────────────────────────────────
const ORB_STATES = {
  idle:        '#a8a29e',
  listening:   '#4caf85',
  processing:  '#9d7bb5',
  whispering:  '#d99366',
}

function setRingState(state) {
  const color = ORB_STATES[state] || ORB_STATES.listening
  if (orbCore) orbCore.style.backgroundColor = color
  
  if (state === 'whispering' || state === 'processing') {
    ringContainer.classList.add('is-whispering')
  } else {
    ringContainer.classList.remove('is-whispering')
  }
}

function setStatus(text, immediate = false) {
  if (isPaused && text !== 'paused') return
  if (immediate) {
    if (statusDebounce) { clearTimeout(statusDebounce); statusDebounce = null }
    if (statusLabel) statusLabel.textContent = text
  } else {
    if (statusDebounce) clearTimeout(statusDebounce)
    statusDebounce = setTimeout(() => {
      if (statusLabel) statusLabel.textContent = text
    }, 300)
  }
}

// Threshold below which audio energy is too weak to display as a pulse.
// Anything below this releases the inline transform so the CSS breathing
// animation can take over — the orb gets its "vital sign" back.
const PULSE_FLOOR = 0.04

function pulse(intensity) {
  if (isPaused) return
  if (whisperCard.style.display !== 'none' || actionCard.style.display !== 'none') return
  const clamped = Math.max(0, Math.min(1, intensity))

  if (clamped < PULSE_FLOOR) {
    // Quiet room — release pulse override, let CSS breathe.
    if (orbCore) {
      orbCore.classList.remove('is-pulsing')
      orbCore.style.removeProperty('transform')
    }
    if (orbGlow) {
      orbGlow.style.removeProperty('opacity')
      orbGlow.style.removeProperty('transform')
    }
    return
  }

  const coreScale = 1.0 + (clamped * 0.3)
  const glowScale = 1.0 + (clamped * 0.55)

  if (orbCore) {
    orbCore.classList.add('is-pulsing')
    orbCore.style.transform = `scale(${coreScale})`
  }
  if (orbGlow) {
    orbGlow.style.opacity = 0.15 + (clamped * 0.15)
    orbGlow.style.transform = `translate(-50%, -50%) scale(${glowScale})`
  }
}

// ── Card lifecycle ────────────────────────────────────────────────────────────
function clearTimer() {
  if (collapseTimer) { clearTimeout(collapseTimer); collapseTimer = null }
}

function collapse() {
  clearTimer()
  whisperCard.style.display = 'none'
  actionCard.style.display  = 'none'
  statusWrap.classList.remove('hidden')
  setRingState(isPaused ? 'idle' : 'listening')
  window.chief.orbCollapsed()
}

function showCard(el) {
  statusWrap.classList.add('hidden')
  el.style.display = 'block'
  el.classList.remove('cf-card-entering')
  void el.offsetWidth  // reflow to restart animation
  el.classList.add('cf-card-entering')
  fitOrbToContent()
}

const stage = document.getElementById('stage')

function fitOrbToContent() {
  // Two rAFs: first lets the new card lay out, second reads the post-layout
  // box so wrapped text and animation transforms have settled.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!stage) return
      const rect = stage.getBoundingClientRect()
      // Stage is anchored right:4 / bottom:4; add that back plus a small
      // buffer so card shadow + glow don't get clipped.
      const PAD_X = 12
      const PAD_Y = 12
      const width = Math.ceil(rect.width) + PAD_X
      const height = Math.ceil(rect.height) + PAD_Y
      window.chief.fitOrb(width, height)
    })
  })
}

function flashHalo() {
  if (!orbHalo) return
  // Restart the one-shot animation by toggling the class off then on.
  orbHalo.classList.remove('flash')
  void orbHalo.offsetWidth  // force reflow
  orbHalo.classList.add('flash')
}

function renderWhisperText(text) {
  // Split into word tokens preserving whitespace; render each in a span
  // with a per-word animation-delay so the sentence types itself in.
  whisperPayload.innerHTML = ''
  const words = (text || '').split(/(\s+)/)  // keep separators
  let i = 0
  for (const w of words) {
    if (!w) continue
    if (/^\s+$/.test(w)) {
      whisperPayload.appendChild(document.createTextNode(w))
      continue
    }
    const span = document.createElement('span')
    span.className = 'w'
    span.textContent = w
    span.style.animationDelay = `${i * 28}ms`  // 28ms per word
    whisperPayload.appendChild(span)
    i++
  }
}

function renderProvenance(prov) {
  if (!whisperProvenance) return
  if (!prov || (!prov.source && !prov.date && !prov.entity)) {
    whisperProvenance.classList.add('hidden')
    whisperProvenance.textContent = ''
    return
  }
  // Compose `memory · <source> · <entity>` with hairline-thin separators.
  // Skip empty segments so we never render `memory · · `.
  const parts = ['memory']
  const src = (prov.source || '').trim()
  const date = (prov.date || '').trim()
  const ent = (prov.entity || '').trim()
  if (src && date) parts.push(`${src} · ${date}`)
  else if (src)    parts.push(src)
  else if (date)   parts.push(date)
  if (ent)         parts.push(ent)
  whisperProvenance.textContent = parts.join(' · ')
  whisperProvenance.classList.remove('hidden')
}

function expandWhisper(payload) {
  // Backwards-compatible: older paths pass a plain string.
  const text = (payload && typeof payload === 'object') ? payload.content : payload
  const provenance = (payload && typeof payload === 'object') ? payload.provenance : null

  clearTimer()
  whisperEyebrow.textContent = 'Context to Action by Ipsita'
  renderWhisperText(text)
  renderProvenance(provenance)
  actionCard.style.display = 'none'
  flashHalo()
  showCard(whisperCard)
  setRingState('whispering')
  collapseTimer = setTimeout(collapse, 6000)
}

const ICONS = {
  email: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>`,
  calendar: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
  document: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>`,
  search: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  phone: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,
}

function actionMeta(name) {
  if (name === 'draft_email')    return { icon: ICONS.email,    label: 'Email draft',    approveLabel: 'Send',   doneLabel: 'Sent' }
  if (name === 'send_email')     return { icon: ICONS.email,    label: 'Send email',     approveLabel: 'Send',   doneLabel: 'Sent' }
  if (name === 'check_calendar') return { icon: ICONS.calendar, label: 'Calendar check', approveLabel: 'Confirm', doneLabel: 'Done' }
  if (name === 'block_calendar') return { icon: ICONS.calendar, label: 'Calendar hold',  approveLabel: 'Confirm', doneLabel: 'Held' }
  if (name === 'pull_document')  return { icon: ICONS.document, label: 'Document',       approveLabel: 'Open',   doneLabel: 'Opened' }
  if (name === 'call_person')    return { icon: ICONS.phone,    label: 'Side call',      approveLabel: 'Call',   doneLabel: 'Dialing…' }
  if (name && name.startsWith('search_web')) return { icon: ICONS.search, label: 'Web search', approveLabel: 'Confirm', doneLabel: 'Done' }
  return { icon: ICONS.calendar, label: name || 'Action', approveLabel: 'Approve', doneLabel: 'Done' }
}

let currentApprovalId = null

function expandAction(event) {
  currentApprovalId = event.approval_id ?? null
  clearTimer()
  const meta = actionMeta(event.name)
  actionKind.innerHTML = `${meta.icon} ${meta.label}`
  actionTitle.textContent = formatActionTitle(event.name, event.inputs)
  actionSummary.textContent = formatAction(event.name, event.inputs)
  if (event.inputs.sources?.length) actionSummary.textContent += ' · Sources: ' + event.inputs.sources.join(', ')
  renderPreview(event.name, event.inputs)
  whisperCard.style.display = 'none'

  actionFoot.innerHTML = ''
  if (event.email_preview_only) {
    actionTitle.textContent = 'Local email preview'
    actionSummary.textContent += ' · AgentMail is unavailable'
    const dismiss = document.createElement('button')
    dismiss.className = 'cf-btn cf-btn-quiet cf-btn-sm'
    dismiss.textContent = 'Close'
    dismiss.onclick = collapse
    actionFoot.appendChild(dismiss)
    collapseTimer = setTimeout(collapse, 60000)
  } else if (event.requires_approval && currentApprovalId) {
    const approve = document.createElement('button')
    approve.className = 'cf-btn cf-btn-primary cf-btn-sm'
    approve.textContent = meta.approveLabel
    approve.onclick = () => {
      window.chief.approveAction(event.approval_id)
      clearTimer()
      actionTitle.textContent = 'Approval submitted'
      actionFoot.innerHTML = ''
      collapseTimer = setTimeout(collapse, 20000)
    }
    const dismiss = document.createElement('button')
    dismiss.className = 'cf-btn cf-btn-quiet cf-btn-sm'
    dismiss.textContent = 'Discard'
    dismiss.onclick = () => { window.chief.discardAction(event.approval_id); collapse() }
    actionFoot.appendChild(approve)
    actionFoot.appendChild(dismiss)
    // Email cards stick around longer than the default since the user
    // likely wants to read the body before approving.
    const dwell = (event.name === 'draft_email' || event.name === 'send_email') ? 60000 : 20000
    collapseTimer = setTimeout(collapse, dwell)
  } else {
    collapseTimer = setTimeout(collapse, 8000)
  }

  showCard(actionCard)
  setRingState('whispering')
}

function renderPreview(name, inputs) {
  if (!actionPreview) return
  // Email cards show the full draft body so the user can review before
  // pressing Send. Other tools have no body to surface — hide the slot.
  if (name === 'draft_email' || name === 'send_email') {
    const subject = (inputs.subject || '').trim()
    const body    = (inputs.body || '').trim()
    actionPreview.innerHTML = ''
    if (subject) {
      const subj = document.createElement('div')
      subj.style.cssText = 'font: 600 12px var(--font-ui); color: var(--ink); margin-bottom: 6px;'
      subj.textContent = subject
      actionPreview.appendChild(subj)
    }
    if (body) {
      const b = document.createElement('div')
      b.textContent = body
      actionPreview.appendChild(b)
    } else if (!subject) {
      actionPreview.textContent = '(no body)'
    }
    actionPreview.classList.remove('hidden')
  } else {
    actionPreview.classList.add('hidden')
    actionPreview.textContent = ''
  }
}

function formatActionTitle(name, inputs) {
  switch (name) {
    case 'draft_email':
    case 'send_email':    return `Draft ready — ${inputs.subject}`
    case 'check_calendar': return `Calendar check — ${inputs.date}`
    case 'block_calendar': return `Hold ${inputs.title}`
    case 'search_web':      return `Search — ${inputs.query}`
    case 'search_web_deep': return `Research — ${(inputs.query || inputs.task || '').slice(0, 50)}`
    case 'pull_document': return `Document — ${inputs.description}`
    case 'call_person': return `Call ${inputs.callee_name || inputs.to || 'contact'}`
    default: return name.replace(/_/g, ' ')
  }
}

function formatAction(name, inputs) {
  switch (name) {
    case 'draft_email':
    case 'send_email':    return `To ${inputs.to}`
    case 'check_calendar': return `Checking availability on ${inputs.date}`
    case 'block_calendar': return `${inputs.date} · ${inputs.duration_minutes}m`
    case 'search_web':      return `"${inputs.query}"`
    case 'search_web_deep': return `"${(inputs.query || inputs.task || '').slice(0, 60)}"`
    case 'pull_document': return `"${inputs.description}"`
    case 'call_person': return `${inputs.to} — ${inputs.purpose || '(no script)'}`
    default: return JSON.stringify(inputs)
  }
}

window.chief.onWhisper(expandWhisper)
window.chief.onActionCard(expandAction)
window.chief.onDismiss(collapse)
window.chief.onEmailOutcome(result => {
  if (currentApprovalId !== result.approval_id || actionCard.style.display === 'none') return
  clearTimer()
  actionTitle.textContent = result.message
  actionSummary.textContent = result.message_id ? 'Provider ID: ' + result.message_id : ''
  collapseTimer = setTimeout(collapse, 14000)
})
window.chief.onChiefState((state) => {
  isPaused = (state === 'paused')
  if (isPaused) {
    stopMic()
    collapse()
    setRingState('idle')
    setStatus('paused', true)
  } else {
    setRingState('listening')
    void init()
  }
})

window.chief.onMuteMic(() => { micMuted = true })
window.chief.onUnmuteMic(() => { micMuted = false })

// Drag — track mousemove on the status wrap (not cards) and move window via IPC
let dragOrigin = null
statusWrap.addEventListener('mousedown', (e) => {
  if (e.target === ringContainer || ringContainer.contains(e.target)) return
  dragOrigin = { x: e.screenX, y: e.screenY }
  e.preventDefault()
})
document.addEventListener('mousemove', (e) => {
  if (!dragOrigin) return
  const dx = e.screenX - dragOrigin.x
  const dy = e.screenY - dragOrigin.y
  dragOrigin = { x: e.screenX, y: e.screenY }
  window.chief.moveOrb(dx, dy)
})
document.addEventListener('mouseup', () => { dragOrigin = null })

// Click the status ring → open Settings
ringContainer.addEventListener('click', () => window.chief.openSettings())

// Esc key dismisses active card
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') collapse()
})

// =====================================================================
// Audio capture + VAD
// =====================================================================

const TARGET_RATE = 16000
const FRAME_MS = 100
const RMS_THRESHOLD = 0.025        // anything quieter than this is silence
const END_SILENCE_FRAMES = 7       // ~700ms trailing silence ends an utterance
const MIN_UTTERANCE_FRAMES = 6     // skip blips under ~600ms
const MAX_UTTERANCE_FRAMES = 150   // ~15 s safety cap

let speaking = false
let silenceCount = 0
let buffer = []
let sending = false

function onAudio(pcm, rms) {
  if (micMuted || isPaused) return
  pulse(Math.min(1, rms * 25))

  if (rms > RMS_THRESHOLD) {
    if (!speaking) {
      speaking = true
      buffer = []
    }
    silenceCount = 0
    buffer.push(...pcm)
  } else if (speaking) {
    silenceCount++
    buffer.push(...pcm)
    if (silenceCount >= END_SILENCE_FRAMES) {
      flushIfReady()
      speaking = false
      silenceCount = 0
      buffer = []
    }
  }

  if (speaking && buffer.length >= TARGET_RATE * (MAX_UTTERANCE_FRAMES * FRAME_MS / 1000)) {
    flushIfReady()
    speaking = false
    silenceCount = 0
    buffer = []
  }
}

function flushIfReady() {
  const minSamples = TARGET_RATE * (MIN_UTTERANCE_FRAMES * FRAME_MS / 1000)
  if (buffer.length < minSamples) return
  if (sending) {
    console.log('[stt] dropping utterance, previous send still in flight')
    return
  }
  shipUtterance(new Float32Array(buffer))
}

function f32ToI16(f32) {
  const out = new Int16Array(f32.length)
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]))
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7FFF)
  }
  return out
}

function bytesToB64(bytes) {
  let bin = ''
  // Chunk to avoid hitting argument-count limits on String.fromCharCode
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
  }
  return btoa(bin)
}

function shipUtterance(f32) {
  sending = true
  setStatus('transcribing…')
  try {
    const i16 = f32ToI16(f32)
    const b64 = bytesToB64(new Uint8Array(i16.buffer))
    console.log(`[stt] shipping ${(f32.length / TARGET_RATE).toFixed(2)}s of audio (${b64.length} b64 chars)`)
    window.chief.sendAudioUtterance(b64, TARGET_RATE)
  } catch (e) {
    console.error('[stt] ship error:', e)
  } finally {
    // Let the next utterance start while ElevenLabs transcribes; the dedupe
    // is light because VAD already gates on silence.
    setTimeout(() => {
      sending = false
      setStatus('listening')
    }, 50)
  }
}

async function startMic() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    })
    console.log('[mic] stream acquired')
    return stream
  } catch (e) {
    console.error('[mic] getUserMedia failed:', e?.name, e?.message || e)
    setStatus('mic denied')
    return null
  }
}

let activeStream
let activeContext
let captureGeneration = 0

function stopMic() {
  captureGeneration++
  speaking = false
  buffer = []
  activeStream?.getTracks().forEach(track => track.stop())
  activeStream = null
  if (activeContext) void activeContext.close().catch(console.error)
  activeContext = null
}

async function init() {
  if (activeStream) return
  const generation = ++captureGeneration
  setStatus('starting mic…')
  const stream = await startMic()
  if (!stream) return
  if (isPaused || generation !== captureGeneration) {
    stream.getTracks().forEach(track => track.stop())
    return
  }

  activeStream = stream
  try {
    const ctx = new AudioContext({ sampleRate: TARGET_RATE })
    activeContext = ctx
    await ctx.audioWorklet.addModule('audio-worklet.js')
    if (generation !== captureGeneration) return
    const source = ctx.createMediaStreamSource(stream)
    const worklet = new AudioWorkletNode(ctx, 'vad-worklet')
    worklet.port.onmessage = (e) => onAudio(e.data.pcm, e.data.rms)
    source.connect(worklet)
    console.log('[audio] worklet running at', ctx.sampleRate, 'Hz')
    setStatus('listening')
  } catch (error) {
    if (generation !== captureGeneration) return
    stopMic()
    console.error('[audio] worklet failed:', error)
    setStatus('mic error')
  }
}

setStatus('paused', true)
