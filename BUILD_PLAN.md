# Personal Context-to-Action Agent — Coding Session Build Plan

**Reference project:** https://github.com/bliu8/chief
**Target:** Mac desktop personal agent built by adapting Chief, using its supported open-source code and retaining required license/copyright attribution.
**Status:** Historical design plan. See [README.md](README.md) for the implemented architecture, startup commands, and verified results.
**Constraints:** No Docker. Prioritize a functional local application, minimal new services and real evaluated behavior over appearance. No demo-video work.

## A. Product we are building

**Problem:** Lessons, decisions, examples and commitments from workshops, mentoring sessions, meetings and AI-building experiments become scattered. The user cannot reliably recall and apply them at the right moment.

**Promise:** Import previous transcripts **or** listen live, preserve source-grounded personal memory, chat about and connect past experiences, provide selective proactive alerts, take an approved practical action, verify what actually occurred, and capture corrections for future improvement.

### Core user journey
1. User imports a TXT/MD transcript (CSV not necessary) or starts an authorized microphone listening session.
2. System stores the original source and extracts useful, source-linked memories (examples, decisions, corrections, commitments). It should also allow preloaded context.
3. User chats: “What example did the speaker give?” and “How does that connect with my current AI-agent project?” Answer with source snippets rather than inventing details.
4. During listening, the desktop orb intervenes only on consequential, relevant moments; otherwise it remains silent.
5. User requests a follow-up email in chat or approves a suggested follow-up. Agent drafts, asks approval, sends through configured provider, and checks provider evidence of the send. ‘Sent’ is not ‘delivered/read/answered’.
6. User thumbs up/down or corrects an intervention; correction is stored for future decision-policy retrieval. Any policy changes are reviewed and checked against regression tests before activation.
7. Evaluation view/trace records retrieval, intervention, action choice, approval, provider result and error category.

### Must-have in the full target product
- Desktop app launches on Mac.
- Input A: TXT/MD transcript import, persist original text and searchable fragments with source IDs; avoid upload-driven automatic external actions.
- Input B: authorized live microphone listening with visible start/stop and consent indicator.
- Shared memory, used in both chat and live flow.
- Chat about specific transcript examples and cross-session connections, with provenance.
- One meaningful approved external action: send a sourced follow-up email to a controlled test address; verified using a real provider result and, if possible, inbox readback.
- Proactive alert versus silence decision.
- LangSmith tracing plus at least 5 repeatable behavioral evaluation examples with recorded results, including one negative/silent case, a relevance case, an approval case, and a false-success case.
- Feedback and correction persistence, with future decision behavior able to consult approved preferences. **Do not claim that self-improvement is measured until repeated evaluations actually demonstrate it.**

### Not in the initial scope
- Docker; phone calls; Google OAuth; calendar reminders; browser automation; system-audio capture; video ingestion; fine-tuning; autonomous self-rewriting prompts; multiple agents; production cloud deployment; a downloadable signed `.dmg` installer; full UI redesign.

## B. Existing Chief foundation (verified from repository README)

- **Electron:** `electron/main.js` runs windows, tray, shortcuts, local WebSocket and Python worker.
- **Listening/orb:** `electron/orb.js`, `electron/audio-worklet.js` for microphone/VAD and action approval cards.
- **Dashboard:** `electron/MainCallPanel.jsx`, `electron/LiveCallPanel.jsx`.
- **Backend bridge:** `backend/bridge.py`.
- **Speech processing:** `backend/ears/stt_elevenlabs.py`, `backend/ears/hooks.py`.
- **Memory:** `backend/memory/conversation.py`, `retrieval.py`, `claims.py`, `extractor.py`, `supermemory_store.py`, `past_calls.py`.
- **Decision:** `backend/decision/loop.py`, `agent.py`, `prompt.py`, `trace.py` — Haiku `silent` or `whisper` with fallback.
- **Actions:** `backend/action/agent.py`, `email_agentmail.py`, `hangup.py` — Sonnet tool routing and gated email send.
- **Tests:** `tests/` and documented `pytest` command.
- **Current limitation:** The main dashboard is conversation-centric, not an established full transcript-upload + open chat interface. Chief can fall back to seeded memory and simulated cards when API keys are absent; successful display **does not** prove a connected action worked.

