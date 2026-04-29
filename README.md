# 🎻 The Maestro Protocol

A stateless, verification-driven multi-agent orchestrator.

Most AI agent frameworks fail at scale. They stuff massive chat histories into active memory, causing models to hallucinate, drift from their core instructions, and burn through expensive tokens. When agents try to return large code blocks inside JSON payloads, the escape characters break and the entire system crashes.

The Maestro Protocol solves this by treating AI orchestration like a microservices architecture.

## Core Principles

**Stateless Routing.** The Maestro doesn't hoard memory. It reads the current state from the filesystem, delegates a single task, and wipes its context clean. Every iteration is fresh.

**Strict Verification.** A Python `while` loop gates every agent's output against the immutable Core Protocol. Unverified work never reaches the user.

**Control Plane vs Data Plane.** Agents deliver large files (code, markdown, HTML) directly to the filesystem. They return only lightweight Pydantic receipts (JSON) to the Maestro. Zero JSON parsing errors on big payloads.

**Token-Conscious Context.** Agents see compact file manifests — sha256 hashes, sizes, and previews — not full file contents. Just the selected clauses, the project brief, current state, and a manifest. Nothing else.

## Architecture

The system is split into four distinct layers.

**The Laws** — `/config/the_protocol.md` and `/config/clauses/`.
The absolute, immutable rules of the system. The core protocol is injected into every task delegation. Selective clauses (e.g. `karpathy_coding_clause`, `security_clause`, `docs_clause`) are loaded only by the agents that need them.

**The Agents** — `/agents/agent_registry.json`.
Worker agents triggered by intents. Each declares `allowed_paths` (where it may write), `context_paths` (what files it sees), `required_clauses` (which laws apply), and `max_context_files` (how big its manifest can grow).

**The Engine** — `/core/maestro.py`.
The orchestrator. A strict Python loop powered by `instructor` and a pluggable LLM backend. Handles routing, verification, file persistence, and state updates.

**The Vault** — `/vault/<project>/`.
The filesystem-backed memory. Agents work in isolated sandboxes and write deliverables directly to the vault. The Maestro reads `01_CURRENT_STATE.md` to know where the project stands.

## Repository Structure

```text
/maestro-protocol/
├── /core/
│   ├── maestro.py                 # Orchestrator: routing, verification, file plane, schemas
│   └── cli.py                     # Subcommands: init / run / status / manifest
│
├── /agents/
│   └── agent_registry.json        # The directory of available agents
│
├── /config/
│   ├── the_protocol.md            # The universal laws
│   └── /clauses/                  # Optional rules, loaded selectively per agent
│       ├── karpathy_coding_clause.md
│       ├── security_clause.md
│       └── docs_clause.md
│
├── /vault/                        # Filesystem memory (state)
│   └── /<project>/
│       ├── 00_PROJECT_BRIEF.md    # The project goal
│       ├── 01_CURRENT_STATE.md    # Tracks current state
│       ├── 02_DECISIONS_LOG.md    # Audit trail of accepted work
│       ├── 03_LESSONS_LOG.md      # Failures and corrections
│       ├── /04_WORKSPACE/         # Raw deliverables (code, markdown, etc.)
│       └── /05_TASKS/             # Per-iteration JSON audit logs
│
├── /templates/                    # Project templates (python_app, landing_page, ...)
├── /tests/                        # Tests for path safety and verification primitives
├── pyproject.toml
├── requirements.txt
└── README.md
```

## How It Works: The Verification Loop

The Maestro operates on a strict cycle enforced by an inescapable Python loop.

1. **Assess.** The Maestro reads `00_PROJECT_BRIEF.md` and `01_CURRENT_STATE.md` from disk.
2. **Route.** A routing model selects exactly one agent from `agent_registry.json` and produces a focused task instruction.
3. **Delegate.** The selected agent receives the core protocol, its required clauses, a compact context pack, and the task. Nothing else.
4. **Execute.** The agent generates work. Large outputs (code, markdown) go in `file_writes` on its receipt — the Maestro persists them through the controlled data plane.
5. **Report.** The agent returns a strict Pydantic receipt: `COMPLETE`, `NEEDS_INFO`, or `FAILED`.
6. **Verify (the Gatekeeper).** Before any state mutates, the Maestro confirms files were actually written, paths are inside the agent's `allowed_paths`, files exist, and files are non-empty.
   - **Pass** → `01_CURRENT_STATE.md` and `02_DECISIONS_LOG.md` are updated. The loop continues.
   - **Fail** → the failure is logged in `03_LESSONS_LOG.md`. The work is rejected. The agent is asked to try again.
