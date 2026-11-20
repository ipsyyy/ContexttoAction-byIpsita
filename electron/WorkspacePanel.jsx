const insightNames = {
  decision: 'Decision', commitment: 'Commitment', contradiction: 'Contradiction',
  connection: 'Relevant memory', lesson: 'Lesson', claim: 'Claim',
}
const actionStates = {
  draft: 'Draft', awaiting_approval: 'Awaiting approval', sending: 'Sending',
  accepted: 'Accepted by AgentMail', verified: 'Verified in sender inbox',
  unconfirmed: 'Outcome unknown', rejected: 'Rejected', unavailable: 'Unavailable',
  preview: 'Local preview',
}

function recordCount(count, name) {
  return `${count} ${name}${count === 1 ? '' : 's'}`
}

async function workspaceRequest(operation, fields) {
  const reply = await window.chief.workspaceRecords(operation, fields)
  if (!reply.ok) throw new Error(reply.error || 'Workspace unavailable.')
  return reply.result
}

function ExternalLink({ url, children }) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return <span>{children}</span>
  return <a href={url} onClick={event => { event.preventDefault(); void window.chief.openSourceLink(url) }}>{children}</a>
}

function InsightsPanel({ records, refresh, openTranscript, openAction, browseOnly = false, browser = false }) {
  const [filter, setFilter] = useS('')
  const [jobs, setJobs] = useS({})
  const [errors, setErrors] = useS({})
  const items = records.insights.filter(item => !filter || item.kind === filter || (filter === 'research' && (item.kind === 'claim' || item.research)))

  async function check(item) {
    setJobs(current => ({ ...current, [item.id]: true }))
    setErrors(current => ({ ...current, [item.id]: '' }))
    try {
      await workspaceRequest('fact_check', { source_id: item.source_id, insight_id: item.id })
      await refresh()
    } catch (error) {
      setErrors(current => ({ ...current, [item.id]: error.message }))
    } finally {
      setJobs(current => ({ ...current, [item.id]: false }))
    }
  }

  return <section className="workspace-page">
    <header className="workspace-heading">
      <div><h1>Insights</h1><p>{browser ? 'Findings from uploaded transcripts.' : 'Findings from imported transcripts and completed live sessions.'}</p></div>
      <label className="workspace-filter">Show <select value={filter} onChange={event => setFilter(event.target.value)}>
        <option value="">All insights</option>
        {Object.entries(insightNames).map(([kind, name]) => <option key={kind} value={kind}>{name}</option>)}
        <option value="research">Fact checks</option>
      </select></label>
    </header>
    <p className="workspace-count">{recordCount(items.length, 'insight')}</p>
    {items.length === 0 && <p className="workspace-empty">No matching insights yet.</p>}
    <div className="workspace-list">{items.map(item => <details className="workspace-card insight-card" key={item.id}>
      <summary>
        <span className={`workspace-tag tag-${item.kind}`}>{insightNames[item.kind] || item.kind}</span>
        <strong>{item.summary}</strong>
        <span className="workspace-muted workspace-filename">{item.filename}</span>
      </summary>
      <div className="workspace-card-detail">
        <p className="workspace-muted">Source excerpt</p>
        <blockquote>{item.quote}</blockquote>
        {item.historical && <>
          <p className="workspace-muted">Earlier source · {item.historical.filename}</p>
          <blockquote>{item.historical.excerpt}</blockquote>
          {(!browseOnly || records.sources.some(source => source.id === item.historical.source_id)) &&
            <button className="workspace-button" onClick={() => openTranscript(item.historical.source_id, item.historical.offset, Array.from(item.historical.excerpt).length)}>Open earlier transcript</button>}
        </>}
        <button className="workspace-button" onClick={() => openTranscript(item.source_id, item.offset, Array.from(item.quote).length)}>Open source transcript</button>
        {item.draft && (!browseOnly || records.actions.some(action => action.id === item.id)) &&
          <button className="workspace-button" onClick={() => openAction(item.id)}>Open follow-up draft</button>}
        {item.kind === 'claim' && !browseOnly && <section className="workspace-research">
          <div className="workspace-row">
            <strong>Fact check</strong>
            <button className="workspace-button" disabled={jobs[item.id] || (browser && !records.services.tavily)} onClick={() => void check(item)}>
              {jobs[item.id] ? 'Checking…' : item.research?.sources?.length ? 'Check again with Tavily' : 'Check with Tavily'}
            </button>
          </div>
          {browser && !records.services.tavily && <p>Tavily fact-checking is unavailable. Configure Tavily and Claude on the server.</p>}
          {jobs[item.id] && <p role="status">Searching with Tavily and assessing the evidence…</p>}
          {errors[item.id] && <p role="status">{errors[item.id]}</p>}
          {!item.research && !jobs[item.id] && <p className="workspace-muted">No web check has been saved.</p>}
          {item.research && !jobs[item.id] && <div role="status">
            <p><span className="workspace-tag">{{
              supported: 'Supported', contradicted: 'Contradicted', lookup: 'Evidence found',
              skipped: 'Not checked', unavailable: 'Search unavailable',
            }[item.research.status] || 'Unverified'}</span> {item.research.summary || (item.research.status === 'skipped' ? 'No skip reason was saved for this older check.' : '')}</p>
            <ul>{item.research.sources.map(source => <li key={source.url}><ExternalLink url={source.url}>{source.title || source.url}</ExternalLink></li>)}</ul>
          </div>}
        </section>}
        {!browseOnly && !browser && <ContextFeedback kind="insight" reference={item.id} sources={[item]} />}
      </div>
    </details>)}</div>
  </section>
}

