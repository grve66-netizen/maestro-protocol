import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import instructor
from pydantic import BaseModel, Field, field_validator


SUPPORTED_PROVIDERS = ("openai", "anthropic")

DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "openai": ("gpt-4o-mini", "gpt-4o"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-4-6"),
}


def make_client(provider: str, model: str):
    """Return an instructor client bound to provider+model.

    Built-in support: openai, anthropic. Extending to other providers
    (gemini, cohere, groq, ollama, mistral, etc.) is a one-line addition
    here — instructor.from_provider already supports them.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported MAESTRO_PROVIDER: {provider!r}. "
            f"Built-in support: {', '.join(SUPPORTED_PROVIDERS)}. "
            "instructor.from_provider supports more — extend make_client() to enable them."
        )
    return instructor.from_provider(f"{provider}/{model}")


class AgentStatus(str, Enum):
    COMPLETE = "COMPLETE"
    NEEDS_INFO = "NEEDS_INFO"
    FAILED = "FAILED"


class FileWrite(BaseModel):
    path: str = Field(description="Project-relative path to write, e.g. 04_WORKSPACE/app.py")
    content: str = Field(description="Full file content to write")

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        if Path(value).is_absolute():
            raise ValueError("FileWrite.path must be project-relative, not absolute")
        return value


class AgentDefinition(BaseModel):
    name: str
    description: str
    intents: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=lambda: ["04_WORKSPACE/"])
    required_clauses: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=lambda: ["04_WORKSPACE/"])
    max_context_files: int = 8


class AgentRegistry(BaseModel):
    agents: list[AgentDefinition]

    def names(self) -> list[str]:
        return [agent.name for agent in self.agents]

    def get(self, name: str) -> AgentDefinition:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise KeyError(f"Unknown agent: {name}")


class MaestroDecision(BaseModel):
    reasoning: str = Field(description="Brief reason this agent was selected")
    chosen_agent: str = Field(description="Name of the agent from agent_registry.json")
    task_for_agent: str = Field(description="Specific instructions for the selected agent")


class AgentReceipt(BaseModel):
    status: AgentStatus
    summary_of_work: str
    file_writes: list[FileWrite] = Field(default_factory=list)
    files_updated: list[str] = Field(default_factory=list)
    next_step_suggestion: str
    questions_for_user: list[str] = Field(default_factory=list)
    is_project_complete: bool = Field(
        default=False,
        description="Set true only when the entire project goal is finished, not just this task.",
    )


class VerificationResult(BaseModel):
    passed: bool
    reason: str
    required_changes: list[str] = Field(default_factory=list)


class FileManifestEntry(BaseModel):
    path: str
    size_bytes: int
    sha256_12: str
    modified_utc: str
    preview: str = ""


class ContextPack(BaseModel):
    project_brief: str
    current_state: str
    relevant_files: list[FileManifestEntry]
    token_policy_note: str

    def render_for_prompt(self) -> str:
        files = "\n".join(
            f"- {item.path} ({item.size_bytes} bytes, sha256:{item.sha256_12})\n  preview: {item.preview}"
            for item in self.relevant_files
        ) or "No relevant workspace files selected."
        return (
            f"Project brief:\n{self.project_brief}\n\n"
            f"Current state:\n{self.current_state}\n\n"
            f"Relevant file manifest:\n{files}\n\n"
            f"Context policy:\n{self.token_policy_note}"
        )


class VaultManager:
    def __init__(self, base_dir: str = "./vault"):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def get_project_dir(self, project_name: str) -> Path:
        project_dir = (self.base_dir / project_name).resolve()
        if not self._is_within(project_dir, self.base_dir):
            raise ValueError("Invalid project name: path traversal detected")
        return project_dir

    def resolve_project_path(self, project_name: str, relative_path: str) -> Path:
        project_dir = self.get_project_dir(project_name)
        target = (project_dir / relative_path).resolve()
        if not self._is_within(target, project_dir):
            raise ValueError(f"Invalid project-relative path: {relative_path}")
        return target

    def load_text(self, path: Path, missing_message: str) -> str:
        if not path.exists():
            raise FileNotFoundError(missing_message)
        return path.read_text(encoding="utf-8")

    def load_protocol(self, config_dir: str = "./config") -> str:
        path = Path(config_dir) / "the_protocol.md"
        return self.load_text(path, f"Protocol missing at {path}")

    def load_clauses(self, clauses_dir: str = "./config/clauses") -> dict[str, str]:
        path = Path(clauses_dir)
        if not path.exists():
            return {}
        return {
            clause_file.stem: clause_file.read_text(encoding="utf-8")
            for clause_file in sorted(path.glob("*.md"))
        }

    def compile_protocol(self, protocol: str, clauses: dict[str, str], selected_clause_names: Optional[list[str]] = None) -> str:
        selected_names = list(clauses.keys()) if selected_clause_names is None else selected_clause_names
        compiled = protocol.strip()
        loaded = [(name, clauses[name]) for name in selected_names if name in clauses]
        if loaded:
            compiled += "\n\n# Loaded Clauses\n"
            for name, content in loaded:
                compiled += f"\n\n## Clause: {name}\n{content.strip()}\n"
        return compiled

    def load_agent_registry(self, registry_path: str = "./agents/agent_registry.json") -> AgentRegistry:
        path = Path(registry_path)
        raw = self.load_text(path, f"Agent registry missing at {path}")
        return AgentRegistry.model_validate_json(raw)

    def load_project_brief(self, project_name: str) -> str:
        path = self.get_project_dir(project_name) / "00_PROJECT_BRIEF.md"
        return self.load_text(path, f"Project brief missing at {path}")

    def load_current_state(self, project_name: str) -> str:
        path = self.get_project_dir(project_name) / "01_CURRENT_STATE.md"
        if not path.exists():
            return "No current state has been recorded yet."
        return path.read_text(encoding="utf-8")

    def update_current_state(self, project_name: str, update: str) -> None:
        path = self.resolve_project_path(project_name, "01_CURRENT_STATE.md")
        path.write_text(f"## Current State\n\n{update}\n", encoding="utf-8")

    def append_log(self, project_name: str, relative_path: str, message: str) -> None:
        path = self.resolve_project_path(project_name, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(f"\n- {message}")

    def is_allowed_path(self, relative_path: str, allowed_paths: list[str]) -> bool:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        return any(normalized.startswith(prefix.replace("\\", "/").lstrip("/")) for prefix in allowed_paths)

    def write_project_file(self, project_name: str, relative_path: str, content: str, allowed_paths: list[str]) -> Path:
        if not self.is_allowed_path(relative_path, allowed_paths):
            raise ValueError(f"Path not allowed for this agent: {relative_path}")
        path = self.resolve_project_path(project_name, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def file_exists_and_nonempty(self, project_name: str, relative_path: str) -> bool:
        path = self.resolve_project_path(project_name, relative_path)
        return path.exists() and path.is_file() and bool(path.read_text(encoding="utf-8").strip())

    def build_file_manifest(self, project_name: str, roots: list[str], max_files: int = 20, preview_chars: int = 240) -> list[FileManifestEntry]:
        project_dir = self.get_project_dir(project_name)
        entries: list[FileManifestEntry] = []
        ignored_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}

        for root in roots:
            root_path = self.resolve_project_path(project_name, root)
            if not root_path.exists():
                continue
            candidates = [root_path] if root_path.is_file() else [p for p in root_path.rglob("*") if p.is_file()]
            for file_path in candidates:
                if any(part in ignored_dirs for part in file_path.parts):
                    continue
                if len(entries) >= max_files:
                    break
                try:
                    raw = file_path.read_bytes()
                    text = raw.decode("utf-8", errors="ignore")
                except OSError:
                    continue
                rel = file_path.relative_to(project_dir).as_posix()
                stat = file_path.stat()
                entries.append(FileManifestEntry(
                    path=rel,
                    size_bytes=stat.st_size,
                    sha256_12=hashlib.sha256(raw).hexdigest()[:12],
                    modified_utc=datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    preview=" ".join(text.strip().split())[:preview_chars],
                ))
            if len(entries) >= max_files:
                break

        return sorted(entries, key=lambda item: item.path)

    def build_context_pack(self, project_name: str, agent: AgentDefinition) -> ContextPack:
        return ContextPack(
            project_brief=self.load_project_brief(project_name),
            current_state=self.load_current_state(project_name),
            relevant_files=self.build_file_manifest(project_name, agent.context_paths, max_files=agent.max_context_files),
            token_policy_note=(
                "Only a compact file manifest is included by default. "
                "Request specific files in next_step_suggestion if full contents are required. "
                "Prefer targeted edits over rewriting unrelated files."
            ),
        )

    def create_task_log(self, project_name: str, payload: dict) -> None:
        task_dir = self.resolve_project_path(project_name, "05_TASKS")
        task_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = task_dir / f"task_{timestamp}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def init_project(self, project_name: str, template_name: str = "python_app") -> None:
        project_dir = self.get_project_dir(project_name)
        if project_dir.exists() and any(project_dir.iterdir()):
            raise FileExistsError(f"Project already exists and is not empty: {project_dir}")
        template_dir = Path("./templates") / template_name
        if not template_dir.exists():
            raise FileNotFoundError(f"Template not found: {template_dir}")
        shutil.copytree(template_dir, project_dir, dirs_exist_ok=True)


def verify_agent_output(vault: VaultManager, project_name: str, receipt: AgentReceipt, agent: AgentDefinition) -> VerificationResult:
    if receipt.status != AgentStatus.COMPLETE:
        return VerificationResult(passed=False, reason="Only COMPLETE receipts can be verified")

    written_paths = [file_write.path for file_write in receipt.file_writes]
    reported_paths = receipt.files_updated or written_paths

    if not reported_paths:
        return VerificationResult(
            passed=False,
            reason="Agent reported COMPLETE but no files were updated",
            required_changes=["Return file_writes and files_updated for completed work"],
        )

    for path in reported_paths:
        if not vault.is_allowed_path(path, agent.allowed_paths):
            return VerificationResult(
                passed=False,
                reason=f"File path is outside allowed paths for {agent.name}: {path}",
                required_changes=[f"Only write under: {agent.allowed_paths}"],
            )
        if not vault.file_exists_and_nonempty(project_name, path):
            return VerificationResult(
                passed=False,
                reason=f"File missing or empty after write: {path}",
                required_changes=["Create a non-empty file and list it in files_updated"],
            )

    return VerificationResult(passed=True, reason="Basic verification passed")


def conduct(
    project_name: str,
    user_prompt: str,
    provider: Optional[str] = None,
    routing_model: Optional[str] = None,
    agent_model: Optional[str] = None,
    max_iterations: int = 10,
) -> None:
    provider = provider or os.getenv("MAESTRO_PROVIDER", "openai")
    default_routing, default_agent = DEFAULT_MODELS.get(provider, (None, None))
    routing_model = routing_model or os.getenv("MAESTRO_ROUTING_MODEL") or default_routing
    agent_model = agent_model or os.getenv("MAESTRO_AGENT_MODEL") or default_agent

    if not routing_model or not agent_model:
        raise ValueError(
            f"No default models registered for provider {provider!r}. "
            "Set MAESTRO_ROUTING_MODEL and MAESTRO_AGENT_MODEL, "
            "or pass --routing-model and --agent-model."
        )

    print(f"Maestro Protocol initiated for project: {project_name}")
    print(f"Provider: {provider} | routing: {routing_model} | agent: {agent_model}")
    vault = VaultManager()
    routing_client = make_client(provider, routing_model)
    agent_client = make_client(provider, agent_model)

    try:
        protocol = vault.load_protocol()
        clauses = vault.load_clauses()
        registry = vault.load_agent_registry()
    except (FileNotFoundError, ValueError) as error:
        print(f"Setup error: {error}")
        return

    allowed_agents = registry.names()
    registry_summary = "\n".join(
        f"- {agent.name}: {agent.description} | intents: {', '.join(agent.intents)}"
        for agent in registry.agents
    )
    routing_protocol = protocol.strip()
    current_task = user_prompt

    for iteration in range(1, max_iterations + 1):
        project_brief = vault.load_project_brief(project_name)
        current_state = vault.load_current_state(project_name)

        print(f"\n--- Iteration {iteration}/{max_iterations} ---")
        print("Selecting agent with compact routing context...")

        decision = routing_client.chat.completions.create(
            response_model=MaestroDecision,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the Maestro, a stateless multi-agent orchestrator. "
                        "Choose exactly one agent from the registry. Keep reasoning concise.\n\n"
                        f"Core protocol only:\n{routing_protocol}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Available agents:\n{registry_summary}\n\n"
                        f"Allowed agent names: {allowed_agents}\n\n"
                        f"Project brief:\n{project_brief}\n\n"
                        f"Current state:\n{current_state}\n\n"
                        f"Current task:\n{current_task}"
                    ),
                },
            ],
        )

        if decision.chosen_agent not in allowed_agents:
            current_task = f"Invalid agent selected. Choose only from: {allowed_agents}"
            vault.append_log(project_name, "03_LESSONS_LOG.md", current_task)
            continue

        agent = registry.get(decision.chosen_agent)
        agent_protocol = vault.compile_protocol(protocol, clauses, agent.required_clauses)
        context_pack = vault.build_context_pack(project_name, agent)

        print(f"Selected agent: {agent.name}")
        print(f"Loaded clauses: {agent.required_clauses or ['none']}")
        print(f"Context files summarized: {len(context_pack.relevant_files)}")

        receipt = agent_client.chat.completions.create(
            response_model=AgentReceipt,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are the {agent.name} agent. Follow the protocol strictly. "
                        "Return structured output only. If you complete work, include file_writes for files the Maestro should persist. "
                        f"You may only write under these paths: {agent.allowed_paths}. "
                        "Set is_project_complete=true only when the entire project goal in the brief is finished. "
                        "For intermediate steps, leave it false and describe the next step in next_step_suggestion.\n\n"
                        f"Protocol and selected clauses:\n{agent_protocol}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Context pack:\n{context_pack.render_for_prompt()}\n\n"
                        f"Task:\n{decision.task_for_agent}"
                    ),
                },
            ],
        )

        files_written: list[str] = []
        verification = VerificationResult(passed=False, reason="Not verified")

        if receipt.status == AgentStatus.COMPLETE:
            try:
                for file_write in receipt.file_writes:
                    vault.write_project_file(project_name, file_write.path, file_write.content, agent.allowed_paths)
                    files_written.append(file_write.path)
                if not receipt.files_updated:
                    receipt.files_updated = files_written
                verification = verify_agent_output(vault, project_name, receipt, agent)
            except ValueError as error:
                verification = VerificationResult(passed=False, reason=str(error), required_changes=["Use only safe project-relative paths"])

            if verification.passed:
                vault.update_current_state(project_name, receipt.summary_of_work)
                vault.append_log(project_name, "02_DECISIONS_LOG.md", f"{agent.name}: {receipt.summary_of_work}")
                current_task = receipt.next_step_suggestion
                print("Verification passed.")

                vault.create_task_log(project_name, {
                    "iteration": iteration,
                    "provider": provider,
                    "routing_model": routing_model,
                    "agent_model": agent_model,
                    "agent": agent.name,
                    "loaded_clauses": agent.required_clauses,
                    "context_files": [entry.model_dump() for entry in context_pack.relevant_files],
                    "decision": decision.model_dump(),
                    "receipt": receipt.model_dump(),
                    "files_written": files_written,
                    "verification": verification.model_dump(),
                    "next_task": current_task,
                })

                if receipt.is_project_complete:
                    print("Project completed successfully.")
                    return
            else:
                print(f"Verification failed: {verification.reason}")
                vault.append_log(project_name, "03_LESSONS_LOG.md", f"Verification failed for {agent.name}: {verification.reason}")
                current_task = f"Fix the failed output. Reason: {verification.reason}. Required changes: {verification.required_changes}"

        elif receipt.status == AgentStatus.NEEDS_INFO:
            print(f"Agent needs clarification: {receipt.questions_for_user}")
            user_answer = input("Your answer: ")
            current_task = f"The user answered: {user_answer}. Continue the original task."

        elif receipt.status == AgentStatus.FAILED:
            print("Agent failed.")
            vault.append_log(project_name, "03_LESSONS_LOG.md", f"{agent.name} failed: {receipt.summary_of_work}")
            current_task = "The previous agent failed. Choose a different approach or a different agent."

        vault.create_task_log(project_name, {
            "iteration": iteration,
            "provider": provider,
            "routing_model": routing_model,
            "agent_model": agent_model,
            "agent": agent.name,
            "loaded_clauses": agent.required_clauses,
            "context_files": [entry.model_dump() for entry in context_pack.relevant_files],
            "decision": decision.model_dump(),
            "receipt": receipt.model_dump(),
            "files_written": files_written,
            "verification": verification.model_dump(),
            "next_task": current_task,
        })

    vault.append_log(project_name, "03_LESSONS_LOG.md", f"Max iterations reached on task: {current_task}")
    print("Max iterations reached. Stopped to avoid looping.")


if __name__ == "__main__":
    conduct("Fintech_App", "Build a landing page for my fintech app")
