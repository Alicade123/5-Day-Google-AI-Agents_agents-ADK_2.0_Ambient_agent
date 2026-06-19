# Local development targets for the ambient expense agent (ADK 2.0)

install:
	uv sync

playground:
	uv run python expense_agent/fast_api_app.py

test:
	uv run python -m pytest tests/ -xvs

lint:
	uv run ruff check . --fix && uv run ruff format .

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	uv sync --extra eval
	-agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml
	uv run python tests/eval/local_grade.py
