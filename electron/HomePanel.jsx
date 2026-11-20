function HomeIcon({ type }) {
  const paths = {
    insights: 'M8 15c1 2 2 3 4 3s3-1 4-3m-8 0c0-1.8-3-3-3-7a7 7 0 0 1 14 0c0 4-3 5.2-3 7m-6 6h4',
    review: 'M12 3 2.5 19.5h19L12 3zm0 6v4m0 3h.01',
    actions: 'M3 5h18v14H3zm0 1 9 7 9-7',
    transcripts: 'M5 4h14v16H5zm3 4h8m-8 4h8m-8 4h5',
    memory: 'M9 8h-3a4 4 0 1 0 0 8h3m6-8h3a4 4 0 1 1 0 8h-3m-7-4h8',
  }
  return <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
    <path d={paths[type]} />
  </svg>
}

function HomePanel({ records, loaded, unread, navigate, openTranscript, browser = false }) {
  const pending = records.actions.filter(item => ['draft', 'awaiting_approval', 'sending'].includes(item.state))
  const memories = records.insights.filter(item => item.kind === 'connection' || item.historical)
  const metrics = [
    { label: 'Saved insights', value: records.insights.length, icon: 'insights', view: 'insights' },
    { label: 'Important findings to review', value: unread, icon: 'review', view: 'insights', hidden: browser },
    { label: 'Pending email drafts and actions', value: pending.length, icon: 'actions', view: 'actions' },
    { label: 'Saved conversations', value: records.sources.length, icon: 'transcripts', view: 'transcripts' },
    { label: 'Relevant memories', value: memories.length, icon: 'memory', view: 'insights' },
  ]
  return <main className="workspace-page home-page">
    <section className="home-welcome">
      <div>
        <h1>Welcome back, Ipsita</h1>
        <p>Your conversations, decisions, and follow-ups—all in one place.</p>
      </div>
      <div className="home-welcome-actions">
        {!browser && <button className="workspace-button" onClick={() => navigate('session')}>Open Session</button>}
        <button className="workspace-button primary" onClick={() => navigate('transcripts')}>Open Transcripts</button>
      </div>
    </section>
    {!loaded && <p role="status" className="home-status">Loading your workspace…</p>}
    <section className="home-metrics" aria-label="Workspace summary" aria-busy={!loaded}>
      {metrics.filter(metric => !metric.hidden).map(metric => <button key={metric.label} className="home-metric" onClick={() => navigate(metric.view)}>
        <span className={`home-metric-icon metric-${metric.icon}`}><HomeIcon type={metric.icon} /></span>
        <strong>{loaded ? metric.value : '—'}</strong>
        <span>{metric.label}</span>
      </button>)}
    </section>
    <div className="home-activity">
      <section className="workspace-card home-section">
        <header><h2>Recent conversations</h2><button className="home-text-button" onClick={() => navigate('transcripts')}>View all</button></header>
        {loaded && records.sources.length === 0 && <p className="workspace-muted">{browser
          ? 'Upload a transcript to see it here.'
          : 'Import a transcript or finish a live session to see it here.'}</p>}
        {records.sources.slice(0, 4).map(source => <button className="home-record" key={source.id} onClick={() => openTranscript(source.id)}>
          <span>{source.filename}</span>
          <small>{source.state === 'complete' ? recordCount(source.count, 'insight') : source.state === 'pending' ? 'Analyzing…' : 'Open transcript'}{source.imported_at ? ' · ' + new Date(source.imported_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''}</small>
        </button>)}
      </section>
      <section className="workspace-card home-section">
        <header><h2>Relevant memories</h2><button className="home-text-button" onClick={() => navigate('insights')}>Open Insights</button></header>
        {loaded && memories.length === 0 && <p className="workspace-muted">Links to earlier conversations will appear here.</p>}
        {memories.slice(0, 3).map(item => <button className="home-record" key={item.id} onClick={() => openTranscript(item.source_id)}>
          <span>{item.summary}</span>
          <small>{item.filename}</small>
        </button>)}
      </section>
    </div>
  </main>
}
