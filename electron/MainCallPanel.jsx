/* eslint-disable */
const { useState: useS, useEffect: useE, useRef: useR } = React

function fmtElapsed(startISO, endISO) {
  if (!startISO) return '—'
  const start = new Date(startISO).getTime()
  if (Number.isNaN(start)) return '—'
  const end = endISO ? new Date(endISO).getTime() : Date.now()
  const total = Math.max(0, Math.floor((end - start) / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

const STATE_CFG = {
  active: { ring: '#27ae60',                label: 'Listening',  hue: '#27ae60' },
  paused: { ring: '#f39c12',                label: 'Paused',     hue: '#f39c12' },
  idle:   { ring: 'var(--hairline-strong)', label: 'Idle',       hue: 'var(--muted)' },
  ended:  { ring: 'var(--hairline-strong)', label: 'Idle',       hue: 'var(--muted)' },
}

function Avatar({ name, state }) {
  const initial = (name || '?').trim().charAt(0).toUpperCase() || '?'
  const cfg = STATE_CFG[state] || STATE_CFG.idle
  return (
    <div style={{ position:'relative', width: 88, height: 88, borderRadius:'50%',
        background: 'var(--canvas)', border: `2px solid ${cfg.ring}`,
        display:'flex', alignItems:'center', justifyContent:'center',
        boxShadow: state === 'active' ? `0 0 0 6px ${cfg.ring}22` : 'none',
        transition: 'box-shadow 240ms, border-color 240ms' }}>
      <span style={{ font:'600 32px/1 var(--font-ui)', color:'var(--ink)' }}>{initial}</span>
      {state === 'active' && (
        <span style={{ position:'absolute', inset:-6, borderRadius:'50%',
            border: `2px solid ${cfg.ring}`, opacity: 0.35,
            animation: 'cf-main-pulse 1.6s ease-out infinite' }} />
      )}
      <style>{`@keyframes cf-main-pulse { 0%{transform:scale(1);opacity:.5} 100%{transform:scale(1.22);opacity:0} }`}</style>
    </div>
  )
}

function StatusPill({ state }) {
  const cfg = STATE_CFG[state] || STATE_CFG.idle
  return (
    <div style={{ display:'inline-flex', alignItems:'center', gap:7,
        padding:'4px 10px', borderRadius:999, border:'1px solid var(--hairline)',
        background:'var(--canvas)' }}>
      <span style={{ width:6, height:6, borderRadius:'50%', background:cfg.hue }} />
      <span style={{ font:'500 12px var(--font-ui)', color:'var(--ink)' }}>{cfg.label}</span>
    </div>
  )
}

function TranscriptLine({ entry }) {
  const isUser = entry.speaker === 'user'
  const align = isUser ? 'flex-end' : 'flex-start'
  const bg = isUser ? '#eef3fd' : 'var(--surface-strong)'
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems: align, marginBottom: 8 }}>
      <span style={{ font:'10px var(--font-mono)', color:'var(--muted)', marginBottom:2 }}>
        {entry.speaker === 'user' ? 'you' : entry.speaker} · {(entry.at || '').slice(11, 19)}
      </span>
      <div style={{
        font:'13px/1.4 var(--font-ui)', color:'var(--ink)',
        background: bg, padding:'7px 11px', borderRadius:10, maxWidth:'82%',
        wordBreak:'break-word',
      }}>{entry.text}</div>
    </div>
  )
}

function WhisperLine({ entry }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', marginBottom: 10 }}>
      <span style={{ font:'10px var(--font-mono)', color:'#d99366', marginBottom:2, letterSpacing:'0.04em', textTransform:'uppercase' }}>
        Alert · {(entry.at || '').slice(11, 19)}
      </span>
      <div style={{
        font:'500 13px/1.4 var(--font-ui)', color:'#7c4015',
        background:'#fbeee2', padding:'7px 12px', borderRadius:10,
        border:'1px solid #f3d4b5', maxWidth:'88%', textAlign:'center',
        wordBreak:'break-word',
      }}>{entry.text}</div>
      {entry.source && <small>{entry.source}</small>}
      {entry.id && <ContextFeedback kind="alert" reference={entry.id} sources={[entry]} />}
    </div>
  )
}

