from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.maestro import (
    AgentDefinition,
    AgentReceipt,
    AgentStatus,
    DEFAULT_MODELS,
    FileWrite,
    SUPPORTED_PROVIDERS,
    VaultManager,
    make_client,
    verify_agent_output,
)


@pytest.fixture
def vault(tmp_path):
    return VaultManager(base_dir=str(tmp_path / "vault"))


@pytest.fixture
def agent():
    return AgentDefinition(
        name="test_agent",
        description="test",
        allowed_paths=["04_WORKSPACE/"],
        context_paths=["04_WORKSPACE/"],
    )


def _make_project(vault, name):
    project_dir = vault.get_project_dir(name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "00_PROJECT_BRIEF.md").write_text("brief", encoding="utf-8")
    return project_dir


# --- path traversal ---


def test_get_project_dir_rejects_parent_traversal(vault):
    with pytest.raises(ValueError, match="path traversal"):
        vault.get_project_dir("../escape")


def test_resolve_project_path_rejects_parent_traversal(vault):
    _make_project(vault, "proj")
    with pytest.raises(ValueError, match="Invalid project-relative path"):
        vault.resolve_project_path("proj", "../../etc/passwd")


def test_file_write_rejects_absolute_path():
    with pytest.raises(ValidationError):
        FileWrite(path="/etc/passwd", content="x")


# --- is_allowed_path ---


def test_is_allowed_path_basic(vault):
    assert vault.is_allowed_path("04_WORKSPACE/foo.py", ["04_WORKSPACE/"])
    assert not vault.is_allowed_path("01_CURRENT_STATE.md", ["04_WORKSPACE/"])


def test_is_allowed_path_normalizes_separators(vault):
    assert vault.is_allowed_path("04_WORKSPACE\\foo.py", ["04_WORKSPACE/"])
    assert vault.is_allowed_path("/04_WORKSPACE/foo.py", ["04_WORKSPACE/"])


def test_is_allowed_path_multiple_prefixes(vault):
    allowed = ["04_WORKSPACE/docs/", "README.md"]
    assert vault.is_allowed_path("README.md", allowed)
    assert vault.is_allowed_path("04_WORKSPACE/docs/x.md", allowed)
    assert not vault.is_allowed_path("04_WORKSPACE/code/x.py", allowed)


# --- write_project_file ---


def test_write_outside_allowed_path_rejected(vault):
    _make_project(vault, "proj")
    with pytest.raises(ValueError, match="not allowed"):
        vault.write_project_file("proj", "01_CURRENT_STATE.md", "x", ["04_WORKSPACE/"])


def test_write_creates_parent_dirs(vault):
    _make_project(vault, "proj")
    written = vault.write_project_file(
        "proj", "04_WORKSPACE/a/b/c.txt", "hello", ["04_WORKSPACE/"]
    )
    assert written.exists()
    assert written.read_text(encoding="utf-8") == "hello"


# --- file_exists_and_nonempty ---


def test_file_exists_and_nonempty(vault):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    empty = workspace / "empty.txt"
    empty.write_text("", encoding="utf-8")
    nonempty = workspace / "ok.txt"
    nonempty.write_text("hi", encoding="utf-8")
    whitespace = workspace / "ws.txt"
    whitespace.write_text("   \n", encoding="utf-8")

    assert not vault.file_exists_and_nonempty("proj", "04_WORKSPACE/empty.txt")
    assert vault.file_exists_and_nonempty("proj", "04_WORKSPACE/ok.txt")
    assert not vault.file_exists_and_nonempty("proj", "04_WORKSPACE/ws.txt")
    assert not vault.file_exists_and_nonempty("proj", "04_WORKSPACE/missing.txt")


# --- build_file_manifest ---


def test_manifest_skips_ignored_dirs(vault):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    (workspace / "real.py").write_text("print('hi')", encoding="utf-8")
    junk = workspace / "__pycache__"
    junk.mkdir()
    (junk / "junk.pyc").write_text("garbage", encoding="utf-8")
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref", encoding="utf-8")

    paths = [e.path for e in vault.build_file_manifest("proj", ["04_WORKSPACE/"])]
    assert "04_WORKSPACE/real.py" in paths
    assert all("__pycache__" not in p for p in paths)
    assert all(".git" not in p for p in paths)


def test_manifest_respects_max_files(vault):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    for i in range(20):
        (workspace / f"f{i}.txt").write_text("content", encoding="utf-8")
    entries = vault.build_file_manifest("proj", ["04_WORKSPACE/"], max_files=5)
    assert len(entries) == 5


def test_manifest_entries_are_sorted(vault):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    for name in ["c.txt", "a.txt", "b.txt"]:
        (workspace / name).write_text("x", encoding="utf-8")
    entries = vault.build_file_manifest("proj", ["04_WORKSPACE/"])
    assert [e.path for e in entries] == [
        "04_WORKSPACE/a.txt",
        "04_WORKSPACE/b.txt",
        "04_WORKSPACE/c.txt",
    ]


