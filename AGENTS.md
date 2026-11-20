# Context to Action by Ipsita

Use the active project checkout for development. Read [README.md](README.md) for supported startup and verification commands. [BUILD_PLAN.md](BUILD_PLAN.md) records the original design and phase criteria.

## Architecture

The foundation is [Chief](https://github.com/bliu8/chief). Electron owns native windows and microphone capture. It starts `scripts/live_loop.py` and uses an authenticated loopback WebSocket bridge. The desktop stores transcripts and searchable passages in SQLite. The browser uses the shared React workspace components and Python business logic through `web/server.py`, with a separate SingleStore workspace.

## Development and verification

- Preserve upstream attribution and the existing desktop and browser entry points.
- Keep provider credentials, private audio and transcripts out of commits and traces. Use synthetic fixtures and disposable stores for tests.
- Require visible consent for live capture and explicit approval for external actions. Keep provider acceptance, verification, delivery and simulation distinct.
- Verify exact source citations and cover absent evidence, denied approvals, duplicate approvals and uncertain provider outcomes.
- Run `.venv/bin/python -m pytest -q`. For UI work, verify the affected app from its normal startup command. Record whether each integration check exercised a real provider or a simulated tool.
