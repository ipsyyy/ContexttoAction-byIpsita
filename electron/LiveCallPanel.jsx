/* eslint-disable */
const { useState: useS, useEffect: useE, useRef: useR } = React

function fmtElapsed(startISO) {
  if (!startISO) return '—'
  const start = new Date(startISO).getTime()
  if (Number.isNaN(start)) return '—'
  const ms = Math.max(0, Date.now() - start)
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function fmtNumber(n) {
  if (!n) return ''
  // Pretty-format E.164 US numbers: +1XXXXXXXXXX → +1 (XXX) XXX-XXXX
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(n)
  if (m) return `+1 (${m[1]}) ${m[2]}-${m[3]}`
  return n
}

const STATE_COLORS = {
  idle:      { ring: 'var(--hairline-strong)', label: 'Idle',       hue: 'var(--muted)' },
  dialing:   { ring: '#9d7bb5',                label: 'Dialing…',   hue: '#9d7bb5' },
  ringing:   { ring: '#f39c12',                label: 'Ringing…',   hue: '#f39c12' },
  connected: { ring: '#27ae60',                label: 'On call',    hue: '#27ae60' },
  ended:     { ring: 'var(--hairline-strong)', label: 'Call ended', hue: 'var(--muted)' },
  failed:    { ring: '#c0392b',                label: 'Call failed',hue: '#c0392b' },
}

function Avatar({ name, state }) {
  const initial = (name || '?').trim().charAt(0).toUpperCase() || '?'
  const cfg = STATE_COLORS[state] || STATE_COLORS.idle
  const pulsing = state === 'dialing' || state === 'ringing'
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', padding:'8px 0 12px' }}>
      <div style={{
        position:'relative',
        width: 96, height: 96, borderRadius:'50%',
        background: 'var(--canvas)',
        border: `2px solid ${cfg.ring}`,
        display:'flex', alignItems:'center', justifyContent:'center',
        boxShadow: state === 'connected' ? `0 0 0 6px ${cfg.ring}22` : 'none',
        transition: 'box-shadow 240ms, border-color 240ms',
      }}>
        <span style={{ font:'600 36px/1 var(--font-ui)', color:'var(--ink)' }}>{initial}</span>
        {pulsing && (
          <span style={{
            position:'absolute', inset:-6, borderRadius:'50%',
            border: `2px solid ${cfg.ring}`, opacity: 0.4,
            animation: 'cf-call-pulse 1.4s ease-out infinite',
          }} />
        )}
      </div>
      <style>{`@keyframes cf-call-pulse { 0% { transform: scale(1); opacity: 0.5; } 100% { transform: scale(1.25); opacity: 0; } }`}</style>
    </div>
  )
}

