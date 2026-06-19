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

"""Local LLM-as-judge grading when Vertex eval project is unavailable."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from google import genai
from google.genai import types

from expense_agent.config import config as agent_config  # noqa: F401 — loads .env

ROOT = Path(__file__).resolve().parents[2]
TRACES_PATH = ROOT / "artifacts" / "traces" / "generated_traces.json"
CONFIG_PATH = ROOT / "tests" / "eval" / "eval_config.yaml"
RESULTS_DIR = ROOT / "artifacts" / "grade_results"


def _extract_text(content: dict | None) -> str:
    if not content:
        return ""
    parts = content.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    return "\n".join(texts)


def _build_instance(case: dict) -> dict:
    prompt = case.get("prompt") or {}
    agent_data = case.get("agent_data") or {}
    responses = case.get("responses") or []
    response = responses[0]["response"] if responses else {}
    return {
        "prompt": json.dumps(prompt),
        "response": json.dumps(response),
        "agent_data": json.dumps(agent_data),
    }


def _run_judge(client: genai.Client, template: str, instance: dict) -> dict:
    prompt = template
    for key, value in instance.items():
        prompt = prompt.replace("{" + key + "}", value)
    result = client.models.generate_content(
        model=agent_config.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    text = result.text or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"score": 1, "explanation": f"Judge returned non-JSON: {text[:200]}"}


def main() -> None:
    traces = json.loads(TRACES_PATH.read_text(encoding="utf-8"))
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    metrics_to_run = cfg["metrics_to_run"]
    metrics_by_name = {m["name"]: m for m in cfg["custom_metrics"]}

    client = genai.Client()
    results: list[dict] = []

    print("\n=== Evaluation Scorecard ===\n")
    for case in traces["eval_cases"]:
        case_id = case["eval_case_id"]
        instance = _build_instance(case)
        case_scores: dict[str, dict] = {}
        for metric_name in metrics_to_run:
            template = metrics_by_name[metric_name]["prompt_template"]
            verdict = _run_judge(client, template, instance)
            case_scores[metric_name] = verdict
        results.append({"eval_case_id": case_id, "scores": case_scores})

        print(f"Case: {case_id}")
        for metric_name, verdict in case_scores.items():
            print(
                f"  {metric_name}: {verdict.get('score')} — "
                f"{verdict.get('explanation', '')[:120]}"
            )
        print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "local_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