// Interleave utterances and whispers by timestamp.
function interleave(transcript, whispers) {
  const items = []
  for (const t of transcript) items.push({ kind: 'utterance', at: t.at, data: t })
  for (const w of whispers)   items.push({ kind: 'whisper',   at: w.at, data: w })
  items.sort((a, b) => (a.at || '').localeCompare(b.at || ''))
  return items
}

function TranscriptLibrary({ records, refreshRecords, selectedSource, showInsights }) {
  const [sources, setSources] = useS([])
  const [preview, setPreview] = useS(null)
  const [notice, setNotice] = useS('')
  const [busy, setBusy] = useS(false)

  async function refresh() {
    const reply = await window.chief.transcripts('list')
    if (reply.ok) setSources(reply.result)
    else setNotice(reply.error)
  }

  useE(() => {
    void refresh()
    if (selectedSource) void run('preview', { id: selectedSource })
  }, [selectedSource])
  useE(() => {
    if (preview?.analysis?.state !== 'pending') return
    const timer = setInterval(async () => {
      const reply = await window.chief.transcripts('preview', preview.id)
      if (reply.ok && reply.result) {
        setPreview(current => current?.id === preview.id ? reply.result : current)
      }
    }, 2500)
    return () => clearInterval(timer)
  }, [preview?.id, preview?.analysis?.state])

  async function run(operation, source) {
    setBusy(true)
    setNotice('')
    try {
      const reply = await window.chief.transcripts(operation, source?.id)
      if (!reply.ok) setNotice(reply.error)
      else if (operation === 'import' && !reply.cancelled) {
        setNotice(reply.result.duplicate ? 'This transcript is already saved.' : 'Transcript imported. Review analysis in the file preview.')
        await Promise.all([refresh(), refreshRecords()])
      } else if (operation === 'preview') {
        setPreview(reply.result)
        if (!reply.result) setNotice('This transcript is unavailable.')
      } else if (operation === 'retry_analysis') {
        const updated = await window.chief.transcripts('preview', source.id)
        if (updated.ok) setPreview(updated.result)
      } else if (operation === 'delete') {
        if (preview?.id === source.id) setPreview(null)
        setNotice('Transcript removed.')
        await Promise.all([refresh(), refreshRecords()])
      }
    } catch {
      setNotice('The transcript library could not complete that request.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="workspace-page">
      <header className="workspace-heading"><div><h1>Transcripts</h1><p>Import notes or review a saved session.</p></div></header>
      <button className="cf-btn cf-btn-primary cf-btn-sm" disabled={busy} onClick={() => run('import')}>
        Import transcript
      </button>
      <button className="cf-btn cf-btn-quiet cf-btn-sm" disabled={busy} onClick={() => void Promise.all([refresh(), refreshRecords()])}>
        Refresh
      </button>
      <p>TXT or Markdown, up to 1 MB. Original text stays on this Mac. Claude analyzes imported text. Selected insights can be saved to configured memory providers. Tavily checks selected public claims.</p>
      {notice && <p role="status">{notice}</p>}
      {sources.length ? <ul>{sources.map(source => (
        <li key={source.id}>
          <button className="cf-btn cf-btn-quiet cf-btn-sm" disabled={busy} onClick={() => run('preview', source)}>
            {source.filename}
          </button>
          <button className="cf-btn cf-btn-quiet cf-btn-sm" disabled={busy} onClick={() => {
            if (window.confirm('Delete this transcript and its searchable excerpts?')) run('delete', source)
          }}>Delete</button>
          {(() => {
            const summary = records.sources.find(item => item.id === source.id)
            if (!summary) return null
            return <p className="workspace-muted" aria-live="polite">{summary.state === 'complete'
              ? `Analysis complete · ${recordCount(summary.count, 'insight')} · ${recordCount(summary.drafts, 'draft')}`
              : summary.state === 'pending' ? 'Analyzing…' : `Analysis ${summary.state.replace('_', ' ')}`}</p>
          })()}
        </li>
      ))}</ul> : <p>No saved transcripts.</p>}
      {preview && <>
        <h3>{preview.filename}</h3>
        <p role="status">Analysis {preview.analysis?.state?.replace('_', ' ')}</p>
        {['failed', 'unavailable', 'not_started'].includes(preview.analysis?.state) &&
          <button onClick={() => run('retry_analysis', preview)} disabled={busy}>Analyze transcript</button>}
        {preview.analysis?.findings?.length > 0 && <button className="workspace-button" onClick={showInsights}>View saved insights</button>}
        <details open={Boolean(selectedSource)}><summary>Original text</summary><pre style={{ whiteSpace:'pre-wrap', overflowWrap:'anywhere' }}>{preview.text}</pre></details>
      </>}
    </section>
  )
}

function ContextFeedback({ kind, reference, sources }) {
  const [rating, setRating] = useS('helpful')
  const [correction, setCorrection] = useS('')
  const [preference, setPreference] = useS('')
  const [notice, setNotice] = useS('')

  async function save(event) {
    event.preventDefault()
    try {
      const reply = await window.chief.saveContextFeedback({
        kind, reference, rating, correction, preference: preference || null,
        sourceIds: [...new Set(sources.map(source => source.source_id).filter(Boolean))],
      })
      setNotice(reply.ok ? 'Feedback saved.' : reply.error)
    } catch {
      setNotice('Feedback could not be saved.')
    }
  }

  return <details>
    <summary>Give feedback</summary>
    <form onSubmit={save}>
      <label>Rating <select value={rating} onChange={e => setRating(e.target.value)}>
        <option value="helpful">Helpful</option>
        <option value="unhelpful">Unhelpful</option>
        <option value="incorrect">Incorrect fact</option>
        <option value="unnecessary">Unnecessary interruption</option>
        <option value="wrong_action">Wrong action</option>
        <option value="wrong_memory">Wrong memory</option>
        <option value="wrong_attribution">Incorrect speaker attribution</option>
      </select></label>
      <label>Correction <input value={correction} maxLength={1000} onChange={e => setCorrection(e.target.value)} /></label>
      <label>Apply a reviewed alert preference <select value={preference} onChange={e => setPreference(e.target.value)}>
        <option value="">Keep current preference</option>
        <option value="material_only">Only alert for material contradictions</option>
        <option value="standard">Use standard alert policy</option>
      </select></label>
      <button className="cf-btn cf-btn-quiet cf-btn-sm">Save feedback</button>
      {notice && <p role="status">{notice}</p>}
    </form>
  </details>
}

const MainCallPanel = () => {
  const [status, setStatus] = useS({
    state: 'idle', hint: '', entity_id: '', resolved: false,
    hydrated_claims: 0, conversation_id: '',
    started_at: '', ended_at: '',
    transcript: [], whispers: [],
  })
  const [elapsed, setElapsed] = useS('—')
  const [view, setView] = useS('home')
  const bound = useR(false)
  const scrollRef = useR(null)

  useE(() => {
    if (bound.current) return
    bound.current = true
    window.chief.onMainCallStatus(s => setStatus(prev => ({ ...prev, ...s })))
    window.chief.requestMainCallStatus()
  }, [])

  useE(() => {
    const id = setInterval(() => {
      if (status.state === 'active') setElapsed(fmtElapsed(status.started_at))
      else if (status.state === 'ended') setElapsed(fmtElapsed(status.started_at, status.ended_at))
      else setElapsed('—')
    }, 500)
    return () => clearInterval(id)
  }, [status.state, status.started_at, status.ended_at])

  useE(() => {
    if (!scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [status.transcript.length, status.whispers.length])

  const [captureRequested, setCaptureRequested] = useS(false)
  useE(() => {
    const update = state => setCaptureRequested(state === 'listening')
    window.chief.onChiefState(update)
    window.chief.getAppState().then(({ state }) => update(state))
  }, [])

  const [records, setRecords] = useS({ insights: [], actions: [], sources: [], controlled_recipient: '' })
  const [recordError, setRecordError] = useS('')
  const [recordsLoaded, setRecordsLoaded] = useS(false)
  const [selectedSource, setSelectedSource] = useS(null)
  const [selectedAction, setSelectedAction] = useS(null)
  const [seen, setSeen] = useS(() => {
    try { return JSON.parse(localStorage.getItem('seen-insights-v1') || '[]') } catch { return [] }
  })
  const incoming = records.insights.filter(item => ['decision','commitment','contradiction','connection'].includes(item.kind) || (item.kind === 'claim' && (item.review_later || item.public_check)))
  const unread = incoming.filter(item => !seen.includes(item.id)).length
  const hasUnread = unread > 0

  async function refreshRecords() {
    try {
      setRecords(await workspaceRequest('workspace'))
      setRecordError('')
      setRecordsLoaded(true)
    } catch (error) {
      setRecordError(error.message)
    }
  }

  useE(() => {
    void refreshRecords()
    const timer = setInterval(refreshRecords, 3500)
    return () => clearInterval(timer)
  }, [])
  useE(() => {
    if (view !== 'insights' || !hasUnread) return
    const ids = [...new Set([...seen, ...incoming.map(item => item.id)])].slice(-3000)
    localStorage.setItem('seen-insights-v1', JSON.stringify(ids))
    setSeen(ids)
  }, [records, view, hasUnread])

  function openTranscript(id) {
    setSelectedSource(id)
    setView('transcripts')
  }

  function openAction(id) {
    setSelectedAction(id)
    setView('actions')
  }

  const items = interleave(status.transcript, status.whispers)
  const cfg = STATE_CFG[status.state] || STATE_CFG.idle

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', background:'var(--surface-card)', fontFamily:'var(--font-ui)' }}>

      <header className="product-header">
        <span className="product-mark" aria-hidden="true">↗</span>
        <strong>Context to Action by Ipsita</strong>
        <span className="product-mic" data-active={captureRequested}>{captureRequested ? 'Listening requested' : 'Microphone off'}</span>
        <button className="product-close" aria-label="Close workspace" onClick={() => window.chief.closeMainCall()}>×</button>
      </header>
      <nav aria-label="Workspace views" className="workspace-nav">
        {['Home', 'Session', 'Transcripts', 'Chat', 'Insights', 'Actions', 'Settings'].map(tab => {
          const destination = tab.toLowerCase()
          return <button key={tab} aria-pressed={view === destination} onClick={() => {
            if (destination === 'settings') { window.chief.openSettings(); return }
            if (destination === 'transcripts') setSelectedSource(null)
            setView(destination)
          }}>
            {tab}{destination === 'insights' && hasUnread && <span className="workspace-dot" aria-label="New findings" />}
          </button>
        })}
      </nav>
      {recordError && ['home', 'insights', 'actions'].includes(view) && <p role="status" className="workspace-error">{recordError}</p>}
      {view === 'home' ? <HomePanel records={records} loaded={recordsLoaded} unread={unread} navigate={destination => { if (destination === 'transcripts') setSelectedSource(null); setView(destination) }} openTranscript={openTranscript} />
        : view === 'transcripts' ? <TranscriptLibrary records={records} refreshRecords={refreshRecords} selectedSource={selectedSource} showInsights={() => setView('insights')} />
        : view === 'chat' ? <GroundedChat />
        : view === 'insights' ? <InsightsPanel records={records} refresh={refreshRecords} openTranscript={openTranscript} openAction={openAction} />
        : view === 'actions' ? <ActionsPanel records={records} refresh={refreshRecords} selected={selectedAction} select={setSelectedAction} /> : <>
      {/* Header — avatar, who, status, timer */}
      <div style={{ padding:'18px 24px 0', display:'flex', flexDirection:'column', alignItems:'center', gap:6 }}>
        <Avatar name={status.hint || status.entity_id} state={status.state} />
        <div style={{ font:'600 18px/1.2 var(--font-ui)', color:'var(--ink)', textAlign:'center', marginTop:4 }}>
          {status.hint || (status.state === 'idle' ? 'No active call' : 'Unknown counterparty')}
        </div>
        {status.entity_id && (
          <div style={{ font:'12px/1.2 var(--font-mono)', color:'var(--muted)' }}>
            {status.entity_id}
            {status.hydrated_claims > 0 && (
              <span style={{ marginLeft:8, background:'var(--surface-strong)',
                  border:'1px solid var(--hairline)', borderRadius:4,
                  padding:'1px 6px', font:'11px var(--font-mono)', color:'var(--ink)' }}>
                {status.hydrated_claims} prior claim{status.hydrated_claims === 1 ? '' : 's'}
              </span>
            )}
          </div>
        )}
        <StatusPill state={status.state} />
        <div style={{ font: '12px var(--font-ui)', color: 'var(--muted)' }}>
          {status.hydrated_claims > 0 ? 'Saved session claims loaded' : 'No saved session claims loaded'}
        </div>
        <button className="cf-btn cf-btn-primary cf-btn-sm" onClick={() => window.chief.toggleListening()}>
          {captureRequested ? 'Stop listening' : 'Start listening'}
        </button>
        <div style={{ font: '12px var(--font-ui)', color: 'var(--muted)' }}>
          {captureRequested ? 'See the orb for microphone status. Completed sessions are saved to Transcripts.' : 'Microphone off. Start only with everyone’s consent. Configured speech and AI providers process live audio and text.'}
        </div>
        <div style={{ font:'500 24px/1 var(--font-mono)', color:'var(--ink)', letterSpacing:'0.05em' }}>
          {elapsed}
        </div>
      </div>

      {/* Transcript with whispers inline */}
      <div style={{ flex:1, margin:'14px 16px 0', padding:'12px 14px',
          overflow:'hidden', display:'flex', flexDirection:'column',
          border:'1px solid var(--hairline)', borderRadius:8, background:'var(--canvas)' }}>
        <div style={{ font:'600 11px/1 var(--font-ui)', color:'var(--muted)',
            letterSpacing:'0.08em', textTransform:'uppercase', marginBottom:8 }}>
          Transcript and alerts
        </div>
        <div ref={scrollRef} style={{ flex:1, overflowY:'auto', paddingRight:4 }}>
          {items.length === 0 ? (
            <div style={{ font:'12px var(--font-ui)', color:'var(--muted)', padding:'8px 0' }}>
              {status.state === 'active'
                ? 'Listening…'
                : 'Start listening during a workshop or meeting to see its transcript and alerts here.'}
            </div>
          ) : items.map((it, i) => (
            it.kind === 'whisper'
              ? <WhisperLine key={'w'+i} entry={it.data} />
              : <TranscriptLine key={'t'+i} entry={it.data} />
          ))}
        </div>
      </div>

      {/* Footer */}
      <div style={{ padding:'14px 18px 18px', display:'flex', justifyContent:'center', gap:10, flexShrink:0 }}>
        <button
          onClick={() => window.chief.resetMainCall()}
          className="cf-btn cf-btn-ghost"
          title="Clear the transcript without quitting."
        >Clear transcript</button>
      </div>

      </>}
    </div>
  )
}
