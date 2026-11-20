// Run in Electron with an isolated data directory and only Anthropic/Tavily credentials.
const assert = require('node:assert/strict')
const path = require('node:path')
const { app, dialog } = require('electron')

assert.ok(process.env.CONTEXT_AGENT_DATA_DIR)
assert.ok(process.env.CONTEXT_AGENT_BRIDGE_PORT)
process.env.PYTHON_DOTENV_DISABLED = '1'
for (const key of Object.keys(process.env)) {
  if (/^(AGENTMAIL|AGENTPHONE|MOSS_|SUPERMEMORY|LANGSMITH|ELEVENLABS)/.test(key)) delete process.env[key]
}
app.setPath('userData', path.join(process.env.CONTEXT_AGENT_DATA_DIR, 'electron-test'))
const file = 'public-claim.txt'
dialog.showOpenDialog = async () => ({
  canceled: false, filePaths: [path.resolve(__dirname, 'fixtures', file)],
})
let whispers = 0

app.on('browser-window-created', (_, window) => {
  const send = window.webContents.send.bind(window.webContents)
  window.webContents.send = (name, ...args) => {
    if (name === 'whisper') whispers++
    return send(name, ...args)
  }
  window.webContents.on('did-finish-load', async () => {
    if (!window.webContents.getURL().endsWith('/main-call.html')) return
    const execute = code => window.webContents.executeJavaScript(code)
    async function until(code, attempts = 250) {
      for (let i = 0; i < attempts; i++) {
        const result = await execute(code)
        if (result) return result
        await new Promise(resolve => setTimeout(resolve, 200))
      }
      throw new Error('Expected renderer state did not appear: ' + code.slice(0, 110))
    }
    async function press(label, scope = 'body') {
      await until(`(() => {
        const button = [...document.querySelectorAll(${JSON.stringify(scope + ' button')})].find(b =>
          b.textContent.trim() === ${JSON.stringify(label)})
        if (!button || button.disabled) return false
        button.click()
        return true
      })()`)
    }
    try {
      const reopening = process.env.CONTEXT_CHECK_REOPEN === '1'
      if (!reopening) {
        await press('Transcripts', 'nav')
        await until(`window.chief.workspaceRecords('workspace').then(reply => reply.ok)`)
        await press('Import transcript')
        await until(`document.body.textContent.includes('Analysis complete') && !!document.querySelector('.workspace-dot')`)
      }
      await press('Insights', 'nav')
      await execute(`(() => {
        const filter = document.querySelector('.workspace-filter select')
        filter.value = 'research'
        filter.dispatchEvent(new Event('change', { bubbles: true }))
      })()`)
      const card = `[...document.querySelectorAll('.insight-card')].find(card =>
        card.querySelector('.tag-claim') && card.querySelector('.workspace-filename').textContent === ${JSON.stringify(file)}
        && card.textContent.includes('2022') && card.textContent.includes('12 billion'))`
      await until(`!!(${card})`)
      await execute(`(${card}).querySelector('summary').click()`)
      if (!reopening) {
        await until(`!document.querySelector('.workspace-dot') &&
          document.querySelector('.insight-card[open]').textContent.includes('Saved for later review')`)
        await press('Check with Tavily', '.insight-card[open]')
        await until(`document.querySelector('.insight-card[open]').textContent.includes('Searching with Tavily')`)
      }
      const outcome = await until(`(() => {
        const research = document.querySelector('.insight-card[open] .workspace-research')
        if (!research || ![...research.querySelectorAll('.workspace-tag')].some(x => x.textContent === 'Contradicted')) return false
        return { text: research.textContent, urls: [...research.querySelectorAll('a')].map(a => a.href) }
      })()`)
      assert.match(outcome.text, /2018/)
      assert.match(outcome.text, /7\.5/)
      assert.ok(outcome.urls.length)
      assert.equal(whispers, 0)
      console.log(`PASS ${reopening ? 'restart readback' : 'import, unread, claim, loading and provider assessment'} · ${outcome.urls.length} cited HTTPS sources`)
      for (const source of outcome.urls) console.log('Source:', source)
      app.quit()
    } catch (error) {
      console.error(error.message)
      app.once('will-quit', () => app.exit(1))
      app.quit()
    }
  })
})
require('../electron/main.js')
