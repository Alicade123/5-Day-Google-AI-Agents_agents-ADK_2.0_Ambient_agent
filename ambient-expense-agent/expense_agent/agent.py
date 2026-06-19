# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ambient expense-approval workflow built with ADK 2.0 graph workflows."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from google.adk import Agent, Context, Event, Workflow
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import RequestInput
from google.genai import types
from pydantic import BaseModel, Field

from .config import config

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ExpenseData(BaseModel):
    """Expense report data extracted from an incoming event."""

    amount: float = Field(description="Expense amount in USD")
    submitter: str = Field(description="Email of the person who submitted")
    category: str = Field(description="Expense category, e.g. travel, meals")
    description: str = Field(description="What the expense is for")
    date: str = Field(description="Date of the expense (YYYY-MM-DD)")


class ApprovalDecision(BaseModel):
    """Human manager decision for a high-value expense."""

    decision: str = Field(description="Either 'approve' or 'reject'")


# ---------------------------------------------------------------------------
# Security patterns
# ---------------------------------------------------------------------------

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"auto[- ]?approve",
        r"bypass\s+(all\s+)?(rules|checks|security|validation)",
        r"do\s+not\s+(flag|review|check|escalate)",
        r"you\s+must\s+(approve|accept|allow)",
        r"system\s+prompt",
        r"disregard\s+(all\s+)?(rules|policies|instructions)",
    ]
]


# ---------------------------------------------------------------------------
# Function nodes
# ---------------------------------------------------------------------------


def _extract_text(node_input: Any) -> str:
    if isinstance(node_input, types.Content):
        if node_input.parts:
            return node_input.parts[0].text or ""
        return ""
    if isinstance(node_input, str):
        return node_input
    return str(node_input)


def parse_expense_email(node_input: Any) -> Event:
    """Parse a Pub/Sub trigger event or a plain local-test payload."""
    text = _extract_text(node_input)
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return Event(output={"error": f"Invalid JSON: {text[:200]}"})

    if "amount" in event:
        data = event
    else:
        data = event.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(base64.b64decode(data))
            except Exception:
                return Event(output={"error": f"Failed to decode data: {data[:200]}"})

    return Event(
        output={
            "amount": float(data.get("amount", 0)),
            "submitter": data.get("submitter", "unknown"),
            "category": data.get("category", "other"),
            "description": data.get("description", ""),
            "date": data.get("date", ""),
        }
    )


def route_by_amount(node_input: dict, ctx: Context) -> Event:
    """Route on the configured dollar threshold — deterministic, no LLM."""
    ctx.state["expense_data"] = node_input
    amount = node_input.get("amount", 0)
    if amount >= config.review_threshold:
        return Event(route="NEEDS_REVIEW", output=node_input)
    return Event(route="AUTO_APPROVE", output=node_input)


def auto_approve(node_input: dict) -> Event:
    """Instantly approve low-value expenses in pure Python."""
    log_entry = {
        "severity": "INFO",
        "message": (
            f"Expense auto-approved: ${node_input['amount']:.2f}"
            f" from {node_input['submitter']}"
        ),
        "decision": "approved",
        "amount": node_input["amount"],
        "submitter": node_input["submitter"],
        "category": node_input["category"],
    }
    print(json.dumps(log_entry), flush=True)
    return Event(
        output={"status": "approved", **node_input},
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        f"Auto-approved ${node_input['amount']:.2f} expense from "
                        f"{node_input['submitter']} ({node_input['category']})."
                    )
                )
            ],
        ),
    )


def security_screen(node_input: dict, ctx: Context) -> Event:
    """Redact PII and block prompt-injection before the LLM sees the expense."""
    expense = dict(node_input)
    original_description = expense.get("description", "")
    redacted_categories: list[str] = []

    if SSN_PATTERN.search(original_description):
        expense["description"] = SSN_PATTERN.sub("[REDACTED-SSN]", original_description)
        redacted_categories.append("SSN")
    if CREDIT_CARD_PATTERN.search(expense["description"]):
        expense["description"] = CREDIT_CARD_PATTERN.sub(
            "[REDACTED-CC]", expense["description"]
        )
        redacted_categories.append("CREDIT_CARD")

    injection_detected = any(
        pattern.search(original_description) for pattern in INJECTION_PATTERNS
    )

    state_delta = {
        "expense_data": expense,
        "redacted_categories": redacted_categories,
    }

    if injection_detected:
        state_delta["security_event"] = True
        log_entry = {
            "severity": "WARNING",
            "message": "Security event: prompt injection detected in expense description",
            "alert_type": "prompt_injection",
            "submitter": expense.get("submitter"),
            "amount": expense.get("amount"),
        }
        print(json.dumps(log_entry), flush=True)
        return Event(route="INJECTION", output=expense, state=state_delta)

    return Event(route="CLEAN", output=expense, state=state_delta)


