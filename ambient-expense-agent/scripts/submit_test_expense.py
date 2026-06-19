"""Submit the lab test expense payload to a running ADK playground."""

import json
import urllib.request

BASE = "http://127.0.0.1:8080"
APP = "expense_agent"
USER = "test-user"
SESSION_ID = "lab-test-session-3"

expense = {
    "amount": 150.0,
    "submitter": "alice@company.com",
    "category": "software",
    "description": "IDE License",
    "date": "2026-06-06",
}


def post(path: str, body: dict | None = None) -> str:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        method="POST",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def post_stream(path: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        method="POST",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode().strip()
            if line:
                print(line)


if __name__ == "__main__":
    print("Creating session...")
    post(f"/apps/{APP}/users/{USER}/sessions/{SESSION_ID}")
    print("Submitting expense payload...")
    payload = {
        "appName": APP,
        "userId": USER,
        "sessionId": SESSION_ID,
        "newMessage": {
            "role": "user",
            "parts": [{"text": json.dumps(expense)}],
        },
        "streaming": False,
    }
    post_stream("/run_sse", payload)
