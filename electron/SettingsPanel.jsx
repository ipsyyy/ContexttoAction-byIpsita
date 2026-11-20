/* eslint-disable */
const { useState: useSt, useEffect: useEf, useRef: useRf } = React

const KEY_FIELDS = [
  { key: 'ANTHROPIC_API_KEY', label: 'Anthropic' },
  { key: 'ELEVENLABS_API_KEY', label: 'ElevenLabs' },
  { key: 'MOSS_PROJECT_ID', label: 'Moss project ID' },
  { key: 'MOSS_PROJECT_KEY', label: 'Moss project key' },
  { key: 'SUPERMEMORY_API_KEY', label: 'Supermemory' },
  { key: 'TAVILY_API_KEY', label: 'Tavily' },
  { key: 'AGENTMAIL_API_KEY', label: 'AgentMail' },
  { key: 'AGENTMAIL_INBOX_ID', label: 'AgentMail inbox' },
  { key: 'CONTEXT_AGENT_TEST_RECIPIENT', label: 'Approved test recipient' },
  { key: 'LANGSMITH_API_KEY', label: 'LangSmith' },
]

const CALIBRATION_SECONDS = 10
const CAPTURE_SAMPLE_RATE = 16000

// Float32 PCM [-1,1] → base64 PCM16 LE.
function pcmFloat32ToBase64PCM16(samples) {
  const out = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  const bytes = new Uint8Array(out.buffer)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

async function recordCalibration(seconds, onProgress) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  })
  const ctx = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE })
  await ctx.audioWorklet.addModule('audio-worklet.js')
  const source = ctx.createMediaStreamSource(stream)
  const worklet = new AudioWorkletNode(ctx, 'vad-worklet')

  const chunks = []
  let total = 0
  const target = ctx.sampleRate * seconds
  let resolveDone
  const done = new Promise(r => { resolveDone = r })

  worklet.port.onmessage = (e) => {
    const pcm = e.data.pcm
    if (!pcm || total >= target) return
    chunks.push(pcm)
    total += pcm.length
    onProgress(Math.min(1, total / target))
    if (total >= target) resolveDone()
  }
  source.connect(worklet)

  await done
  source.disconnect()
  try { worklet.disconnect() } catch {}
  try { await ctx.close() } catch {}
  stream.getTracks().forEach(t => t.stop())

  const merged = new Float32Array(total)
  let off = 0
  for (const c of chunks) { merged.set(c, off); off += c.length }
  return {
    audioB64: pcmFloat32ToBase64PCM16(merged),
    sampleRate: CAPTURE_SAMPLE_RATE,
  }
}

function todayISO() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function KeyRow({ label, keyName, placeholder, config, onChange }) {
  const [editing, setEditing] = useSt(false)
  const [draft, setDraft] = useSt('')
  const current = config[keyName] || ''

  function save() {
    if (draft.trim()) onChange(keyName, draft.trim())
    setEditing(false)
    setDraft('')
  }

  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 0', borderBottom:'1px solid var(--hairline)' }}>
      <div style={{ flex:1 }}>
        <div style={{ font:'500 13px/1 var(--font-ui)', color:'var(--ink)' }}>{label}</div>
        <div style={{ font:'12px/1.4 var(--font-mono)', color: current ? 'var(--muted)' : '#c0392b', marginTop:3 }}>
          {current ? 'Configured' : 'Not set'}
        </div>
      </div>
      {editing ? (
        <div style={{ display:'flex', gap:6 }}>
          <input
            autoFocus
            type="password"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
            placeholder={placeholder || 'Enter a new value'}
            style={{ font:'12px var(--font-mono)', padding:'5px 8px', border:'1px solid var(--hairline-strong)', borderRadius:6, width:220, background:'var(--canvas)', color:'var(--ink)', outline:'none' }}
          />
          <button className="cf-btn cf-btn-primary cf-btn-sm" onClick={save}>Save</button>
          <button className="cf-btn cf-btn-quiet cf-btn-sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      ) : (
        <button className="cf-btn cf-btn-ghost cf-btn-sm" onClick={() => { setDraft(''); setEditing(true) }}>
          {current ? 'Update' : 'Set'}
        </button>
      )}
    </div>
  )
}