function StatusPill({ state, available }) {
  const cfg = STATE_COLORS[state] || STATE_COLORS.idle
  return (
    <div style={{ display:'flex', alignItems:'center', gap:8, padding:'4px 10px', borderRadius:999, border:'1px solid var(--hairline)', background:'var(--canvas)' }}>
      <span style={{ width:6, height:6, borderRadius:'50%', background:cfg.hue }} />
      <span style={{ font:'500 12px var(--font-ui)', color:'var(--ink)' }}>{cfg.label}</span>
      {!available && (
        <span style={{ font:'10px var(--font-mono)', color:'var(--muted)' }}>· simulated</span>
      )}
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

const LiveCallPanel = () => {
  const [status, setStatus] = useS({
    state: 'idle',
    to_number: '', callee_name: '', purpose: '',
    started_at: '', ended_at: '',
    transcript: [], available: false,
  })
  const [elapsed, setElapsed] = useS('—')
  const listenerBound = useR(false)
  const scrollRef = useR(null)

  useE(() => {
    if (listenerBound.current) return
    listenerBound.current = true
    window.chief.onPhoneStatus(s => setStatus(prev => ({ ...prev, ...s })))
    window.chief.requestPhoneStatus()
  }, [])

  useE(() => {
    const id = setInterval(() => {
      if (status.state === 'connected') setElapsed(fmtElapsed(status.started_at))
      else if (status.state === 'ended' && status.started_at && status.ended_at) {
        const start = new Date(status.started_at).getTime()
        const end   = new Date(status.ended_at).getTime()
        const t = Math.max(0, Math.floor((end - start) / 1000))
        setElapsed(`${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`)
      } else setElapsed('—')
    }, 500)
    return () => clearInterval(id)
  }, [status.state, status.started_at, status.ended_at])

  useE(() => {
    if (!scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [status.transcript.length])

  const cfg = STATE_COLORS[status.state] || STATE_COLORS.idle
  const inCall = status.state === 'dialing' || status.state === 'ringing' || status.state === 'connected'
  const terminal = status.state === 'ended' || status.state === 'failed'

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', background:'var(--surface-card)', fontFamily:'var(--font-ui)' }}>

      {/* Title bar */}
      <div style={{
        height: 44, display:'flex', alignItems:'center', padding:'0 16px',
        borderBottom:'1px solid var(--hairline)', WebkitAppRegion: 'drag', flexShrink: 0,
      }}>
        <div style={{ flex:1, display:'flex', alignItems:'baseline', gap:8 }}>
          <span style={{ font:'600 13px/1 var(--font-ui)', color:'var(--ink)', letterSpacing:'-0.01em' }}>
            Context to Action by Ipsita · Side call
          </span>
          <span style={{ font:'500 10px/1 var(--font-ui)', color:'var(--muted)', textTransform:'uppercase', letterSpacing:'0.08em', border:'1px solid var(--hairline)', borderRadius:4, padding:'2px 6px' }}>
            Parallel to primary call
          </span>
        </div>
        <button
          onClick={() => window.chief.closeLiveCall()}
          style={{
            WebkitAppRegion: 'no-drag',
            width: 22, height: 22, borderRadius:'50%', border:'none',
            background:'var(--hairline-strong)', color:'var(--muted)',
            cursor:'pointer', fontSize:15, lineHeight:1,
            display:'flex', alignItems:'center', justifyContent:'center', padding:0,
          }}
        >×</button>
      </div>

      {/* Header — avatar, name, number, status */}
      <div style={{ padding:'18px 24px 0', display:'flex', flexDirection:'column', alignItems:'center' }}>
        <Avatar name={status.callee_name || status.to_number} state={status.state} />
        <div style={{ font:'600 17px/1.2 var(--font-ui)', color:'var(--ink)', textAlign:'center' }}>
          {status.callee_name || (status.state === 'idle' ? 'No side call' : 'Unknown')}
        </div>
        <div style={{ font:'13px/1.2 var(--font-mono)', color:'var(--muted)', marginTop:4, textAlign:'center' }}>
          {fmtNumber(status.to_number) || (status.available ? 'AgentPhone connected' : 'AgentPhone in simulated mode')}
        </div>
        <div style={{ marginTop:10 }}>
          <StatusPill state={status.state} available={status.available} />
        </div>
        <div style={{ font:'500 22px/1 var(--font-mono)', color:'var(--ink)', marginTop:14, letterSpacing:'0.05em' }}>
          {elapsed}
        </div>
        {status.purpose && (
          <div style={{ font:'12px/1.4 var(--font-ui)', color:'var(--body)', marginTop:10, textAlign:'center', maxWidth:380 }}>
            <span style={{ color:'var(--muted)' }}>Purpose: </span>{status.purpose}
          </div>
        )}
      </div>

      {/* Post-call result (visible after call ends) */}
      {status.result && (status.state === 'ended' || status.state === 'failed') && (
        <div style={{ margin:'14px 16px 0', padding:'12px 14px', borderRadius:8,
            background:'#fbeee2', border:'1px solid #f3d4b5', color:'#7c4015',
            font:'500 13px/1.45 var(--font-ui)' }}>
          <div style={{ font:'600 10px/1 var(--font-ui)', textTransform:'uppercase', letterSpacing:'0.08em', color:'#a05a1f', marginBottom:6 }}>
            Result
          </div>
          {status.result}
        </div>
      )}

      {/* Transcript */}
      <div style={{ flex:1, margin:'16px 16px 0', padding:'12px 14px', overflow:'hidden', display:'flex', flexDirection:'column', border:'1px solid var(--hairline)', borderRadius:8, background:'var(--canvas)' }}>
        <div style={{ font:'600 11px/1 var(--font-ui)', color:'var(--muted)', letterSpacing:'0.08em', textTransform:'uppercase', marginBottom:8 }}>
          Side-call transcript
        </div>
        <div ref={scrollRef} style={{ flex:1, overflowY:'auto', paddingRight:4 }}>
          {status.transcript.length === 0 ? (
            <div style={{ font:'12px var(--font-ui)', color:'var(--muted)', padding:'8px 0' }}>
              {inCall ? 'Waiting for the side-call agent to speak…' : 'The side-call transcript will appear here once a side call is connected. Your primary call (the one you’re overhearing) is unaffected.'}
            </div>
          ) : (
            status.transcript.map((e, i) => <TranscriptLine key={i} entry={e} />)
          )}
        </div>
      </div>

      {/* Footer — hang up / reset */}
      <div style={{ padding:'14px 18px 18px', display:'flex', justifyContent:'center', gap:10, flexShrink:0 }}>
        {inCall && (
          <button
            onClick={() => window.chief.phoneHangUp()}
            className="cf-btn cf-btn-primary"
            style={{ background:'#c0392b', borderColor:'#c0392b', minWidth:150,
                display:'flex', alignItems:'center', justifyContent:'center', gap:8, color:'white' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67"/>
              <line x1="22" y1="2" x2="2" y2="22"/>
            </svg>
            End call
          </button>
        )}
        {terminal && (
          <button
            onClick={() => window.chief.phoneReset()}
            className="cf-btn cf-btn-ghost"
          >Clear</button>
        )}
        {status.state === 'idle' && (
          <div style={{ font:'12px/1.5 var(--font-ui)', color:'var(--muted)', textAlign:'center', maxWidth:360 }}>
            Your assistant prepares a side call when your primary conversation calls for a quick lookup with a third party. Approve the “Side call” card on the orb to start one.
          </div>
        )}
      </div>

    </div>
  )
}