function ActionsPanel({ records, refresh, selected, select, reviewOnly = false, openTranscript }) {
  const [edit, setEdit] = useS(null)
  const [approval, setApproval] = useS(null)
  const [error, setError] = useS('')
  const [busy, setBusy] = useS(false)
  const current = records.actions.find(item => item.id === selected)
  const editable = current && ['draft', 'awaiting_approval', 'rejected', 'unavailable', 'preview'].includes(current.state)

  useE(() => {
    if (current) setEdit({ recipient: current.recipient || records.controlled_recipient, subject: current.subject, body: current.body })
    else setEdit(null)
    setApproval(null)
    setError('')
  }, [selected])

  async function request(operation) {
    setBusy(true)
    setError('')
    try {
      const result = await workspaceRequest(operation, {
        action_id: selected, ...edit, token: approval?.token,
      })
      setApproval(operation === 'review_saved' ? result : null)
      await refresh()
    } catch (failure) {
      setError(failure.message)
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  return <section className="workspace-page">
    <header className="workspace-heading"><div><h1>Actions</h1><p>{reviewOnly ? 'Review saved follow-up drafts. Sending is unavailable in the browser.' : 'Review follow-up emails and see their send status.'}</p></div></header>
    {records.actions.length === 0 && <p className="workspace-empty">No saved email drafts yet.</p>}
    <div className="workspace-actions">
      <div className="workspace-list">{records.actions.map(item =>
        <button key={item.id} className="workspace-card action-row" aria-pressed={selected === item.id}
          disabled={busy} onClick={() => select(item.id)}>
          <strong>{item.subject}</strong>
          <span className={`workspace-tag tag-${item.state}`}>{actionStates[item.state] || item.state}</span>
          <span className="workspace-muted workspace-filename">{item.filename}</span>
        </button>)}</div>
      {current && <article className="workspace-card workspace-editor">
        <h2>{approval ? reviewOnly ? 'Review email' : 'Confirm email' : 'Email details'}</h2>
        <p className="workspace-muted" role="status">{actionStates[current.state]}{current.message && ` · ${current.message}`}</p>
        {current.message_id && <p className="provider-id">Provider message ID <code>{current.message_id}</code></p>}
        {openTranscript && current.source_id && <button className="workspace-button" onClick={() => openTranscript(current.source_id)}>Open source transcript</button>}
        {approval ? <>
          <p><strong>To</strong> {approval.recipient}</p>
          <p><strong>Subject</strong> {approval.subject}</p>
          <pre>{approval.body}</pre>
          {!reviewOnly && approval.recipient !== records.controlled_recipient && <p role="status">Sending is limited to the configured test recipient.</p>}
          <div className="workspace-row">
            {!reviewOnly && <button className="workspace-button primary" disabled={busy || approval.recipient !== records.controlled_recipient}
              onClick={() => void request('approve_saved')}>{busy ? 'Working…' : 'Approve and send'}</button>}
            <button className="workspace-button" disabled={busy} onClick={() => void request('cancel_review')}>Back to draft</button>
          </div>
        </> : edit && <form onSubmit={event => { event.preventDefault(); void request('review_saved') }}>
          <label>Recipient<input type="email" maxLength={254} required readOnly={!editable} value={edit.recipient}
            onChange={event => setEdit({ ...edit, recipient: event.target.value })}/></label>
          <label>Subject<input maxLength={200} required readOnly={!editable} value={edit.subject}
            onChange={event => setEdit({ ...edit, subject: event.target.value })}/></label>
          <label>Message<textarea rows={8} maxLength={8000} required readOnly={!editable} value={edit.body}
            onChange={event => setEdit({ ...edit, body: event.target.value })}/></label>
          {editable && <button className="workspace-button primary" disabled={busy}>Review email</button>}
        </form>}
        {error && <p role="status">{error}</p>}
      </article>}
    </div>
  </section>
}

function MarkdownAnswer({ content }) {
  const renderer = new window.marked.Renderer()
  renderer.html = () => ''
  const html = window.DOMPurify.sanitize(window.marked.parse(String(content || ''), { breaks: true, renderer }), {
    ALLOWED_TAGS: ['p','br','ul','ol','li','strong','em','del','blockquote','pre','code','h1','h2','h3','h4','h5','h6','hr','table','thead','tbody','tr','th','td','a'],
    ALLOWED_ATTR: ['href','title','start'],
  })
  return <div className="markdown-answer" dangerouslySetInnerHTML={{ __html: html }}
    onClick={event => {
      const anchor = event.target.closest('a')
      if (!anchor) return
      event.preventDefault()
      const url = anchor.getAttribute('href') || ''
      if (/^https?:\/\//i.test(url)) void window.chief.openSourceLink(url)
    }} />
}

function GroundedChat({ browser = false, openTranscript, modelAvailable = true } = {}) {
  const [question, setQuestion] = useS('')
  const [useModel, setUseModel] = useS(false)
  const [messages, setMessages] = useS([])
  const [draft, setDraft] = useS(null)
  const [draftNotice, setDraftNotice] = useS('')
  const [busy, setBusy] = useS(false)

  async function ask(event) {
    event.preventDefault()
    const submitted = question.trim()
    if (!submitted || busy) return
    setBusy(true)
    setQuestion('')
    try {
      const reply = await window.chief.askContext(submitted, useModel)
      const result = reply.ok ? reply.result : {answer: reply.error, sources: []}
      setMessages(previous => [...previous, {question: submitted, ...result}])
    } catch {
      setMessages(previous => [...previous, {question: submitted, answer: 'Chat is unavailable.', sources: []}])
    } finally {
      setBusy(false)
    }
  }

  async function reviewDraft(event) {
    event.preventDefault()
    try {
      const reply = await window.chief.reviewEmailDraft(draft)
      setDraftNotice(reply.ok ? reply.result.review : reply.error)
      if (reply.ok) setDraft(null)
    } catch {
      setDraftNotice('The draft could not be opened for review.')
    }
  }

  return (
    <section className={browser ? "workspace-page web-chat" : undefined} style={{ flex:1, overflow:'auto', padding:'16px 24px' }}>
      {browser && <header className="workspace-heading"><h1>Chat</h1></header>}
      {messages.map((message, index) => (
        <article key={index} className={browser ? "workspace-card web-answer" : undefined}>
          <h3>{message.question}</h3>
          <MarkdownAnswer content={message.answer} />
          {message.trace_id && <small>Trace ID {message.trace_id}</small>}
          {message.sources.map((source, number) => (
            <details key={number}>
              <summary>{source.filename}</summary>
              <blockquote style={{ whiteSpace:'pre-wrap' }}>{source.excerpt}</blockquote>
              {browser && source.source_id && <button className="workspace-button"
                onClick={() => openTranscript(source.source_id, source.offset, Array.from(source.excerpt).length)}>Open source transcript</button>}
            </details>
          ))}
          {message.sources.length > 0 && <button className={browser ? 'workspace-button' : 'cf-btn cf-btn-quiet cf-btn-sm'} onClick={() => {
            setDraftNotice('')
            setDraft({
              subject: '',
              body: message.mode === 'claude' ? message.answer : '',
              sourceIds: [...new Set(message.sources.map(source => source.source_id).filter(Boolean))],
            })
          }}>Prepare follow-up email</button>}
          {!browser && message.id && <ContextFeedback kind="chat" reference={message.id} sources={message.sources} />}
        </article>
      ))}
      {draft && <form onSubmit={reviewDraft}>
        <h3>Follow-up draft</h3>
        <label>Subject <input value={draft.subject} required maxLength={200} onChange={e => setDraft({...draft, subject: e.target.value})} /></label>
        <label>Message <textarea value={draft.body} required maxLength={8000} rows={5} onChange={e => setDraft({...draft, body: e.target.value})} style={{ display:'block', width:'100%' }} /></label>
        <button className={browser ? 'workspace-button primary' : 'cf-btn cf-btn-primary cf-btn-sm'}>Review email</button>
        <button type="button" className={browser ? 'workspace-button' : 'cf-btn cf-btn-quiet cf-btn-sm'} onClick={() => setDraft(null)}>Cancel</button>
      </form>}
      {draftNotice && <p role="status">{draftNotice}</p>}
      <form onSubmit={ask}>
        <label htmlFor="memory-question">{browser ? 'Ask about your saved transcripts' : 'Ask about your transcripts or sample memory'}</label>
        <textarea id="memory-question" value={question} maxLength={2000} rows={3} onChange={e => setQuestion(e.target.value)} style={{ display:'block', width:'100%' }} />
        <label><input type="checkbox" checked={useModel} disabled={!modelAvailable} onChange={e => setUseModel(e.target.checked)} /> {modelAvailable ? 'Allow Claude to receive this question and relevant excerpts' : 'Claude is unavailable. Chat will return matching excerpts.'}</label>
        <p><button className={browser ? 'workspace-button primary' : 'cf-btn cf-btn-primary cf-btn-sm'} disabled={busy || !question.trim()}>{busy ? 'Searching…' : 'Ask'}</button></p>
      </form>
    </section>
  )
}
