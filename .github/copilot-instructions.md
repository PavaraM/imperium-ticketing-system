# Copilot instructions for Imperium Ticketing

FastAPI app for issuing and checking in QR tickets for a school party.
The Dockerfile, compose files and deployment config are project-owner written and must not be created or edited by the agent.

Run: `uv run uvicorn app.asgi:app --reload`
Test: `uv run pytest`
Lint/format: `uv run ruff check . && uv run ruff format --check .`

Follow AGENTS.md for all rules.