## C. Work phases with low-level tasks and acceptance criteria

### Phase 0 — First-run validation and scope lock | 20–45 min
- Clone `https://github.com/bliu8/chief`, make your own development branch, keep upstream license notices, rename the product-facing application and repository as desired.
- Inspect `README.md`, `.env.example`, `package.json`, `requirements.txt`, actual source tree and tests; confirm Python/Node versions on Mac.
- Install using repository instructions (`npm install`, `pip install -r requirements.txt`), configure needed environment values, run `npm run dev` and `pytest`.
- Confirm app window opens, microphone permission can be granted, typed utterance injection works, and existing alert card appears on seeded scenario.
- Record any failing dependencies and avoid modifying architecture until the baseline works.
**Exit criterion:** Existing Chief window and one original alert operate, with baseline tests logged. **Stop/triage** if startup takes more than ~45 min; the 2h plan is then at risk.

### Phase 1 — Product branding + experience cleanup | 15–35 min
- Set a new product name in Electron window titles, package metadata, app UI labels and imagery, only where genuinely needed.
- Update welcome copy for personal learning and technical-conversation usage; retain upstream attribution and MIT license notices in the repo and redistributed copies.
- Preserve the working orb, microphone pipeline, approval behavior and main window; hide phone-only views from the main navigation rather than rewriting them.
- Ensure visible source status: seeded/demo memory versus actual persisted memory; simulated action versus sent action.
**Exit criterion:** Rebranded desktop UI launches, with no regression to listening or approval.

### Phase 2 — User-supplied transcript import | 40–80 min
- Add an “Import transcript” picker for `.txt` and `.md`; accept up to a conservative size limit and show import status and errors.
- Store raw text plus filename, timestamp, optional session title, source ID; chunk into retrievable passages preserving transcript wording, speaker names and timestamps when present.
- Reuse Chief's existing memory/retrieval interfaces where possible. Verify imported material is actually searched—not merely displayed or static-seeded.
- Provide a small transcript list, source preview and delete control. Do not automatically email or publish uploaded content.
- Add one import test and one exact-detail retrieval test with a sample workshop transcript.
**Exit criterion:** A fresh user transcript is imported, persists across restart, and a question can cite the correct excerpt. **Main uncertainty:** connecting new uploads to Chief's Supermemory/Moss flow cleanly.

### Phase 3 — Personal chat tied to the same memory | 60–100 min
- Add a simple Chat tab/panel to the existing main window (do not rebuild the UI).
- Send a user question through Electron/WebSocket to a Python chat handler; call an already-configured Claude model.
- Retrieve from the *same memory* used for live notifications, synthesize grounded answers, include source titles/snippets, and abstain when evidence is absent.
- Chat examples: “What exact example did the workshop give?”, “Compare this with my earlier hackathon lesson”, “Draft a follow-up email using those sources.”
- Reuse the existing email approval action facade instead of creating a parallel sending system.
- Add tests for grounded detail, cross-source connections, unknown-answer abstention, and no auto-send.
**Exit criterion:** Imported and preloaded memory can both be cited in chat; no unapproved external action.

### Phase 4 — Real approved email + verification | 30–90 min
- Configure the repo's existing AgentMail provider for a dedicated controlled test recipient and authorized sender.
- Inspect and preserve `email_agentmail.py` and the UI approval-card flow; ensure a denied send produces **no provider send**.
- Provide exact draft, recipient, subject, sources used and a clear approve/cancel step.
- On approval, perform real send; record provider message ID/status where available; optionally read back recipient inbox for stronger confirmation.
- Show correct status words: draft, approved, submitted, sent/accepted, delivered (only if evidenced), replied (only if evidenced).
- Test success, rejection, duplicate approval and simulated-versus-real provider behavior.
**Exit criterion:** One approved external email action can be independently verified without falsely claiming receipt or response. **Main uncertainty:** provider account creation/credentials can blow the time limit.

