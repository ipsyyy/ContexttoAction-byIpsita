const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('chief', {
  // Renderer -> main (forwarded to Python)
  sendSttPartial: (text) => ipcRenderer.send('stt-partial', text),
  sendSttFinal: (text) => ipcRenderer.send('stt-final', text),
  sendAudioUtterance: (audioB64, sampleRate) =>
    ipcRenderer.send('audio-utterance', { audio: audioB64, sample_rate: sampleRate }),
  approveAction: (approvalId) => ipcRenderer.send('approve-action', approvalId),
  discardAction: (approvalId) => ipcRenderer.send('discard-action', approvalId),
  onEmailOutcome: (cb) => ipcRenderer.on('email-outcome', (_e, result) => cb(result)),
  openSettings: () => ipcRenderer.send('open-settings'),
  closeSettings: () => ipcRenderer.send('close-settings'),
  dismissCard: () => ipcRenderer.send('dismiss-card'),
  getConfig: () => ipcRenderer.invoke('get-config'),
  setConfigKey: (key, value) => ipcRenderer.invoke('set-config-key', key, value),
  saveContextFeedback: fields => ipcRenderer.invoke('context-feedback', fields),
  reviewEmailDraft: fields => ipcRenderer.invoke('review-email-draft', fields),
  askContext: (question, useModel) => ipcRenderer.invoke('grounded-chat', question, useModel),
  workspaceRecords: (operation, fields) => ipcRenderer.invoke('workspace-records', operation, fields),
  openSourceLink: url => ipcRenderer.invoke('open-source-link', url),
  transcripts: (operation, id) => ipcRenderer.invoke('transcripts', operation, id),
  toggleListening: () => ipcRenderer.send('toggle-listening'),
  getAppState: () => ipcRenderer.invoke('get-app-state'),
  listMemories: (limit) => ipcRenderer.send('list-memories', limit),
  onMemoriesList: (cb) => ipcRenderer.on('memories_list', (_e, payload) => cb(payload)),
  requestVoiceStatus: () => ipcRenderer.send('voice-status-request'),
  enrollVoice: (audioB64, sampleRate) =>
    ipcRenderer.send('enroll-voice', { audio: audioB64, sample_rate: sampleRate }),
  clearVoice: () => ipcRenderer.send('clear-voice'),
  onVoiceStatus: (cb) => ipcRenderer.on('voice_status', (_e, payload) => cb(payload)),
  setCounterparty: (hint) => ipcRenderer.send('set-counterparty', hint),
  requestCounterpartyStatus: () => ipcRenderer.send('counterparty-status-request'),
  onCounterpartyStatus: (cb) => ipcRenderer.on('counterparty_status', (_e, payload) => cb(payload)),
  requestRetrievalStatus: () => ipcRenderer.send('retrieval-status-request'),
  onRetrievalStatus: (cb) => ipcRenderer.on('retrieval_status', (_e, payload) => cb(payload)),
  requestPhoneStatus: () => ipcRenderer.send('phone-status-request'),
  onPhoneStatus: (cb) => ipcRenderer.on('phone_status', (_e, payload) => cb(payload)),
  phoneHangUp: () => ipcRenderer.send('phone-hangup'),
  phoneReset: () => ipcRenderer.send('phone-reset'),
  openLiveCall: () => ipcRenderer.send('open-live-call'),
  closeLiveCall: () => ipcRenderer.send('close-live-call'),
  openMainCall: () => ipcRenderer.send('open-main-call'),
  closeMainCall: () => ipcRenderer.send('close-main-call'),
  requestMainCallStatus: () => ipcRenderer.send('main-call-status-request'),
  onMainCallStatus: (cb) => ipcRenderer.on('main_call_status', (_e, payload) => cb(payload)),
  endMainCall: () => ipcRenderer.send('main-call-end'),
  resetMainCall: () => ipcRenderer.send('main-call-reset'),

  // Main -> renderer (events from Python)
  // Whisper payload is now {content, provenance?}. Older callers that
  // expect a string still work because cb receives the whole object and
  // the orb knows to read `.content`.
  onWhisper: (cb) => ipcRenderer.on('whisper', (_e, payload) => cb(payload)),
  onActionCard: (cb) => ipcRenderer.on('action_card', (_e, event) => cb(event)),
  onDismiss: (cb) => ipcRenderer.on('dismiss', () => cb()),
  onChiefState: (cb) => ipcRenderer.on('chief-state', (_e, state) => cb(state)),
  onMuteMic: (cb) => ipcRenderer.on('mute-mic', () => cb()),
  onUnmuteMic: (cb) => ipcRenderer.on('unmute-mic', () => cb()),
  moveOrb: (dx, dy) => ipcRenderer.send('orb-move', dx, dy),
  orbCollapsed: () => ipcRenderer.send('orb-collapsed'),
  fitOrb: (width, height) => ipcRenderer.send('orb-fit', { width, height }),
})