function VoiceCalibrationRow({ status, onEnroll, onClear }) {
  const [recording, setRecording] = useSt(false)
  const [progress, setProgress] = useSt(0)
  const [error, setError] = useSt('')

  const enrolled = !!(status && status.enrolled)
  const enrolledAt = status?.enrolled_at || ''
  const lastLabel = status?.last_label || ''
  const lastSim = status?.last_similarity || 0

  async function record() {
    setError('')
    setProgress(0)
    setRecording(true)
    try {
      const { audioB64, sampleRate } = await recordCalibration(
        CALIBRATION_SECONDS,
        p => setProgress(p),
      )
      onEnroll(audioB64, sampleRate)
    } catch (e) {
      setError((e && e.message) || String(e))
    } finally {
      setRecording(false)
    }
  }

  let detail
  if (recording) {
    detail = `Recording… ${Math.round(progress * CALIBRATION_SECONDS)}/${CALIBRATION_SECONDS}s — keep speaking naturally.`
  } else if (error) {
    detail = `Error: ${error}`
  } else if (enrolled) {
    const when = enrolledAt ? enrolledAt.replace('T', ' ').slice(0, 16) : ''
    const tail = lastLabel ? ` · last utterance classified as "${lastLabel}" (sim ${lastSim})` : ''
    detail = `Calibrated${when ? ' on ' + when : ''}.${tail}`
  } else {
    detail = `Not calibrated — record ${CALIBRATION_SECONDS}s so the assistant can tell your voice from the counterparty's.`
  }

  return (
    <div style={{ padding:'10px 0', borderBottom:'1px solid var(--hairline)' }}>
      <div style={{ display:'flex', alignItems:'center', gap:10 }}>
        <div style={{ flex:1 }}>
          <div style={{ font:'500 13px/1 var(--font-ui)', color:'var(--ink)' }}>Voice calibration</div>
          <div style={{ font:'12px/1.4 var(--font-ui)', color: enrolled ? 'var(--muted)' : '#c0392b', marginTop:3 }}>
            {detail}
          </div>
        </div>
        <div style={{ display:'flex', gap:6 }}>
          <button className="cf-btn cf-btn-primary cf-btn-sm" onClick={record} disabled={recording}>
            {recording ? 'Recording…' : (enrolled ? 'Re-record' : 'Record')}
          </button>
          {enrolled && !recording && (
            <button className="cf-btn cf-btn-quiet cf-btn-sm" onClick={onClear}>Clear</button>
          )}
        </div>
      </div>
      {recording && (
        <div style={{ marginTop: 8, height: 4, background:'var(--hairline)', borderRadius: 2, overflow:'hidden' }}>
          <div style={{ width:`${Math.round(progress * 100)}%`, height:'100%', background:'var(--ink)', transition:'width 100ms linear' }} />
        </div>
      )}
    </div>
  )
}

