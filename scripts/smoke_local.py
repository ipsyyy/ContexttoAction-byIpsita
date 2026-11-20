"""Exercise the local worker over WebSocket with synthetic inputs and an isolated store."""

import asyncio
import json
import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from websockets.asyncio.server import serve

async def main():
    transcript = Path("tests/fixtures/workshop.txt").read_text(encoding="utf-8")
    with TemporaryDirectory() as data_dir:
        for operations in (
            [("import", {"filename": "workshop.txt", "text": transcript})],
            [("list", {}), ("chat", {"question": "What did Ada say about the purple invoice router?"})],
        ):
            async def check(ws):
                try:
                    for operation, fields in operations:
                        await ws.send(json.dumps({"type": "transcripts", "id": operation, "operation": operation, **fields}))
                        async for raw in ws:
                            event = json.loads(raw)
                            if event.get("type") == "transcript_result" and event["id"] == operation:
                                assert event["ok"], event
                                response = event["result"]
                                if operation == "list":
                                    assert response[0]["filename"] == "workshop.txt", response
                                if operation == "chat":
                                    assert response["mode"] == "local", response
                                    assert "Ada: The workshop example was a purple invoice router." in response["sources"][0]["excerpt"], response
                                print(f"PASS {operation}", flush=True)
                                break
                    done.set_result(True)
                except Exception as error:
                    if not done.done():
                        done.set_exception(error)

            done = asyncio.get_running_loop().create_future()
            async with serve(check, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                script = (
                    "import sys, runpy; sys.path.insert(0, 'backend'); import bridge; "
                    f"bridge.WS_URL = 'ws://127.0.0.1:{port}'; "
                    "runpy.run_path('scripts/live_loop.py', run_name='__main__')"
                )
                worker = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", "-c", script,
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": data_dir,
                        "PYTHON_DOTENV_DISABLED": "1",
                        "CONTEXT_AGENT_DATA_DIR": data_dir,
                    },
                )
                try:
                    await asyncio.wait_for(done, 10)
                finally:
                    worker.terminate()
                    await worker.wait()

asyncio.run(main())
