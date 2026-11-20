/* eslint-disable */
// Chief UI primitives + surface components.
// Exposed on window for sibling Babel scripts.

const { useState, useEffect, useRef } = React;

/* ============================================================
   Icons — inline 1.5px Lucide-style strokes
   ============================================================ */
const I = {
  mic: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="19" x2="12" y2="22"/>
    </svg>
  ),
  pause: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>
    </svg>
  ),
  play: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="6 4 20 12 6 20 6 4"/>
    </svg>
  ),
  settings: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
  ),
  x: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  ),
  check: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  ),
  calendar: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  mail: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>
    </svg>
  ),
  brain: (p) => (
    <svg width={p.s||16} height={p.s||16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5a3 3 0 1 0-5.9 0M12 5a3 3 0 1 1 5.9 0M6.1 5A4 4 0 0 0 4 12.5M17.9 5A4 4 0 0 1 20 12.5M4 12.5a4 4 0 0 0 4 7M20 12.5a4 4 0 0 1-4 7M12 5v15M8 19.5a3 3 0 0 0 4 0M12 19.5a3 3 0 0 0 4 0"/>
    </svg>
  ),
  chevron: (p) => (
    <svg width={p.s||14} height={p.s||14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6"/>
    </svg>
  ),
  // Product mark
  mark: (p) => (
    <svg width={p.s||18} height={p.s||18} viewBox="0 0 64 64" fill="none">
      <path d="M18 41 L30 30 H44 M38 24 L44 30 L38 36" stroke="currentColor" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx="18" cy="41" r="4" fill="currentColor"/>
    </svg>
  )
};

/* ============================================================
   Primitives — Button, Badge, Hotkey, Divider, Toggle
   ============================================================ */
const Button = ({ variant = "ghost", children, hk, onClick, icon, style }) => {
  const cls = `cf-btn cf-btn-${variant}`;
  return (
    <button className={cls} onClick={onClick} style={style}>
      {icon}
      <span>{children}</span>
      {hk && <span className="cf-hk">{hk}</span>}
    </button>
  );
};

const Badge = ({ tone = "idle", children }) => (
  <span className={`cf-badge cf-badge-${tone}`}>
    <span className="cf-badge-dot"/>
    {children}
  </span>
);

const Hotkey = ({ children }) => <kbd className="cf-kbd">{children}</kbd>;

const Toggle = ({ on, onChange }) => (
  <div className={`cf-toggle ${on ? "is-on" : ""}`} onClick={() => onChange && onChange(!on)}/>
);

/* ============================================================
   StatusRing — the ambient indicator
   states: idle | listening | processing | whispering
   ============================================================ */
const StatusRing = ({ state = "listening", size = 22 }) => {
  const stroke = 1.25;
  const r = (size - stroke*2) / 2;
  const cx = size / 2;
  const dotColor = {
    listening:  "#4caf85",
    whispering: "#d99366",
    processing: "#9d7bb5",
    idle:       "#a8a29e",
  }[state];
  return (
    <div className={`cf-ring cf-ring-${state}`} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} fill="none">
        <circle cx={cx} cy={cx} r={r} className="cf-ring-track" strokeWidth={stroke}/>
        {state === "listening" && (
          <circle cx={cx} cy={cx} r={r} strokeWidth={stroke} strokeLinecap="round"
                  strokeDasharray={`${r*1.0} ${r*5}`} className="cf-ring-sweep-live"/>
        )}
        {state === "processing" && (
          <circle cx={cx} cy={cx} r={r} stroke="#9d7bb5" strokeWidth={stroke} strokeLinecap="round"
                  strokeDasharray={`${r*Math.PI} ${r*2*Math.PI}`}/>
        )}
        {state === "whispering" && (
          <circle cx={cx} cy={cx} r={r} strokeWidth={stroke*1.2} strokeLinecap="round"
                  className="cf-ring-sweep-pulse"/>
        )}
      </svg>
      <div className="cf-ring-center">
        <span className="cf-ring-dot" style={{ background: dotColor }}/>
      </div>
    </div>
  );
};

/* ============================================================
   WhisperCard — THE hero. Always-on-top overlay.
   ============================================================ */