def emit_expense_alert(
    submitter: str,
    amount: float,
    category: str,
    risk_summary: str,
) -> dict:
    """Emit a structured log alerting finance to review a high-value expense."""
    log_entry = {
        "severity": "WARNING",
        "message": (
            f"Expense review alert: ${amount:.2f} from {submitter} — {risk_summary}"
        ),
        "alert_type": "expense_review",
        "submitter": submitter,
        "amount": amount,
        "category": category,
        "risk_summary": risk_summary,
    }
    print(json.dumps(log_entry), flush=True)
    return {"status": "alert_emitted", "submitter": submitter, "amount": amount}


review_agent = Agent(
    name="review_agent",
    model=config.model,
    mode="single_turn",
    instruction="""You are an expense review agent. You receive expense reports
of $100 or more that passed the security screen and need risk analysis.

Analyze the expense and:
1. Check for risk factors: unusual category for the amount, vague description,
   suspiciously round numbers, very high value (>$1000), or potential policy
   violations.
2. Call the `emit_expense_alert` tool with the submitter, amount, category,
   and a brief risk summary explaining why this expense needs human review.
3. Return a structured review.

Your review MUST include:
- **Amount**: The expense amount
- **Submitter**: Who submitted it
- **Category**: The expense category
- **Risk level**: low, medium, or high
- **Risk factors**: What flags you found (if any)
- **Recommendation**: approve, request-more-info, or escalate""",
    input_schema=ExpenseData,
    tools=[emit_expense_alert],
)


def request_approval(node_input: Any, ctx: Context):
    """Pause the workflow for human approval via RequestInput (HITL)."""
    expense = ctx.state.get("expense_data", {})
    redacted = ctx.state.get("redacted_categories", [])
    security_event = ctx.state.get("security_event", False)

    message = "Expense requires manager approval. Approve or reject."
    if security_event:
        message = (
            "SECURITY ALERT: Prompt injection detected. "
            "The LLM review was skipped. Approve or reject."
        )
    elif redacted:
        message += f" Redacted from description: {', '.join(redacted)}."

    payload = {
        **expense,
        "security_event": security_event,
        "redacted_categories": redacted,
    }
    if security_event:
        payload["risk_summary"] = "Prompt injection detected — bypassed LLM review"

    yield RequestInput(
        interrupt_id="expense_approval",
        message=message,
        payload=payload,
        response_schema=ApprovalDecision,
    )


def process_decision(node_input: Any, ctx: Context) -> Event:
    """Record the human decision and emit the final outcome."""
    decision = "unknown"
    if isinstance(node_input, dict):
        decision = node_input.get("decision", "unknown")
    elif isinstance(node_input, str):
        decision = "approve" if "approve" in node_input.lower() else "reject"

    approved = decision == "approve"
    expense = ctx.state.get("expense_data", {})
    security_event = ctx.state.get("security_event", False)
    status = "approved" if approved else "rejected"

    log_entry = {
        "severity": "INFO" if approved else "WARNING",
        "message": f"Expense {status} by manager",
        "decision": status,
        "security_event": security_event,
    }
    print(json.dumps(log_entry), flush=True)

    submitter = expense.get("submitter", "unknown")
    amount = expense.get("amount", 0)
    category = expense.get("category", "")
    description = expense.get("description", "")
    date = expense.get("date", "")

    parts = [f"${amount:.2f} expense from {submitter} has been {status}."]
    if security_event:
        parts.append("Flagged as a security event (prompt injection).")
    if description:
        parts.append(f'"{description}" ({category}) on {date}.')
    if approved:
        parts.append(
            "The expense has been logged and will be processed for reimbursement."
        )
    else:
        parts.append(
            "The submitter will be notified and may resubmit with additional documentation."
        )

    final_message = " ".join(parts)
    return Event(
        output={"status": status, "message": final_message, "security_event": security_event},
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=final_message)],
        ),
    )


# ---------------------------------------------------------------------------
# Graph workflow
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="expense_processor",
    edges=[
        ("START", parse_expense_email, route_by_amount),
        (
            route_by_amount,
            {
                "AUTO_APPROVE": auto_approve,
                "NEEDS_REVIEW": security_screen,
            },
        ),
        (
            security_screen,
            {
                "CLEAN": review_agent,
                "INJECTION": request_approval,
            },
        ),
        (review_agent, request_approval),
        (request_approval, process_decision),
    ],
)

app = App(
    name="expense_agent",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
