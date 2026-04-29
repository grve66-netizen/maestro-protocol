# Maestro Protocol

A stateless, verification-driven multi-agent orchestration pattern.

Maestro Protocol is designed around a simple idea: agents should not be trusted just because they say they completed a task. They return structured receipts, the Maestro writes files through a controlled data plane, and completion is accepted only after verification.

## What V2 Adds

This version adds the next layer for practical token control and extensibility:

- selective clause loading per agent
- compact context packs instead of full chat history
- workspace file manifests with hashes, sizes, and previews
- safe file-write data plane
- registry-based agent selection
- task audit logs
- configurable routing and agent models
- CLI commands for init, run, status, and manifest

## Architecture

```text
/config/
├── the_protocol.md
└── clauses/
    ├── karpathy_coding_clause.md
    ├── security_clause.md
    └── docs_clause.md

/agents/
└── agent_registry.json

/core/
├── maestro.py
└── cli.py

/vault/<project>/
├── 00_PROJECT_BRIEF.md
├── 01_CURRENT_STATE.md
├── 02_DECISIONS_LOG.md
├── 03_LESSONS_LOG.md
├── 04_WORKSPACE/
└── 05_TASKS/
```

## Token Strategy

Maestro avoids growing chat history. Each loop sends only:

1. core protocol
2. selected agent clauses
3. project brief
4. current state
5. current task
6. compact manifest for relevant files

The agent registry controls context through:

```json
{
  "context_paths": ["04_WORKSPACE/frontend/", "04_WORKSPACE/docs/"],
  "required_clauses": ["karpathy_coding_clause"],
  "max_context_files": 8
}
```

This keeps prompts smaller and prevents every agent from receiving every rule and every file.

## Agent Registry

Agents are defined in `/agents/agent_registry.json`.

Important fields:

- `name`: exact agent identifier
- `description`: role summary used by the router
- `intents`: task keywords and capabilities
- `allowed_paths`: where the agent may write
- `context_paths`: which files are summarized in its context pack
- `required_clauses`: clauses loaded into the agent prompt
- `max_context_files`: cap for manifest size

## Safe File Writes

Agents return file writes in their structured receipt:

```json
{
  "status": "COMPLETE",
  "summary_of_work": "Created a landing page.",
  "file_writes": [
    {
      "path": "04_WORKSPACE/frontend/index.html",
      "content": "<html>...</html>"
    }
  ],
  "files_updated": ["04_WORKSPACE/frontend/index.html"],
  "next_step_suggestion": "Project complete"
}
```

The Maestro validates paths before writing. Agents cannot write outside their allowed paths or escape the project directory.

## Verification

Before accepting a COMPLETE receipt, Maestro verifies:

- files were actually written
- paths are allowed
- files exist
- files are non-empty

Only verified work updates `01_CURRENT_STATE.md` and `02_DECISIONS_LOG.md`.

Failures are logged in `03_LESSONS_LOG.md`.

This is presence-only verification — it confirms that an agent did the writes it claimed to, not that the resulting code is correct. Semantic verification (a dedicated review agent, automated tests) is intentionally out of scope for this layer; see "Recommended Next Additions".

## Project Completion

The orchestration loop ends only when an agent returns a receipt with `is_project_complete: true`. This is a structured boolean on the receipt, not a heuristic over `next_step_suggestion`. Agents are instructed to set it only when the full project brief is satisfied, so intermediate tasks don't accidentally end the run.

## Requirements

- Python 3.10 or newer
- An OpenAI API key (set as `OPENAI_API_KEY`) for the `run` command. Other commands (`init`, `status`, `manifest`) do not call the API.

## CLI

Install dependencies:

```bash
pip install -r requirements.txt
```

Set your API key:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

Create a project:

```bash
python -m core.cli init my_project --template landing_page
```

Run the orchestrator:

```bash
python -m core.cli run my_project "Build a landing page"
```

Use separate routing and agent models:

```bash
python -m core.cli run my_project "Build a landing page" \
  --routing-model gpt-4o-mini \
  --agent-model gpt-4o
```

Show project state:

```bash
python -m core.cli status my_project
```

Show workspace manifest:

```bash
python -m core.cli manifest my_project
```

## Tests

Run the test suite with:

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover path traversal, allowed-path enforcement, manifest building, protocol compilation, and verification gating. They do not call OpenAI, so no API key is needed to run them.

## Environment Defaults

You can set default models with:

```bash
export MAESTRO_ROUTING_MODEL="gpt-4o-mini"
export MAESTRO_AGENT_MODEL="gpt-4o"
```

CLI arguments override environment variables.

## Current Limitations

This is still intentionally lean.

Not included yet:

- semantic verification against full requirements
- automatic test execution
- full-file retrieval on demand
- parallel agents
- persistent vector memory
- web UI

## Recommended Next Additions

1. Add test execution for code-producing agents.
2. Add semantic verification by a dedicated review agent.
3. Add targeted full-file loading when manifest previews are insufficient.
4. Add per-task clause selection beyond agent defaults.
5. Add cost accounting by storing prompt and completion token usage in task logs.

## Positioning

Maestro Protocol is not trying to be a large framework.

It is a minimal orchestration pattern for:

- stateless agent loops
- structured receipts
- filesystem-backed state
- verifier-gated completion
- token-conscious context loading