const WhisperCard = ({ eyebrow = "Last call", payload, hint, hotkey = "esc", onDismiss }) => {
  return (
    <div className="cf-whisper">
      <div className="cf-whisper-body">
        <div className="cf-whisper-eyebrow">{eyebrow}</div>
        <div className="cf-whisper-payload">{payload}</div>
        {hint && <div className="cf-whisper-hint">{hint}</div>}
      </div>
      <div className="cf-whisper-foot">
        <span className="cf-whisper-foot-hk"><Hotkey>{hotkey}</Hotkey> dismiss</span>
        <span className="cf-whisper-foot-meta">14s ago</span>
      </div>
    </div>
  );
};

/* ============================================================
   ActionCard — secondary variant with approve/discard
   ============================================================ */
const ActionCard = ({ kind = "email", title, summary, preview, onApprove, onDiscard }) => {
  const icon = kind === "email" ? <I.mail s={14}/> : <I.calendar s={14}/>;
  const label = kind === "email" ? "Email draft" : "Calendar event";
  return (
    <div className="cf-action">
      <div className="cf-action-head">
        <span className="cf-action-kind">{icon}{label}</span>
        <span className="cf-action-time">ready · 0:03</span>
      </div>
      <div className="cf-action-title">{title}</div>
      {summary && <div className="cf-action-summary">{summary}</div>}
      {preview && <div className="cf-action-preview">{preview}</div>}
      <div className="cf-action-foot">
        <Button variant="primary" hk="⌘ ↵" icon={<I.check s={13}/>} onClick={onApprove}>Approve</Button>
        <Button variant="quiet" icon={<I.x s={13}/>} onClick={onDiscard}>Discard</Button>
      </div>
    </div>
  );
};

/* ============================================================
   TrayMenu — menu-bar dropdown
   ============================================================ */
const TrayMenu = ({ state = "listening", onSelect }) => {
  const stateLabel = { listening: "Listening", paused: "Paused", action: "Action pending" }[state];
  const meta = {
    listening: "Sales call · 24:18 · Zoom",
    paused: "Manually paused · ⌥⇧ P to resume",
    action: "1 action ready for review",
  }[state];
  return (
    <div className="cf-tray">
      <div className="cf-tray-status">
        <StatusRing state={state === "action" ? "whispering" : state === "paused" ? "idle" : "listening"} size={18}/>
        <div className="cf-tray-status-text">
          <div className="cf-tray-status-label">{stateLabel}</div>
          <div className="cf-tray-status-meta">{meta}</div>
        </div>
      </div>
      <div className="cf-tray-divider"/>
      <button className="cf-tray-row" onClick={() => onSelect && onSelect("pause")}>
        <I.pause s={14}/>
        <span>Pause listening</span>
        <Hotkey>⌥⇧ P</Hotkey>
      </button>
      <button className="cf-tray-row" onClick={() => onSelect && onSelect("whisper")}>
        <I.mark s={14}/>
        <span>Force a whisper</span>
        <Hotkey>⌥ Space</Hotkey>
      </button>
      <button className="cf-tray-row" onClick={() => onSelect && onSelect("memory")}>
        <I.brain s={14}/>
        <span>Open memory</span>
      </button>
      <div className="cf-tray-divider"/>
      <button className="cf-tray-row" onClick={() => onSelect && onSelect("settings")}>
        <I.settings s={14}/>
        <span>Settings…</span>
      </button>
      <button className="cf-tray-row cf-tray-row-quiet" onClick={() => onSelect && onSelect("quit")}>
        <span style={{width:14}}/>
        <span>Quit Context to Action by Ipsita</span>
        <Hotkey>⌘ Q</Hotkey>
      </button>
    </div>
  );
};

/* ============================================================
   MenuBarIcon — what lives in the macOS menu bar
   ============================================================ */
const MenuBarIcon = ({ state = "listening", active }) => (
  <div className={`cf-menubar-icon ${active ? "is-active" : ""}`}>
    <StatusRing state={state} size={16}/>
  </div>
);

Object.assign(window, {
  I, Button, Badge, Hotkey, Toggle, StatusRing,
  WhisperCard, ActionCard, TrayMenu, MenuBarIcon
});