function CounterpartyRow({ status, onSave }) {
  const [draft, setDraft] = useSt('')
  const [editing, setEditing] = useSt(false)

  const hint = status?.hint || ''
  const resolved = !!status?.resolved
  const hydrated = status?.hydrated_claims || 0
  const sessionActive = !!status?.session_active

  useEf(() => { setDraft(hint) }, [hint])

  function save() {
    window.chief.setCounterparty(draft.trim())
    setEditing(false)
  }

  let detail
  if (!hint) {
    detail = 'Not set — the app will run without per-counterparty memory priming.'
  } else if (!resolved) {
    detail = `Hint "${hint}" did not resolve to a known entity — no prior claims loaded.`
  } else {
    const session = sessionActive ? ' (session active)' : ''
    detail = `Resolved → ${status.entity_id}; ${hydrated} prior claim${hydrated === 1 ? '' : 's'} hydrated from Supermemory${session}.`
  }

  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 0', borderBottom:'1px solid var(--hairline)' }}>
      <div style={{ flex:1 }}>
        <div style={{ font:'500 13px/1 var(--font-ui)', color:'var(--ink)' }}>Who's on the call?</div>
        <div style={{ font:'12px/1.4 var(--font-ui)', color: resolved ? 'var(--muted)' : (hint ? '#c0392b' : 'var(--muted)'), marginTop:3 }}>
          {detail}
        </div>
      </div>
      {editing ? (
        <div style={{ display:'flex', gap:6 }}>
          <input
            autoFocus
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
            placeholder="e.g. Marcus Chen"
            style={{ font:'13px var(--font-ui)', padding:'5px 8px', border:'1px solid var(--hairline-strong)', borderRadius:6, width:220, background:'var(--canvas)', color:'var(--ink)', outline:'none' }}
          />
          <button className="cf-btn cf-btn-primary cf-btn-sm" onClick={save}>Save</button>
          <button className="cf-btn cf-btn-quiet cf-btn-sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      ) : (
        <button className="cf-btn cf-btn-ghost cf-btn-sm" onClick={() => setEditing(true)}>
          {hint ? 'Change' : 'Set'}
        </button>
      )}
    </div>
  )
}

