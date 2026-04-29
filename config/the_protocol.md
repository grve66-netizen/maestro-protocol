# Maestro Protocol

Maestro is a stateless multi-agent orchestrator.

## Core Rules

1. Read project state from the filesystem before acting.
2. Select agents only from `/agents/agent_registry.json`.
3. Agents must return structured receipts.
4. Agents may not write directly to arbitrary paths.
5. The Maestro persists file writes only after path validation.
6. COMPLETE work must pass verification before project state is updated.
7. Failed verification must be logged in `03_LESSONS_LOG.md`.
8. Keep prompts compact: use current state, task-specific context, selected clauses, and compact file manifests instead of full history.
9. The `is_project_complete` flag on a receipt is the only signal that ends the orchestration loop. Set it only when the full project brief is satisfied, not after every intermediate task.

## Clause Rules

Additional clauses may live in `/config/clauses/`.

Agents load only the clauses listed in their `required_clauses` field unless the implementation explicitly adds more.

If a clause conflicts with this core protocol, this core protocol wins.

## Context Rules

Do not send full workspace contents by default.

Use compact context packs containing:

- project brief
- current state
- relevant file manifest
- short previews
- hashes and sizes

Full file contents should only be included when explicitly needed.
