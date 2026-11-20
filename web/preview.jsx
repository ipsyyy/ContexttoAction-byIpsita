const { useState: useS, useEffect: useE } = React
const sourceLimit = 1024 * 1024
const storageKey = 'context-to-action-web-preview-v1'

function readWorkspace() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]')
    return Array.isArray(saved) ? saved : []
  } catch {
    return []
  }
}

async function identifier(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
}

function validateTranscript(filename, text) {
  if (typeof filename !== 'string' || !/^[^/\\]+\.(txt|md)$/i.test(filename)) throw new Error('Choose a TXT or Markdown transcript.')
  if (typeof text !== 'string' || !text.trim() || text.includes('\0')) throw new Error('Choose a nonempty UTF-8 transcript.')
  if (new TextEncoder().encode(text).length > sourceLimit) throw new Error('Choose a transcript up to 1 MB.')
}

async function readExport(file) {
  if (file.size > 4 * sourceLimit) throw new Error('Choose a workspace export up to 4 MB.')
  const exported = JSON.parse(await file.text())
  if (exported?.format !== 'context-to-action-preview-v1' || !Array.isArray(exported.sources)) {
    throw new Error('Choose a Context to Action desktop export.')
  }
  for (const source of exported.sources) {
    validateTranscript(source.filename, source.text)
    if (await identifier(source.text) !== source.id || typeof source.imported_at !== 'string'
        || !Array.isArray(source.findings) || !Array.isArray(source.actions)) {
      throw new Error('The export has an invalid source or missing finding data.')
    }
    const characters = Array.from(source.text)
    for (const finding of source.findings) {
      if (typeof finding.quote !== 'string' || !Number.isInteger(finding.offset)
          || finding.offset < 0
          || characters.slice(finding.offset, finding.offset + Array.from(finding.quote).length).join('') !== finding.quote) {
        throw new Error('A saved finding does not match its source text.')
      }
    }
  }
  return exported.sources
}

function PreviewApp() {
  const [sources, setSources] = useS(readWorkspace)
  const [view, setView] = useS('home')
  const [selected, setSelected] = useS('')
  const [notice, setNotice] = useS('')
  const [busy, setBusy] = useS(false)
  const views = {home:'Home', transcripts:'Transcripts', insights:'Insights', actions:'Actions'}
  const records = {
    sources: sources.map(source => ({ ...source, count:source.findings.length, drafts:source.actions.length })),
    insights: sources.flatMap(source => source.findings.map(finding => ({ ...finding, filename:source.filename, source_id:source.id }))),
    actions: sources.flatMap(source => source.actions.map(action => ({ ...action, filename:source.filename }))),
  }
  const current = sources.find(source => source.id === selected)
  const openTranscript = id => { setSelected(id); setView('transcripts') }
  const openAction = id => { setSelected(id); setView('actions') }

  async function importFile(event, format) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    setNotice('')
    try {
      let incoming
      if (format === 'export') {
        incoming = await readExport(file)
      } else {
        if (file.size > sourceLimit) throw new Error('Choose a transcript up to 1 MB.')
        const text = new TextDecoder('utf-8', { fatal:true, ignoreBOM:true }).decode(await file.arrayBuffer())
        validateTranscript(file.name, text)
        incoming = [{ id:await identifier(text), filename:file.name, text, imported_at:new Date().toISOString(),
          state:'not_started', findings:[], actions:[] }]
      }
      const merged = new Map(sources.map(source => [source.id, source]))
      for (const source of incoming) {
        if (format === 'export' || !merged.has(source.id)) merged.set(source.id, source)
      }
      const next = [...merged.values()].sort((a,b) => b.imported_at.localeCompare(a.imported_at))
      localStorage.setItem(storageKey, JSON.stringify(next))
      setSources(next)
      setNotice(format === 'export' ? 'Saved insights and pending drafts imported.' : 'Transcript saved in this browser.')
      if (incoming.length) openTranscript(incoming[0].id)
    } catch (error) {
      setNotice(error.name === 'QuotaExceededError' ? 'This browser has no more storage for transcripts.' : error.message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <header className="product-header"><span className="product-mark" aria-hidden="true">C</span><strong>Context to Action by Ipsita</strong></header>
    <nav className="workspace-nav" aria-label="Workspace">{Object.entries(views).map(([id, name]) =>
      <button key={id} aria-pressed={id === view} onClick={() => setView(id)}>{name}</button>)}</nav>
    <p className="web-notice">Browser preview. Files stay in this browser. Import a desktop export to see analyzed findings and pending drafts.</p>
    {view === 'home' && <HomePanel browser records={records} loaded unread={0} navigate={setView} openTranscript={openTranscript} />}
    {view === 'transcripts' && <section className="workspace-page">
      <header className="workspace-heading"><h1>Transcripts</h1></header>
      <div className="web-fields">
        <label>Upload transcript <input type="file" accept=".txt,.md" disabled={busy} onChange={event => void importFile(event, 'text')} /></label>
        <label>Import desktop export <input type="file" accept=".json" disabled={busy} onChange={event => void importFile(event, 'export')} /></label>
      </div>
      {notice && <p role="status">{notice}</p>}
      {sources.length === 0 && <p>No saved transcripts.</p>}
      <div className="workspace-list">{sources.map(source =>
        <button className="workspace-card action-row" aria-pressed={selected === source.id} key={source.id}
          onClick={() => openTranscript(source.id)}><strong>{source.filename}</strong>
          <span>{source.state === 'complete' ? recordCount(source.findings.length, 'saved insight') : 'Original text'}</span>
        </button>)}</div>
      {current && <article className="workspace-card web-source">
        <h2>{current.filename}</h2>
        {current.findings.length > 0 && <button className="workspace-button" onClick={() => setView('insights')}>Browse saved insights</button>}
        <details open key={current.id}><summary>Original text</summary><pre>{current.text}</pre></details>
      </article>}
    </section>}
    {view === 'insights' && <InsightsPanel browseOnly records={records} openTranscript={openTranscript} openAction={openAction} />}
    {view === 'actions' && <section className="workspace-page">
      <header className="workspace-heading"><h1>Pending actions</h1></header>
      {records.actions.length === 0 && <p>No pending drafts.</p>}
      <div className="workspace-list">{records.actions.map(action =>
        <details className="workspace-card insight-card" key={action.id} open={action.id === selected}>
          <summary><span className="workspace-tag">Draft</span><strong>{action.subject}</strong><span>{action.filename}</span></summary>
          <div className="workspace-card-detail"><blockquote>{action.body}</blockquote>
            <button className="workspace-button" onClick={() => openTranscript(action.source_id)}>Open source transcript</button>
          </div>
        </details>)}</div>
    </section>}
  </>
}

ReactDOM.createRoot(document.getElementById('root')).render(<PreviewApp />)
