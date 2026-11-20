// Launch the Mac app with a new library and exercise the real desktop bridge.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { app, dialog } = require('electron')

fs.mkdirSync('.local-data', { recursive: true })
const workspace = fs.mkdtempSync(path.resolve('.local-data', 'clean-start-'))
process.env.CONTEXT_AGENT_DATA_DIR = workspace
process.env.PYTHON_DOTENV_DISABLED = '1'
for (const key of Object.keys(process.env)) {
  if (/^(ANTHROPIC_|AGENTMAIL_|AGENTPHONE_|ELEVENLABS_|LANGSMITH_|MOSS_|SUPERMEMORY_|TAVILY_|SIE_|SUPERLINKED_)/.test(key)) {
    delete process.env[key]
  }
}
app.setPath('userData', path.join(workspace, 'profile'))
dialog.showOpenDialog = async () => ({
  canceled: false,
  filePaths: [path.resolve(__dirname, 'fixtures', 'workshop.txt')],
})

app.on('browser-window-created', (_, window) => {
  window.webContents.on('did-finish-load', async () => {
    if (!window.webContents.getURL().endsWith('/main-call.html')) return
    const run = code => window.webContents.executeJavaScript(code)
    async function until(code) {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await run(code)) return
        await new Promise(resolve => setTimeout(resolve, 150))
      }
      throw new Error('Missing renderer state: ' + code)
    }
    function press(label) {
      return until(`(() => {
        const button = [...document.querySelectorAll('button')].find(element =>
          element.textContent.trim() === ${JSON.stringify(label)});
        if (!button || button.disabled) return false;
        button.click();
        return true;
      })()`)
    }
    try {
      await until('document.title === "Context to Action by Ipsita" && document.querySelector(".home-welcome h1")')
      await until('window.chief.workspaceRecords("workspace").then(reply => reply.ok && reply.result.sources.length === 0)')
      await press('Transcripts')
      await press('Import transcript')
      await until('document.body.textContent.includes("workshop.txt")')
      const reply = await run('window.chief.askContext("What did Ada say about the purple invoice router?", false)')
      assert.equal(reply.ok, true)
      assert.equal(reply.result.mode, 'local')
      assert(reply.result.sources.some(source => source.filename === 'workshop.txt' && source.excerpt.includes('purple invoice router')))
      console.log('PASS fresh Electron launch, worker bridge, synthetic transcript upload and cited Chat')
      app.quit()
    } catch (error) {
      console.error(error.message)
      app.once('will-quit', () => app.exit(1))
      app.quit()
    }
  })
})
require('../electron/main.js')
