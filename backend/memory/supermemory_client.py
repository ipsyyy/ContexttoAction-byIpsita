"""Translate Chief's document operations to the installed Supermemory SDK."""

from types import SimpleNamespace
from hashlib import sha256
import os


def tag_field(tag):
    return "tag_" + sha256(tag.encode()).hexdigest()[:24]


class NamespacedDocuments:
    def __init__(self, client):
        self.client = client
        self.namespace = os.getenv("CONTEXT_SUPERMEMORY_NAMESPACE", "context-to-action")
        self.search = self

    def add(self, *, content, container_tags, custom_id=None, metadata=None, **options):
        document_id = sha256((custom_id or content).encode()).hexdigest()
        fields = {**(metadata or {}), "chief_document_id": document_id}
        # Preserve Chief's JSON even while Supermemory extracts source content.
        if len(content.encode("utf-8")) <= 3000:
            fields["chief_payload"] = content
        fields.update({tag_field(tag): True for tag in container_tags})
        return self.client.add(self.namespace, content=content, id=document_id, metadata=fields, **options)

    def documents(self, *, q, container_tags, limit=20, include_full_docs=True, **options):
        filters = [{"field": tag_field(tag), "operator": "eq", "value": True} for tag in container_tags]
        request = {"limit": limit, "include": {"content": True}, **options}
        if filters:
            request["filter"] = filters[0] if len(filters) == 1 else {"operator": "and", "operands": filters}
        page = self.client.list(self.namespace, "documents", **request)
        return SimpleNamespace(results=[
            {**document.model_dump(), "content": document.metadata.get("chief_payload") or document.content}
            for document in page.documents
        ])



def compatible_client(client):
    if callable(getattr(type(client), "search", None)):
        return NamespacedDocuments(client)
    return client