### Phase 5 — Self-learning feedback, not model training | 40–75 min
- Add simple feedback on alert/chat: helpful, not helpful, incorrect fact, unnecessary interruption, wrong action.
- Persist the specific event, cited sources, chosen decision, user correction, time and outcome in a local store (SQLite or existing persistence adapter if simple).
- Build a small structured preference/lesson store, e.g. “Only interrupt for material contradictions” with approved/rejected state and provenance.
- Make future decisions retrieve approved user preferences, without changing the model weights or rewriting core prompts after each event.
- Treat repeated feedback as data: candidate learning rules require user review and regression checks; do not silently adapt safety/approval policy.
- Confirm saved feedback persists after app restart and can influence a later **test** decision, while avoiding overclaiming improvement.
**Exit criterion:** The system captures corrections and consumes approved ones in future inference; longer-term gains remain unproven until evaluated over time.

### Phase 6 — LangSmith observability and evaluations | 60–120 min
- Add the LangSmith Python SDK and tracing to existing Python entrypoints (LangChain/LangGraph rewrite not required).
- Trace import and retrieval, chat answer, live decision (`silent`/`whisper`), action selection, approval decision and external provider status; redact transcripts, secrets and personal data unless explicitly authorized.
- Use deterministic event IDs so a UI evaluation record maps to a LangSmith trace.
- Create 5–10 short cases with expected outcomes: relevant past lesson; irrelevant small talk should be silent; contradictory statement should alert; counterparty promise must not send from user's account; user request should draft only until approval; missing-source case should abstain; simulated provider must not be labeled sent.
- Evaluate: retrieval relevance, grounded answers, intervention false positives/negatives, tool selection, approval compliance, provider state accuracy. Use deterministic assertions where possible; LLM-as-judge only as a supplement.
- Record actual passes/failures and inspect incorrect examples; don't manufacture percentages.
- If time allows, expose a small Evaluation tab showing test case, observed decision, expected behavior, pass/fail, error type and LangSmith trace link.
**Exit criterion:** At least one real traced end-to-end run and repeatable scored test set; failed cases are visible, not hidden.

### Phase 7 — End-to-end testing / hardening | 75–150 min
- Regression: run existing `pytest`, relevant JS checks if available, and all new tests.
- Mac usability: mic start/stop and permission, app restart, window overlay behavior, error messages, backend reconnect.
- Transcript: empty file, large file, duplicate import, speaker labels, persistence and deletion.
- Memory/chat: exact quote, cross-session synthesis, wrong/no evidence, citations that open correct source.
- Actions: approve, deny, double-click, provider unavailable, wrong recipient, mock versus live status.
- Eval: traces present in LangSmith, no secrets in telemetry, test cases reproducible, failures labeled correctly.
- Confirm no external action is silently triggered by passive listening or transcript import.
- Maintain a short known-issues file and backup demo input, but do not produce a video.
**Exit criterion:** Complete input→memory→chat/live notice→approval→real action→verification→evaluation path works on this Mac.

## D. Realistic timing and what fits in two hours

| Scope | Approximate total elapsed time | Likely outcome |
|---|---:|---|
| **Checkpoint 1: existing Chief + your branding + preloaded context** | 1–2h | Real desktop listening/alert experience **if** dependencies work. May still use static seeded memory and simulated actions. |
| **Checkpoint 2: useful personalized product** | 3–5h | Transcript import **or** chat working with true persistence, one action path partly integrated. Testing limited. |
| **Checkpoint 3: full target MVP** | **6–10h** | Chat, upload, live companion, approved email, feedback loop, LangSmith tracing, limited eval suite and regression testing. Dependent on provider reliability and coding-agent productivity. |
| **Worst-case due to provider/setup failures** | 10–15h+ | Time can expand; need to cut scope or replace broken integrations. |

**Note:** The phase estimates overlap and are not intended to be summed mechanically; the full-product range assumes experienced AI-assisted coding and no major integration rework. A 2-hour **complete** MVP including all brand-new features is not a credible promise.

## E. Strict priority and scope cuts (first protect the evaluable core)

