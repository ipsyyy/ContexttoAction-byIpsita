const { useState: useS, useEffect: useE } = React
const emptyWorkspace = { sources: [], insights: [], actions: [], controlled_recipient: '', services: {} }
const views = { home: 'Home', transcripts: 'Transcripts', chat: 'Chat', insights: 'Insights', actions: 'Actions' }

async function request(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  })
  if (!response.headers.get('Content-Type')?.startsWith('application/json')) {
    throw new Error('The web server did not return a workspace response.')
  }
  const reply = await response.json()
  if (!response.ok && reply.ok) throw new Error('The web request failed.')
  return reply
}

async function result(path, data) {
  const reply = await request(path, data)
  if (!reply.ok) throw new Error(reply.error || 'Workspace unavailable.')
  return reply.result
}

// Shared components use this interface on both Electron and HTTP.
window.chief = {
  workspaceRecords: (operation, fields) => request(operation, fields),
  askContext: (question, use_model) => request('chat', { question, use_model }),
  reviewEmailDraft: ({ subject, body, sourceIds }) => request('draft', { subject, body, source_id: sourceIds[0] }),
  openSourceLink: url => {
    if (/^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer')
  },
}

function WebApp() {
  const [view, setView] = useS('home')
  const [records, setRecords] = useS(emptyWorkspace)
  const [loaded, setLoaded] = useS(false)
  const [workspaceError, setWorkspaceError] = useS('')
  const [selection, setSelection] = useS(null)
  const [current, setCurrent] = useS(null)
  const [action, setAction] = useS('')
  const [busy, setBusy] = useS('')
  const [notice, setNotice] = useS('')
  const [fileError, setFileError] = useS('')

  async function refresh() {
    try {
      setRecords(await result('workspace'))
      setWorkspaceError('')
    } catch (error) {
      setWorkspaceError(error.message)
    } finally {
      setLoaded(true)
    }
  }

  useE(() => { void refresh() }, [])
  useE(() => {
    let active = true
    setCurrent(null)
    if (selection) result('transcripts/' + selection.id).then(source => {
      if (active) setCurrent(source)
    }).catch(error => {
      if (active) setFileError(error.message)
    })
    return () => { active = false }
  }, [selection])

  function navigate(destination) {
    setView(destination)
    if (destination === 'actions') void refresh()
  }

  function openTranscript(id, offset, length) {
    setFileError('')
    setSelection({ id, offset, length })
    setView('transcripts')
  }

  function openAction(id) {
    setAction(id)
    navigate('actions')
  }

  async function analyze(id) {
    setBusy('Analyzing with Claude…')
    const analysis = await result('analyze', { source_id: id })
    setNotice(`Analysis saved · ${recordCount(analysis.findings.length, 'insight')}`)
    setCurrent(value => value?.id === id ? { ...value, analysis } : value)
    setSelection(value => value?.id === id ? { ...value } : value)
  }

  async function importFile(event) {
    const file = event.target.files[0]
    event.target.value = ''
    if (!file) return
    setFileError('')
    setNotice('')
    setBusy('Saving transcript…')
    try {
      if (file.size > 1024 * 1024) throw new Error('Choose a transcript up to 1 MB.')
      let text
      try {
        text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
      } catch {
        throw new Error('Choose a UTF-8 text file.')
      }
      const source = await result('transcripts', { filename: file.name, text })
      openTranscript(source.id)
      setNotice(source.duplicate ? 'This transcript was already saved.' : 'Transcript saved.')
    } catch (error) {
      setFileError(error.message)
    } finally {
      setBusy('')
      await refresh()
    }
  }

  async function analyzeSelected() {
    setFileError('')
    setNotice('')
    try {
      await analyze(current.id)
    } catch (error) {
      setFileError(error.message)
    } finally {
      setBusy('')
      await refresh()
    }
  }

  return <>
    <header className="product-header">
      <span className="product-mark" aria-hidden="true">C</span>
      <strong>Context to Action by Ipsita</strong>
    </header>
    <nav className="workspace-nav" aria-label="Workspace">
      {Object.entries(views).map(([key, label]) => <button key={key} aria-current={view === key ? 'page' : undefined}
        aria-pressed={view === key} onClick={() => navigate(key)}>{label}</button>)}
    </nav>
    {workspaceError && <p className="web-notice" role="alert">{workspaceError}
      {' '}<button className="workspace-button" onClick={() => void refresh()}>Retry workspace</button></p>}
    {view === 'home' && <HomePanel records={records} loaded={loaded} navigate={navigate} openTranscript={openTranscript} browser />}
    {view === 'transcripts' && <section className="workspace-page">
      <header className="workspace-heading"><div><h1>Transcripts</h1><p>Upload conversation notes or a transcript, then analyze it to save decisions and follow-ups.</p></div></header>
      <div className="web-fields"><label>Upload transcript <input type="file" accept=".txt,.md" disabled={!!busy}
        onChange={importFile} /></label></div>
      <p className="workspace-muted">TXT or Markdown, up to 1 MB. Claude analysis supports up to 24,000 characters per transcript.</p>
      {(busy || notice) && <p role="status">{busy || notice}</p>}
      {fileError && <p role="alert">{fileError}</p>}
      {records.sources.length === 0 && loaded && <p className="workspace-empty">No transcripts yet.</p>}
      <div className="workspace-list">{records.sources.map(source =>
        <button className="workspace-card action-row" aria-pressed={selection?.id === source.id} key={source.id}
          onClick={() => openTranscript(source.id)}><strong>{source.filename}</strong>
          <span>{source.state === 'complete' ? recordCount(source.count, 'saved insight') : 'Original text'}</span>
        </button>)}</div>
      {current && <article className="workspace-card web-source">
        <h2>{current.filename}</h2>
        {current.analysis.state === 'complete' ?
          <button className="workspace-button" onClick={() => navigate('insights')}>Browse saved insights</button> :
          records.services.claude ?
            <><p>Analysis sends this transcript and relevant saved excerpts to Claude. Public claims may be checked with Tavily.</p>
              <button className="workspace-button primary" disabled={!!busy} onClick={analyzeSelected}>Analyze transcript</button></> :
            <p>Claude analysis is unavailable. Your original transcript is saved.</p>}
        <SourceText key={current.id} text={current.text} offset={selection.offset} length={selection.length} />
      </article>}
    </section>}
    <div hidden={view !== 'chat'} className="web-chat-container">
      <GroundedChat browser openTranscript={openTranscript} modelAvailable={!!records.services.claude} />
    </div>
    {view === 'insights' && <InsightsPanel browser records={records} refresh={refresh} openTranscript={openTranscript} openAction={openAction} />}
    {view === 'actions' && <ActionsPanel reviewOnly records={records} refresh={refresh} selected={action} select={setAction} openTranscript={openTranscript} />}
  </>
}

function SourceText({ text, offset, length }) {
  if (!Number.isInteger(offset) || !Number.isInteger(length)) return <details open><summary>Original text</summary><pre>{text}</pre></details>
  const characters = Array.from(text)
  const end = offset + length
  return <details open><summary>Original text</summary><pre>{characters.slice(0, offset).join('')}
    <mark>{characters.slice(offset, end).join('')}</mark>{characters.slice(end).join('')}</pre></details>
}

ReactDOM.createRoot(document.getElementById('root')).render(<WebApp />)