def test_manifest_includes_hash_size_and_preview(vault):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    (workspace / "f.txt").write_text("hello world", encoding="utf-8")
    [entry] = [
        e
        for e in vault.build_file_manifest("proj", ["04_WORKSPACE/"])
        if e.path == "04_WORKSPACE/f.txt"
    ]
    assert entry.size_bytes == len("hello world")
    assert len(entry.sha256_12) == 12
    assert "hello world" in entry.preview


def test_manifest_handles_missing_root(vault):
    _make_project(vault, "proj")
    entries = vault.build_file_manifest("proj", ["04_WORKSPACE/"])
    assert entries == []


# --- compile_protocol ---


def test_compile_protocol_loads_only_selected(vault):
    clauses = {"a": "AAA", "b": "BBB", "c": "CCC"}
    out = vault.compile_protocol("PROTOCOL", clauses, ["a", "c"])
    assert "AAA" in out and "CCC" in out
    assert "BBB" not in out


def test_compile_protocol_loads_all_when_none_selected(vault):
    clauses = {"a": "AAA", "b": "BBB"}
    out = vault.compile_protocol("PROTOCOL", clauses, None)
    assert "AAA" in out and "BBB" in out


def test_compile_protocol_skips_unknown_clause(vault):
    out = vault.compile_protocol("PROTOCOL", {"a": "AAA"}, ["a", "missing"])
    assert "AAA" in out
    assert "missing" not in out


# --- verify_agent_output ---


def test_verify_rejects_failed_status(vault, agent):
    receipt = AgentReceipt(
        status=AgentStatus.FAILED, summary_of_work="x", next_step_suggestion="y"
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert not result.passed


def test_verify_rejects_needs_info_status(vault, agent):
    receipt = AgentReceipt(
        status=AgentStatus.NEEDS_INFO, summary_of_work="x", next_step_suggestion="y"
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert not result.passed


def test_verify_rejects_empty_files_updated(vault, agent):
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE, summary_of_work="x", next_step_suggestion="y"
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert not result.passed
    assert "no files were updated" in result.reason


def test_verify_rejects_path_outside_allowed(vault, agent):
    _make_project(vault, "proj")
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE,
        summary_of_work="x",
        files_updated=["sneaky/place.txt"],
        next_step_suggestion="y",
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert not result.passed
    assert "outside allowed paths" in result.reason


def test_verify_rejects_missing_file(vault, agent):
    _make_project(vault, "proj")
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE,
        summary_of_work="x",
        files_updated=["04_WORKSPACE/never_written.py"],
        next_step_suggestion="y",
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert not result.passed
    assert "missing or empty" in result.reason


def test_verify_passes_for_real_nonempty_file(vault, agent):
    project = _make_project(vault, "proj")
    workspace = project / "04_WORKSPACE"
    workspace.mkdir()
    (workspace / "ok.py").write_text("print('hi')", encoding="utf-8")
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE,
        summary_of_work="x",
        files_updated=["04_WORKSPACE/ok.py"],
        next_step_suggestion="y",
    )
    result = verify_agent_output(vault, "proj", receipt, agent)
    assert result.passed


# --- init_project ---


def test_init_project_refuses_existing_nonempty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    template = tmp_path / "templates" / "fake"
    template.mkdir(parents=True)
    (template / "00_PROJECT_BRIEF.md").write_text("brief", encoding="utf-8")

    vault = VaultManager(base_dir=str(tmp_path / "vault"))
    vault.init_project("proj1", "fake")
    with pytest.raises(FileExistsError):
        vault.init_project("proj1", "fake")


def test_init_project_missing_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vault = VaultManager(base_dir=str(tmp_path / "vault"))
    with pytest.raises(FileNotFoundError):
        vault.init_project("proj1", "does_not_exist")


# --- make_client / providers ---


def test_make_client_dispatches_openai():
    with patch("core.maestro.instructor.from_provider") as mock_from_provider:
        mock_from_provider.return_value = "fake-openai-client"
        client = make_client("openai", "gpt-4o-mini")
    mock_from_provider.assert_called_once_with("openai/gpt-4o-mini")
    assert client == "fake-openai-client"


def test_make_client_dispatches_anthropic():
    with patch("core.maestro.instructor.from_provider") as mock_from_provider:
        mock_from_provider.return_value = "fake-anthropic-client"
        client = make_client("anthropic", "claude-sonnet-4-6")
    mock_from_provider.assert_called_once_with("anthropic/claude-sonnet-4-6")
    assert client == "fake-anthropic-client"


def test_make_client_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported MAESTRO_PROVIDER"):
        make_client("nonsense", "some-model")


def test_default_models_covers_all_supported_providers():
    for provider in SUPPORTED_PROVIDERS:
        assert provider in DEFAULT_MODELS
        routing, agent = DEFAULT_MODELS[provider]
        assert routing and agent
        assert isinstance(routing, str) and isinstance(agent, str)


# --- AgentReceipt structured completion field ---


def test_agent_receipt_default_not_complete():
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE,
        summary_of_work="x",
        next_step_suggestion="y",
    )
    assert receipt.is_project_complete is False


def test_agent_receipt_can_signal_completion():
    receipt = AgentReceipt(
        status=AgentStatus.COMPLETE,
        summary_of_work="x",
        next_step_suggestion="y",
        is_project_complete=True,
    )
    assert receipt.is_project_complete is True
