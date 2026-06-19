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

"""Run eval scenarios through the expense workflow and serialize traces."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from google.adk.runners import InMemoryRunner
from google.genai import types

from expense_agent.agent import app, root_agent

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "tests" / "eval" / "datasets" / "basic-dataset.json"
OUTPUT_PATH = ROOT / "artifacts" / "traces" / "generated_traces.json"

REQUEST_INPUT_NAME = "adk_request_input"


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert an ADK Event to a Vertex-eval-compatible AgentEvent."""
    if event.content is not None:
        return {
            "author": event.author,
            "content": event.content.model_dump(exclude_none=True, mode="json"),
        }

    parts: list[dict[str, Any]] = []
    if event.output is not None:
        parts.append({"text": json.dumps({"output": event.output})})
    if event.actions.state_delta:
        parts.append({"text": json.dumps({"state_delta": event.actions.state_delta})})
    if event.actions.route is not None:
        parts.append({"text": json.dumps({"route": event.actions.route})})

    node_info = getattr(event, "node_info", None)
    if node_info is not None:
        if hasattr(node_info, "model_dump"):
            parts.append({"text": json.dumps({"nodeInfo": node_info.model_dump(exclude_none=True, mode="json")})})
        else:
            parts.append({"text": json.dumps({"nodeInfo": str(node_info)})})

    if not parts:
        parts.append({"text": ""})

    return {"author": event.author, "content": {"role": "model", "parts": parts}}


def _final_response_text(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        content = event.get("content") or {}
        parts = content.get("parts") or []
        texts = [p["text"] for p in parts if p.get("text")]
        if texts:
            return "".join(texts)
        output = event.get("output")
        if isinstance(output, dict) and output.get("message"):
            return str(output["message"])
    return ""


async def _run_case(prompt_text: str, hitl_decision: str | None) -> list[dict[str, Any]]:
    runner = InMemoryRunner(app=app)
    user_id = "eval-user"
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=user_id,
    )

    trace_events: list[dict[str, Any]] = [
        {
            "author": "user",
            "content": {"role": "user", "parts": [{"text": prompt_text}]},
        }
    ]

    current_message: types.Content | None = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt_text)],
    )

    while current_message is not None:
        hitl_call_id: str | None = None
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=current_message,
        ):
            trace_events.append(_event_to_dict(event))
            if event.content and event.content.parts:
                for part in event.content.parts:
                    fc = part.function_call
                    if fc and fc.name == REQUEST_INPUT_NAME:
                        hitl_call_id = fc.id

        if hitl_call_id and hitl_decision:
            logger.info("Resuming HITL with decision=%s", hitl_decision)
            current_message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=REQUEST_INPUT_NAME,
                            response={"decision": hitl_decision},
                            id=hitl_call_id,
                        )
                    )
                ],
            )
            continue
        break

    return trace_events


def _build_eval_case(case: dict[str, Any], trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    final_text = _final_response_text(trace_events)
    merged = {
        "eval_case_id": case["eval_case_id"],
        "prompt": case["prompt"],
        "agent_data": {
            "agents": {
                root_agent.name: {
                    "agent_id": root_agent.name,
                    "agent_type": "Workflow",
                    "instruction": "Ambient expense approval workflow",
                }
            },
            "turns": [
                {
                    "turn_index": 0,
                    "events": trace_events,
                }
            ],
        },
    }
    if final_text:
        merged["responses"] = [
            {"response": {"role": "model", "parts": [{"text": final_text}]}}
        ]
    return merged


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    merged_cases: list[dict[str, Any]] = []

    for case in dataset["eval_cases"]:
        case_id = case["eval_case_id"]
        prompt_text = case["prompt"]["parts"][0]["text"]
        hitl_decision = case.get("hitl_decision")
        logger.info("Running case %s (hitl=%s)", case_id, hitl_decision)
        trace_events = await _run_case(prompt_text, hitl_decision)
        merged_cases.append(_build_eval_case(case, trace_events))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps({"eval_cases": merged_cases}, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d cases)", OUTPUT_PATH, len(merged_cases))


if __name__ == "__main__":
    asyncio.run(main())
