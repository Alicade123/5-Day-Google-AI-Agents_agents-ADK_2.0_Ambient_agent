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

"""FastAPI entry point for the ambient expense agent.

Mounts the ADK workflow with Pub/Sub trigger endpoints so expense events
start the graph automatically. Also serves the dev UI for inspecting
sessions (including human-in-the-loop pauses).
"""

from __future__ import annotations

import json
import logging
import os

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Project root so ADK discovers expense_agent/ as an agent package.
AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    web=True,
    otel_to_cloud=False,
    trigger_sources=["pubsub"],
)


@app.middleware("http")
async def normalize_pubsub_subscription(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Normalize ``projects/.../subscriptions/NAME`` to just ``NAME``.

    Pub/Sub push deliveries include the fully-qualified subscription
    resource path. ADK uses this value as the session ``user_id``.
    """
    if request.url.path.endswith("/trigger/pubsub") and request.method == "POST":
        body = await request.body()
        try:
            data = json.loads(body)
            sub = data.get("subscription", "")
            if "/" in sub:
                short_name = sub.rsplit("/", 1)[-1]
                logger.info(
                    "Normalized Pub/Sub subscription: %s -> %s", sub, short_name
                )
                data["subscription"] = short_name
                request._body = json.dumps(data).encode()
        except (json.JSONDecodeError, KeyError):
            logger.warning("Could not normalize Pub/Sub subscription payload")
    return await call_next(request)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting ambient expense agent on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
