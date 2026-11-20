"""Saved insight and email review records for the desktop workspace."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
import re
from uuid import uuid4

from memory.insights import _setup as setup_analysis


def _setup(db):
    setup_analysis(db)
    db.execute("""CREATE TABLE IF NOT EXISTS workspace_email (
        id TEXT PRIMARY KEY, source_id TEXT, recipient TEXT NOT NULL, subject TEXT NOT NULL,
        body TEXT NOT NULL, state TEXT NOT NULL, approval_id TEXT, message TEXT,
        message_id TEXT, updated_at TEXT NOT NULL
    )""")


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def workspace_snapshot(store):
    with closing(store.connect()) as db, db:
        _setup(db)
        sources = db.execute("""
            SELECT s.id, s.filename, s.imported_at, a.state, a.findings
            FROM sources s LEFT JOIN source_analysis a ON a.source_id = s.id
            ORDER BY s.imported_at DESC
        """).fetchall()
        recipient = os.getenv("CONTEXT_AGENT_TEST_RECIPIENT", "").strip()
        insights, summaries = [], []
        for source in sources:
            findings = json.loads(source["findings"] or "[]")
            summaries.append({
                "id": source["id"], "filename": source["filename"], "imported_at": source["imported_at"],
                "state": source["state"] or "not_started",
                "count": len(findings), "drafts": sum(bool(f.get("draft")) for f in findings),
            })
            for item in findings:
                historical = item.get("historical")
                if historical and not db.execute("SELECT 1 FROM sources WHERE id = ?", (historical["source_id"],)).fetchone():
                    item.pop("historical", None)
                insights.append({**item, "filename": source["filename"], "imported_at": source["imported_at"]})
                draft = item.get("draft")
                if draft:
                    db.execute("""
                        INSERT OR IGNORE INTO workspace_email
                        VALUES (?, ?, ?, ?, ?, 'draft', NULL, NULL, NULL, ?)
                    """, (item["id"], source["id"], recipient, draft["subject"], draft["body"], source["imported_at"]))
        actions = db.execute("""
            SELECT a.id, a.source_id, a.recipient, a.subject, a.body, a.state, a.message, a.message_id,
                   a.updated_at, s.filename
            FROM workspace_email a LEFT JOIN sources s ON a.source_id = s.id
            WHERE a.source_id IS NULL OR s.id IS NOT NULL OR a.state IN ('sending','accepted','verified','unconfirmed','rejected','unavailable')
            ORDER BY a.updated_at DESC
        """).fetchall()
    return {"sources": summaries, "insights": insights, "actions": [dict(row) for row in actions],
            "controlled_recipient": recipient}


def _draft_content(subject, body):
    subject, body = subject.strip(), body.strip()
    if not subject or len(subject) > 200 or "\n" in subject or "\r" in subject or not body or len(body) > 8000:
        raise ValueError("Enter a single-line subject and a message.")
    return subject, body


def save_email_draft(store, source_id, subject, body):
    subject, body = _draft_content(subject, body)
    action_id = uuid4().hex
    with closing(store.connect()) as db, db:
        _setup(db)
        db.execute("""INSERT INTO workspace_email
            VALUES (?, ?, '', ?, ?, 'draft', NULL, NULL, NULL, ?)""", (action_id, source_id, subject, body, _stamp()))
    return {"id": action_id, "review": "Draft saved in Actions."}


def review_email(store, action_id, recipient, subject, body):
    recipient = recipient.strip()
    if not re.fullmatch(r"[^\s@,<>]+@[^\s@,<>]+\.[^\s@,<>]+", recipient):
        raise ValueError("Enter one valid email address.")
    subject, body = _draft_content(subject, body)
    token = uuid4().hex
    with closing(store.connect()) as db, db:
        _setup(db)
        result = db.execute("""
            UPDATE workspace_email SET recipient=?, subject=?, body=?, state='awaiting_approval',
                approval_id=?, message=NULL, message_id=NULL, updated_at=?
            WHERE id=? AND state IN ('draft','awaiting_approval','rejected','unavailable','preview')
              AND (source_id IS NULL OR EXISTS (SELECT 1 FROM sources WHERE id=workspace_email.source_id))
        """, (recipient, subject, body, token, _stamp(), action_id))
        if not result.rowcount:
            raise ValueError("This draft can no longer be edited.")
    return {"token": token, "recipient": recipient, "subject": subject, "body": body}


def cancel_review(store, action_id, token):
    with closing(store.connect()) as db, db:
        _setup(db)
        db.execute("""
            UPDATE workspace_email SET state='draft', approval_id=NULL, updated_at=?
            WHERE id=? AND approval_id=? AND state='awaiting_approval'
        """, (_stamp(), action_id, token))


def approve_email(store, action_id, token):
    from action.email_agentmail import send_verified
    from evaluation.telemetry import record

    draft = begin_send(store, action_id, token)
    record("approval", event_id=token, outcome="approved")
    try:
        outcome = send_verified(draft["recipient"], draft["subject"], draft["body"], token)
    except Exception:
        outcome = {"state": "unconfirmed", "message": "Send outcome unknown. Check the provider before retrying."}
    record("execution", event_id=token, state=outcome["state"])
    finish_send(store, action_id, token, outcome)
    return outcome


def begin_send(store, action_id, token):
    with closing(store.connect()) as db, db:
        _setup(db)
        changed = db.execute("""
            UPDATE workspace_email SET state='sending', updated_at=?
            WHERE id=? AND approval_id=? AND state='awaiting_approval'
              AND (source_id IS NULL OR EXISTS (SELECT 1 FROM sources WHERE id=workspace_email.source_id))
        """, (_stamp(), action_id, token))
        if not changed.rowcount:
            raise ValueError("This approval is no longer valid.")
        return dict(db.execute("SELECT recipient, subject, body FROM workspace_email WHERE id=?", (action_id,)).fetchone())


def finish_send(store, action_id, token, outcome):
    with closing(store.connect()) as db, db:
        db.execute("""
            UPDATE workspace_email SET state=?, message=?, message_id=?, updated_at=?
            WHERE id=? AND approval_id=?
        """, (outcome["state"], outcome.get("message"), outcome.get("message_id"), _stamp(), action_id, token))


def track_orb_draft(store, token, inputs):
    from uuid import uuid5, NAMESPACE_URL

    action_id = str(uuid5(NAMESPACE_URL, "context-to-action:orb:" + token))
    recipient = inputs.get("to") or ""
    if isinstance(recipient, list):
        recipient = ", ".join(recipient)
    with closing(store.connect()) as db, db:
        _setup(db)
        db.execute("""
            INSERT OR IGNORE INTO workspace_email
            VALUES (?, NULL, ?, ?, ?, 'awaiting_approval', ?, NULL, NULL, ?)
        """, (action_id, recipient, inputs.get("subject") or "", inputs.get("body") or "", token, _stamp()))
    return action_id


def fact_check(store, source_id, insight_id):
    from action.agent import research_claim

    if not os.getenv("TAVILY_API_KEY"):
        raise ValueError("Tavily is not configured.")
    with closing(store.connect()) as db:
        _setup(db)
        row = db.execute("SELECT findings FROM source_analysis WHERE source_id=?", (source_id,)).fetchone()
    insight = next((item for item in json.loads(row["findings"]) if item["id"] == insight_id), None) if row else None
    if not insight or insight["kind"] != "claim":
        raise ValueError("Select a saved claim to fact-check.")
    outcome = research_claim(insight["quote"])
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT findings FROM source_analysis WHERE source_id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("The source was removed before the check finished.")
        findings = json.loads(row["findings"])
        for item in findings:
            if item["id"] == insight_id:
                item["research"] = outcome
                break
        else:
            raise ValueError("The claim is no longer available.")
        db.execute("UPDATE source_analysis SET findings=? WHERE source_id=?", (json.dumps(findings), source_id))
    return outcome


def recover_sending():
    from memory.transcripts import local_store

    store = local_store()
    if not store.path.exists():
        return
    with closing(store.connect()) as db, db:
        _setup(db)
        db.execute("""
            UPDATE workspace_email SET state='unconfirmed', message='The app stopped before verifying this send.',
                updated_at=? WHERE state='sending'
        """, (_stamp(),))
