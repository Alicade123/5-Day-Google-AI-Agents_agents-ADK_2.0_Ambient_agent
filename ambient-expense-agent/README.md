# ambient-expense-agent

ADK 2.0 ambient expense-approval agent. Low-value expenses auto-approve in code; high-value ones go through security screening, LLM review, and human approval.

## Setup

1. Install dependencies:
   ```bash
   make install
   ```
2. Add your API key to `app/.env`:
   ```
   GOOGLE_API_KEY=your-key-here
   ```

## Run the agent

Start the local server (Pub/Sub triggers + dev UI on port 8080):

```bash
make playground
```

Or:

```bash
uv run python expense_agent/fast_api_app.py
```

Open the UI: **http://localhost:8080/dev-ui/?app=expense_agent**

## Test the agent

### 1. Pub/Sub trigger — auto-approve (under $100)

**PowerShell:**
```powershell
$expense = '{"amount":45,"submitter":"bob@company.com","category":"meals","description":"Team lunch","date":"2026-04-12"}'
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($expense))
$body = (@{ message = @{ data = $b64 }; subscription = "test-sub" } | ConvertTo-Json -Compress)
Invoke-RestMethod -Uri "http://localhost:8080/apps/expense_agent/trigger/pubsub" -Method Post -ContentType "application/json" -Body $body
```

**Bash:**
```bash
curl -s http://localhost:8080/apps/expense_agent/trigger/pubsub \
  -H "Content-Type: application/json" \
  -d "{\"message\":{\"data\":\"$(printf '%s' '{"amount":45,"submitter":"bob@company.com","category":"meals","description":"Team lunch","date":"2026-04-12"}' | base64)\"},\"subscription\":\"test-sub\"}"
```

View the session: **http://localhost:8080/dev-ui/?app=expense_agent&userId=test-sub**

### 2. Pub/Sub trigger — high-value (needs human approval)

Send a payload with `"amount": 150` (or more). The workflow runs LLM review, then pauses for approval in the dev UI.

### 3. Playground test payload

With the server running:

```bash
uv run python scripts/submit_test_expense.py
```

Then approve or reject in the dev UI.

## Evaluate

```bash
make generate-traces
make grade
```

## Key files

```
expense_agent/
├── agent.py          # ADK 2.0 workflow graph
├── config.py         # $100 threshold, model name
└── fast_api_app.py   # Ambient Pub/Sub entry point
```

## Makefile commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies |
| `make playground` | Run ambient server on :8080 |
| `make generate-traces` | Run eval scenarios |
| `make grade` | Score traces with LLM judges |