7. **Complete.** The loop ends only when an agent returns a receipt with `is_project_complete: true` — a structured boolean, not a string-match heuristic. A `MAX_ITERATIONS` escape hatch prevents infinite looping.

## Token Strategy

Every loop sends only:

1. The core protocol
2. The agent's selected clauses
3. The project brief
4. The current state
5. The current task
6. A compact manifest (paths, hashes, sizes, previews) for relevant files

Each agent's `agent_registry.json` entry caps its own context:

```json
{
  "context_paths": ["04_WORKSPACE/frontend/", "04_WORKSPACE/docs/"],
  "required_clauses": ["karpathy_coding_clause"],
  "max_context_files": 8
}
```

This prevents every agent from receiving every rule and every file.

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
  "next_step_suggestion": "Add a contact form",
  "is_project_complete": false
}
```

The Maestro validates every path before writing: no absolute paths, no `..` traversal, no writes outside the agent's `allowed_paths`. An agent cannot escape its sandbox.

## Quickstart

### Requirements

- Python 3.10 or newer
- An API key for whichever provider you use (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`). Only the `run` command calls the API; `init`, `status`, and `manifest` work offline.

### Install

```bash
git clone https://github.com/grve66-netizen/maestro-protocol.git
cd maestro-protocol
pip install -r requirements.txt
```

For Anthropic: `pip install '.[anthropic]'`.

### Set keys

```bash
export OPENAI_API_KEY="..."
# or
export ANTHROPIC_API_KEY="..."
export MAESTRO_PROVIDER=anthropic
```

### Create a project

```bash
python -m core.cli init my_project --template landing_page
```

This copies a template into `/vault/my_project/`. Edit `00_PROJECT_BRIEF.md` to describe what you want.

### Run

```bash
python -m core.cli run my_project "Build a landing page with hero, value prop, CTA, and footer"
```

### Inspect

```bash
python -m core.cli status my_project       # current state
python -m core.cli manifest my_project     # workspace file manifest
```

## Providers

Built-in support: **OpenAI** (default) and **Anthropic**. Selected by `MAESTRO_PROVIDER` or `--provider`.

| Provider  | Default routing model | Default agent model  |
|-----------|-----------------------|----------------------|
| openai    | `gpt-4o-mini`         | `gpt-4o`             |
| anthropic | `claude-haiku-4-5`    | `claude-sonnet-4-6`  |

Override per-run:

```bash
python -m core.cli run my_project "..." \
  --provider anthropic \
  --routing-model claude-haiku-4-5 \
  --agent-model claude-sonnet-4-6
```

To enable other providers (Gemini, Cohere, Groq, Ollama, Mistral, etc.), extend `SUPPORTED_PROVIDERS` and `DEFAULT_MODELS` in [core/maestro.py](core/maestro.py). `instructor.from_provider` handles the underlying SDK plumbing.

### Environment defaults

```bash
export MAESTRO_PROVIDER="openai"          # or "anthropic"
export MAESTRO_ROUTING_MODEL="gpt-4o-mini"
export MAESTRO_AGENT_MODEL="gpt-4o"
```

Resolution order (highest priority first): CLI args → env vars → built-in defaults for the selected provider.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers path traversal, allowed-path enforcement, manifest building, protocol compilation, the verification gate, and provider dispatch. No LLM is called, so no API key is needed.

## Tech Stack

- **Python 3.10+** — core logic, `pathlib`-based safe I/O
- **Pydantic + instructor** — strict JSON control plane, multi-provider abstraction
- **OpenAI or Anthropic SDK** — pluggable via `MAESTRO_PROVIDER`

## Limitations

The current verification gate is **presence-only**. It confirms files were written, paths are allowed, and files are non-empty. It does not verify that the contents are correct, that code compiles, or that requirements were semantically met. Adding a dedicated review agent for semantic verification is the most important next step — see Roadmap.

## Roadmap

- **True hybrid routing** — different providers for the routing model and the agent model (e.g. local Ollama for routing, cloud Anthropic for agents).
- **Semantic verification** by a dedicated review agent.
- **Automatic test execution** for code-producing agents.
- **On-demand full-file retrieval** when manifest previews aren't enough.
- **Per-task clause selection** beyond the static agent defaults.
- **Token cost accounting** persisted to task logs.

## Positioning

The Maestro Protocol is not trying to be a large framework.

It is a minimal orchestration pattern for:

- stateless agent loops
- structured receipts
- filesystem-backed state
- verifier-gated completion
- token-conscious context loading

## License

MIT. See [LICENSE](LICENSE).
