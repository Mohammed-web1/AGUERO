"""Fake mcp-server-rust, for local testing of mcp-client-python only.

mcp-server-rust has no implementation yet. This stub exposes the same three
tools its README documents (fetch_unread_emails / apply_label / move_email)
over Streamable HTTP, backed by fake in-memory emails, so the real client can
be run and its full fetch -> analyze -> act loop verified end to end.
"""

from __future__ import annotations

from pydantic import BaseModel

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("mock-mcp-server-rust")

_EMAILS: dict[str, dict] = {
    "1": {
        "id": "1",
        "sender": "security@paypa1-verify.com",
        "subject": "Urgent: Verify your account now",
        "snippet": "Your account will be suspended unless you verify within 24 hours...",
        "folder": "Inbox",
        "labels": [],
    },
    "2": {
        "id": "2",
        "sender": "deals@shopmart.example",
        "subject": "50% off everything -- today only!",
        "snippet": "Our biggest sale of the year is here...",
        "folder": "Inbox",
        "labels": [],
    },
    "3": {
        "id": "3",
        "sender": "jane@colleague.example",
        "subject": "Notes from today's project sync",
        "snippet": "Sharing the action items we agreed on...",
        "folder": "Inbox",
        "labels": [],
    },
}


class Email(BaseModel):
    id: str
    sender: str
    subject: str
    snippet: str


@mcp.tool()
def fetch_unread_emails() -> list[Email]:
    """Return unread emails currently sitting in the Inbox."""
    return [
        Email(id=e["id"], sender=e["sender"], subject=e["subject"], snippet=e["snippet"])
        for e in _EMAILS.values()
        if e["folder"] == "Inbox"
    ]


@mcp.tool()
def apply_label(email_id: str, label: str) -> dict:
    """Tag an email with a label (e.g. 'Critical', 'Phishing')."""
    email = _EMAILS.get(email_id)
    if email is None:
        return {"success": False, "error": f"unknown email_id {email_id}"}
    email["labels"].append(label)
    print(f"[MOCK ACTION] apply_label(email_id={email_id!r}, label={label!r}) -> {email['subject']!r}")
    return {"success": True, "email_id": email_id, "label": label}


@mcp.tool()
def move_email(email_id: str, folder: str) -> dict:
    """Move an email to a specific folder (e.g. 'Quarantine')."""
    email = _EMAILS.get(email_id)
    if email is None:
        return {"success": False, "error": f"unknown email_id {email_id}"}
    email["folder"] = folder
    print(f"[MOCK ACTION] move_email(email_id={email_id!r}, folder={folder!r}) -> {email['subject']!r}")
    return {"success": True, "email_id": email_id, "folder": folder}


if __name__ == "__main__":
    print("Mock MCP server (fake mcp-server-rust) on http://127.0.0.1:8080/mcp")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