function RetrievalPanel({ status }) {
  if (!status) return null
  const { query, results, ms, source, at } = status
  const color = ms === 0 ? 'var(--muted)'
              : ms < 200 ? '#27ae60'
              : ms < 800 ? '#f39c12'
              : '#c0392b'
  return (
    <div style={{ border:'1px solid var(--hairline)', borderRadius:6, padding:'10px 12px', background:'var(--canvas)' }}>
      <div style={{ display:'flex', alignItems:'baseline', gap:10, marginBottom:6 }}>
        <span style={{ font:'12px var(--font-mono)', color:'var(--ink)', flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {query ? `“${query}”` : 'No query yet — speak a final utterance.'}
        </span>
        <span style={{ font:'11px var(--font-mono)', color, whiteSpace:'nowrap' }}>{ms} ms</span>
        <span style={{ font:'11px var(--font-mono)', color:'var(--muted)', whiteSpace:'nowrap' }}>{source}</span>
      </div>
      {results && results.length > 0 ? (
        <ul style={{ margin:0, padding:'0 0 0 14px', font:'11px/1.5 var(--font-mono)', color:'var(--body)' }}>
          {results.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      ) : (
        <div style={{ font:'11px var(--font-mono)', color:'var(--muted)' }}>No documents returned.</div>
      )}
      {at && (
        <div style={{ font:'10px var(--font-mono)', color:'var(--muted)', marginTop:6 }}>at {at.replace('T', ' ')}</div>
      )}
    </div>
  )
}

function MemoryByEntity({ memories }) {
  // Group items by the chief:entity:<id> container tag. Falls back to "other"
  // for session summaries and anything without an entity tag.
  const groups = {}
  for (const m of memories) {
    const tags = m.container_tags || []
    const entityTag = tags.find(t => typeof t === 'string' && t.startsWith('chief:entity:'))
    const key = entityTag ? entityTag.replace('chief:entity:', '') : 'other'
    if (!groups[key]) groups[key] = []
    groups[key].push(m)
  }
  const orderedKeys = Object.keys(groups).sort((a, b) => {
    if (a === 'other') return 1
    if (b === 'other') return -1
    return a.localeCompare(b)
  })
  return (
    <div style={{ maxHeight: 280, overflowY: 'auto', border:'1px solid var(--hairline)', borderRadius:6, background:'var(--canvas)' }}>
      {orderedKeys.map(key => (
        <div key={key} style={{ padding:'8px 12px', borderBottom:'1px solid var(--hairline)' }}>
          <div style={{ font:'600 11px/1 var(--font-mono)', color:'var(--ink)', letterSpacing:'0.04em', marginBottom:6 }}>
            {key} <span style={{ color:'var(--muted)', fontWeight:400 }}>· {groups[key].length}</span>
          </div>
          {groups[key].map((m, i) => <MemoryItem key={m.id || m.custom_id || i} item={m} />)}
        </div>
      ))}
    </div>
  )
}

function NameRow({ value, onSave }) {
  const [draft, setDraft] = useSt('')
  const [editing, setEditing] = useSt(false)

  useEf(() => { setDraft(value || '') }, [value])

  function save() {
    onSave(draft.trim())
    setEditing(false)
  }

  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 0', borderBottom:'1px solid var(--hairline)' }}>
      <div style={{ flex:1 }}>
        <div style={{ font:'500 13px/1 var(--font-ui)', color:'var(--ink)' }}>Your name</div>
        <div style={{ font:'12px/1.4 var(--font-ui)', color: value ? 'var(--muted)' : '#c0392b', marginTop:3 }}>
          {value ? `Your assistant calls you “${value}”.` : 'Not set — Alerts will use a neutral greeting.'}
        </div>
      </div>
      {editing ? (
        <div style={{ display:'flex', gap:6 }}>
          <input
            autoFocus
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
            placeholder="e.g. Ben"
            style={{ font:'13px var(--font-ui)', padding:'5px 8px', border:'1px solid var(--hairline-strong)', borderRadius:6, width:220, background:'var(--canvas)', color:'var(--ink)', outline:'none' }}
          />
          <button className="cf-btn cf-btn-primary cf-btn-sm" onClick={save}>Save</button>
          <button className="cf-btn cf-btn-quiet cf-btn-sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      ) : (
        <button className="cf-btn cf-btn-ghost cf-btn-sm" onClick={() => setEditing(true)}>
          {value ? 'Edit' : 'Set'}
        </button>
      )}
    </div>
  )
}

function MemoryItem({ item }) {
  const tags = (item.container_tags || []).filter(t => t && t !== 'chief').map(t => t.replace(/^chief:/, ''))
  const md = item.metadata || {}
  const when = item.updated_at || item.created_at || md.as_of_date || ''
  let preview = item.content || ''
  // Try to render Claim JSON nicely.
  try {
    const obj = JSON.parse(preview)
    if (obj && typeof obj === 'object') {
      if (obj.entity_id || obj.attribute || obj.value !== undefined) {
        preview = `${obj.entity_id ?? '?'} · ${obj.attribute ?? '?'} = ${JSON.stringify(obj.value)}`
        if (obj.source) preview += `  (${obj.source})`
      } else {
        preview = JSON.stringify(obj, null, 0)
      }
    }
  } catch {}

  if (preview.length > 240) preview = preview.slice(0, 240) + '…'

  return (
    <div style={{ padding:'10px 0', borderBottom:'1px solid var(--hairline)' }}>
      <div style={{ font:'12px/1.4 var(--font-mono)', color:'var(--ink)', whiteSpace:'pre-wrap', wordBreak:'break-word' }}>
        {preview || <span style={{ color:'var(--muted)' }}>(empty)</span>}
      </div>
      <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:6 }}>
        {when && (
          <span style={{ font:'11px var(--font-mono)', color:'var(--muted)' }}>{String(when).slice(0, 19).replace('T', ' ')}</span>
        )}
        {tags.map(t => (
          <span key={t} style={{ font:'11px var(--font-mono)', color:'var(--muted)', background:'var(--surface-strong)', border:'1px solid var(--hairline)', borderRadius:4, padding:'1px 6px' }}>
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

const SettingsPanel = () => {
  const [config, setConfig] = useSt({})
  const [saved, setSaved] = useSt(null)
  const [memories, setMemories] = useSt(null) // null = not requested yet
  const [memError, setMemError] = useSt('')
  const [memLoading, setMemLoading] = useSt(false)
  const [memAvailable, setMemAvailable] = useSt(true)
  const [today, setToday] = useSt(todayISO())
  const [voiceStatus, setVoiceStatus] = useSt(null)
  const [counterpartyStatus, setCounterpartyStatus] = useSt(null)
  const [retrievalStatus, setRetrievalStatus] = useSt(null)
  const listenerBound = useRf(false)
  const voiceListenerBound = useRf(false)
  const counterpartyListenerBound = useRf(false)
  const retrievalListenerBound = useRf(false)

  useEf(() => {
    window.chief.getConfig().then(setConfig)
  }, [])

  useEf(() => {
    if (listenerBound.current) return
    listenerBound.current = true
    window.chief.onMemoriesList(payload => {
      setMemLoading(false)
      setMemAvailable(!!payload.available)
      setMemError(payload.error || '')
      setMemories(payload.items || [])
    })
  }, [])

  useEf(() => {
    // Keep "today" fresh if the panel stays open across midnight.
    const id = setInterval(() => setToday(todayISO()), 60_000)
    return () => clearInterval(id)
  }, [])

  function refreshMemories() {
    setMemLoading(true)
    setMemError('')
    window.chief.listMemories(50)
  }

  // Auto-load memories on first mount.
  useEf(() => { refreshMemories() }, [])

  useEf(() => {
    if (voiceListenerBound.current) return
    voiceListenerBound.current = true
    window.chief.onVoiceStatus(s => setVoiceStatus(s))
    window.chief.requestVoiceStatus()
  }, [])

  // Poll voice status every 3s so the "last classified as" line stays fresh
  // while a conversation is active.
  useEf(() => {
    const id = setInterval(() => window.chief.requestVoiceStatus(), 3000)
    return () => clearInterval(id)
  }, [])

  function enrollVoice(audioB64, sampleRate) {
    window.chief.enrollVoice(audioB64, sampleRate)
  }
  function clearVoice() {
    window.chief.clearVoice()
  }

  useEf(() => {
    if (counterpartyListenerBound.current) return
    counterpartyListenerBound.current = true
    window.chief.onCounterpartyStatus(s => setCounterpartyStatus(s))
    window.chief.requestCounterpartyStatus()
  }, [])

  useEf(() => {
    if (retrievalListenerBound.current) return
    retrievalListenerBound.current = true
    window.chief.onRetrievalStatus(s => setRetrievalStatus(s))
    window.chief.requestRetrievalStatus()
  }, [])

  // Poll retrieval status every 2s so the latency panel stays fresh during a
  // live conversation. Cheap — just one ws round-trip.
  useEf(() => {
    const id = setInterval(() => window.chief.requestRetrievalStatus(), 2000)
    return () => clearInterval(id)
  }, [])

  function setCounterparty(hint) {
    window.chief.setCounterparty(hint)
  }

  async function handleChange(key, value) {
    if (!await window.chief.setConfigKey(key, value)) return
    setConfig(prev => ({ ...prev, [key]: KEY_FIELDS.some(field => field.key === key) ? Boolean(value) : value }))
    setSaved(key)
    setTimeout(() => setSaved(null), 2000)
  }

  const hotkeys = [
    ['⌥Space', 'Try a sample alert'],
    ['⌥⇧P',    'Pause / Resume'],
    ['⌘⇧K',    'Open Settings'],
    ['Esc',     'Dismiss card'],
  ]

  const sectionLabel = {
    font: '600 11px/1 var(--font-ui)',
    color: 'var(--muted)',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
    marginBottom: 12,
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', background:'var(--surface-card)', fontFamily:'var(--font-ui)' }}>
      {/* Title bar */}
      <div style={{
        height: 44,
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        borderBottom: '1px solid var(--hairline)',
        WebkitAppRegion: 'drag',
        flexShrink: 0,
      }}>
        <div style={{ flex:1, font:'600 13px/1 var(--font-ui)', color:'var(--ink)', letterSpacing:'-0.01em' }}>
          Context to Action by Ipsita · Settings
        </div>
        <div style={{ font:'12px var(--font-mono)', color:'var(--muted)', marginRight: 12 }}>
          Today: {today}
        </div>
        <button
          onClick={() => window.chief.openMainCall()}
          style={{ WebkitAppRegion:'no-drag', height:22, padding:'0 10px', marginRight:8,
              borderRadius:11, border:'1px solid var(--hairline-strong)',
              background:'transparent', color:'var(--ink)',
              cursor:'pointer', font:'500 11px var(--font-ui)', letterSpacing:'0.02em',
              display:'flex', alignItems:'center', gap:5 }}
          title="Open Main Call dashboard (⌘⇧M)"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
          </svg>
          Call
        </button>
        <button
          onClick={() => window.chief.closeSettings()}
          style={{
            WebkitAppRegion: 'no-drag',
            width: 22, height: 22,
            borderRadius: '50%',
            border: 'none',
            background: 'var(--hairline-strong)',
            color: 'var(--muted)',
            cursor: 'pointer',
            fontSize: 15,
            lineHeight: 1,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 0,
          }}
        >×</button>
      </div>

      {/* Body */}
      <div style={{ flex:1, overflowY:'auto', padding:'24px' }}>

        {/* You */}
        <div style={{ marginBottom: 28 }}>
          <div style={sectionLabel}>You</div>
          <NameRow
            value={config.USER_NAME || ''}
            onSave={(name) => handleChange('USER_NAME', name)}
          />
          <CounterpartyRow
            status={counterpartyStatus}
            onSave={setCounterparty}
          />
          <VoiceCalibrationRow
            status={voiceStatus}
            onEnroll={enrollVoice}
            onClear={clearVoice}
          />
        </div>

        {/* Live retrieval */}
        <div style={{ marginBottom: 28 }}>
          <div style={sectionLabel}>Live retrieval (Moss)</div>
          <RetrievalPanel status={retrievalStatus} />
        </div>

        {/* Memory */}
        <div style={{ marginBottom: 28 }}>
          <div style={{ display:'flex', alignItems:'center', marginBottom: 12 }}>
            <div style={{ ...sectionLabel, marginBottom: 0, flex: 1 }}>Memory (Supermemory)</div>
            <button className="cf-btn cf-btn-ghost cf-btn-sm" onClick={refreshMemories} disabled={memLoading}>
              {memLoading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
          {!memAvailable && (
            <div style={{ font:'12px var(--font-ui)', color:'#c0392b', marginBottom:8 }}>
              Supermemory unavailable {memError ? `— ${memError}` : ''}
            </div>
          )}
          {memAvailable && memories && memories.length === 0 && !memLoading && (
            <div style={{ font:'12px var(--font-ui)', color:'var(--muted)' }}>
              No durable memories stored yet. They appear here after conversations consolidate.
            </div>
          )}
          {memories && memories.length > 0 && (
            <MemoryByEntity memories={memories} />
          )}
        </div>

        {/* API keys */}
        <div style={{ marginBottom: 28 }}>
          <div style={sectionLabel}>API Keys</div>
          {KEY_FIELDS.map(f => (
            <KeyRow key={f.key} keyName={f.key} label={f.label} placeholder={f.placeholder}
              config={config} onChange={handleChange} />
          ))}
          {saved && (
            <div style={{ marginTop:10, font:'12px var(--font-ui)', color:'#27ae60' }}>
              ✓ Saved — restart the app for changes to take effect
            </div>
          )}
        </div>

        {/* Hotkeys */}
        <div>
          <div style={sectionLabel}>Hotkeys</div>
          <div style={{ display:'flex', flexDirection:'column', gap:2 }}>
            {hotkeys.map(([hk, desc]) => (
              <div key={hk} style={{ display:'flex', alignItems:'center', gap:12, padding:'8px 0', borderBottom:'1px solid var(--hairline)' }}>
                <kbd style={{ font:'12px var(--font-mono)', background:'var(--surface-strong)', border:'1px solid var(--hairline-strong)', borderRadius:5, padding:'3px 8px', color:'var(--ink)', minWidth:64, textAlign:'center', flexShrink:0 }}>
                  {hk}
                </kbd>
                <span style={{ font:'13px var(--font-ui)', color:'var(--body)' }}>{desc}</span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  )
}