**Priority P0:** App boots; preloaded memory; chat *or* live context works; one relevant citation; one approved email send with truthful status; minimal evaluated trace; no Docker.
**Priority P1:** User transcript upload and persistent retrieval; full chat plus live shared memory; 5–10-case LangSmith evaluation suite.
**Priority P2:** Feedback history and approved preference retrieval; regression comparison; richer evaluation UI.
**Deferred:** Scheduled reminders, phone calls, calendar/Google integrations, video recordings, system-audio listening, auto self-optimization, `.dmg` packaging, broad redesign.

**If the two-hour deadline is absolute:** Choose either transcript upload **or** chat as the single new feature. Use original Chief listening and notification; instrument one trace, show the source and result; defer the rest. If actual email cannot be connected in time, show a clearly labeled draft/simulation rather than a fabricated send.

## F. Definition of done: honest end-to-end proof

- [ ] Desktop app starts with your product name while keeping upstream attribution/license in the repository.
- [ ] User can add a transcript; its source is preserved and later retrievable.
- [ ] User can ask a chat question and get an evidence-grounded answer with correct excerpt.
- [ ] Existing mic live listener recognizes an important context and appropriately stays silent on irrelevant speech.
- [ ] An approved, external email send succeeds to controlled recipient; denial never sends.
- [ ] Execution states are evidenced, not invented.
- [ ] Feedback/correction survives restart and can be used by a future decision.
- [ ] LangSmith trace shows relevant retrieval, decision, tool call and result without leaking personal data.
- [ ] Reproducible tests include at least one negative and one failure example.
- [ ] `pytest` and the customized product flow run without critical regression.

## G. Project identity, renaming and open-source provenance

- Rename the product, app/window titles, visible labels, example scenarios, package name as feasible, images and documentation. **Avoid indiscriminate renaming of every file/class**: it can break imports and wastes time without improving the user experience.
- Keep the MIT copyright and permission notices wherever required; add a brief acknowledgments section identifying Chief as the open-source foundation.
- Differentiate genuinely through transcript ingestion, sourced personal chat, feedback policy and evaluations. Do not assert that reused infrastructure was independently developed.
- Check hackathon rules separately concerning prebuilt projects and reuse, since an open-source license doesn't determine competition eligibility.

## H. Start-of-coding-session checklist

1. Verify baseline Chief app launch and `pytest` before editing.
2. Capture the complete `.env.example` and identify the **minimum** keys for Haiku, Sonnet, speech, memory, AgentMail and LangSmith; never paste real secrets into chat or commit `.env`.
3. Inspect actual Electron and Python routing so added chat/import modules reuse the current bridge.
4. Use one sample `.txt` with two short, source-rich workshop excerpts and a distinct false-positive example for tests.
5. Implement one vertical slice at a time and run a test after every change.
6. Log all real-versus-simulated states clearly.

**Repository facts checked:** GitHub README of `bliu8/chief` on Nov 18, 2026: https://github.com/bliu8/chief ; key sections Architecture, Actual Features, Ears, Memory, The Decision, The Hands, Getting Started, Tests, License.

## I. Finalized implementation scope

The finalized implementation keeps Chief and the existing desktop shell. Use local SQLite for original transcripts and source excerpts, Moss for semantic recall, Supermemory for selected durable context, and Tavily for public fact checks. Keep sample data clearly identifiable. Verify each provider with synthetic data before wiring it into the production flow.

1. Analyze both imports and completed live sessions. Save grounded commitments, lessons, decisions, factual checks, contradictions, historical links and eligible follow-up drafts.
2. Make saved findings and editable drafts available in the existing transcript preview. Keep sends behind final-content approval and verify provider acceptance separately from delivery.
3. Run and publish a minimum of 12 LangSmith evaluation cases, including objective checks and an evidence-quality judge. Reuse the synthetic dataset for comparisons. Never send real email from evaluation runs.
4. Verify feedback-driven behavior with the same decision before and after an explicitly approved preference. Record the observed change and avoid claims about general improvement.
5. Verify one engineering scenario through the application worker and perform interactive macOS QA. Record all provider and UI limitations.
