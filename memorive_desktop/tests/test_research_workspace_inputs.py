"""Inputs the research workspace takes from outside: agent tool calls, model results, agent
settings, bibliography files, connected folders and developer clients. Each is checked before
anything is stored or sent on.
"""
from __future__ import annotations

import pytest

from memorive.research.workspace.agent_protocol import validate_arguments
from memorive.research.workspace.bibliography import parse
from memorive.research.workspace.model_receipts import ModelExecutionError, verified_result
from memorive.research.workspace.service import ResearchWorkspace, validate_agent_settings


@pytest.fixture
def workspace(tmp_path):
    ws = ResearchWorkspace(tmp_path / "workspace")
    yield ws
    ws.close()


# Agent tool calls (MCP / CLI)

def test_a_well_formed_tool_call_is_accepted():
    args = {"query": "incubation", "project": "default", "limit": 24, "artifact_ids": ["a1"]}
    assert validate_arguments("memo.search", args) is args
    assert validate_arguments("memo.capabilities", {}) == {}


@pytest.mark.parametrize("method, args, code", [
    ("memo.search", ["query"], "TOOL_ARGUMENTS_INVALID"),
    ("memo.search", {"query": "q"}, "TOOL_ARGUMENTS_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "extra": "x"}, "TOOL_ARGUMENTS_INVALID"),
    ("memo.search", {"query": "", "project": "p"}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q" * 16001, "project": "p"}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": 3, "project": "p"}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "limit": 0}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "limit": 25}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "limit": True}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "artifact_ids": ["a"] * 25}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "artifact_ids": [1]}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "artifact_ids": ["a" * 201]}, "TOOL_ARGUMENT_INVALID"),
    ("memo.search", {"query": "q", "project": "p", "artifact_ids": "a1"}, "TOOL_ARGUMENT_INVALID"),
    ("memo.skill_context", [], "TOOL_ARGUMENTS_INVALID"),
    ("memo.skill_context", {"unexpected": 1}, "TOOL_ARGUMENTS_INVALID"),
])
def test_a_tool_call_outside_its_schema_is_refused(method, args, code):
    with pytest.raises(ValueError) as caught:
        validate_arguments(method, args)
    assert str(caught.value) == code


# Model results

RECEIPT = {"status": "PASS", "behavior_sha256": "b" * 64, "token_usage": {"input_tokens": 10}}


def _result(**changes):
    value = {"status": "PASS", "requested_model": "model-a", "returned_model": "model-a",
             "execution_receipt": dict(RECEIPT), "text": "answer"}
    value.update(changes)
    return value


def test_a_result_from_the_chosen_model_with_a_receipt_is_accepted():
    assert verified_result(_result(), {"model_name": "model-a"})["model_name"] == "model-a"


@pytest.mark.parametrize("changes, code", [
    ({"status": "FAIL"}, "CHAT_MODEL_EXECUTION_FAILED"),
    ({"execution_receipt": dict(RECEIPT, status="FAIL")}, "CHAT_MODEL_EXECUTION_FAILED"),
    ({"execution_receipt": None}, "CHAT_MODEL_EXECUTION_FAILED"),
    ({"requested_model": "model-b"}, "CHAT_MODEL_IDENTITY_MISMATCH"),
    ({"returned_model": "model-b"}, "CHAT_MODEL_IDENTITY_MISMATCH"),
    ({"execution_receipt": {"status": "PASS", "token_usage": {"input_tokens": 10}}}, "CHAT_MODEL_RECEIPT_INCOMPLETE"),
    ({"execution_receipt": {"status": "PASS", "behavior_sha256": "b" * 64}}, "CHAT_MODEL_RECEIPT_INCOMPLETE"),
])
def test_a_result_that_cannot_be_traced_to_the_chosen_model_is_refused(changes, code):
    with pytest.raises(ModelExecutionError) as caught:
        verified_result(_result(**changes), {"model_name": "model-a"})
    assert str(caught.value) == code
    assert isinstance(caught.value.execution_receipt, dict)


# Agent settings

def _agents(**changes):
    value = {"agent": "codex", "custom_agents": [{"id": "custom-my-agent", "name": "My agent", "kind": "cli"}],
             "desktop_agents": {"custom-my-agent": {"model": "model-a", "path": r"C:\Tools\agent.exe"},
                                "claude-desktop": {"model": "model-b"}}}
    value.update(changes)
    return value


def test_agent_settings_with_a_custom_agent_are_accepted():
    validate_agent_settings(_agents())
    validate_agent_settings(_agents(agent="custom-my-agent"))
    validate_agent_settings({"agent": "other"})


def _custom(**changes):
    return {"custom_agents": [dict({"id": "custom-my-agent", "name": "My agent", "kind": "cli"}, **changes)]}


@pytest.mark.parametrize("changes, code", [
    ({"custom_agents": {}}, "AGENT_CONFIG_INVALID"),
    ({"custom_agents": [{"id": f"custom-agent-{n:04d}", "name": "A", "kind": "cli"} for n in range(21)]},
     "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": []}, "AGENT_CONFIG_INVALID"),
    ({"custom_agents": [{"id": "custom-my-agent", "name": "A", "kind": "cli", "path": "x"}]}, "AGENT_CONFIG_INVALID"),
    (_custom(id="my-agent"), "AGENT_CONFIG_INVALID"),
    (_custom(id="custom-SHOUT-agent"), "AGENT_CONFIG_INVALID"),
    (_custom(id="custom-short"), "AGENT_CONFIG_INVALID"),
    ({"custom_agents": [{"id": "custom-my-agent", "name": "A", "kind": "cli"},
                        {"id": "custom-my-agent", "name": "B", "kind": "cli"}]}, "AGENT_CONFIG_INVALID"),
    (_custom(name=" "), "AGENT_CONFIG_INVALID"),
    (_custom(name="n" * 61), "AGENT_CONFIG_INVALID"),
    (_custom(kind="service"), "AGENT_CONFIG_INVALID"),
    ({"agent": "unknown"}, "AGENT_INVALID"),
    ({"desktop_agents": {"unknown": {"model": "m"}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"model": "m", "args": "--yes"}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": "model-a"}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"model": "m\nsecond line"}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"model": "m\x00"}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"model": 3}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"path": "C:\\" + "p" * 1030 + ".exe"}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"model": "m" * 161}}}, "AGENT_CONFIG_INVALID"),
    ({"desktop_agents": {"codex": {"path": "agent.exe"}}}, "AGENT_APP_PATH_INVALID"),
    ({"desktop_agents": {"codex": {"path": r"C:\Tools\agent.cmd"}}}, "AGENT_APP_PATH_INVALID"),
    ({"desktop_agents": {"codex": {"path": r"\\server\share\agent.exe"}}}, "AGENT_APP_PATH_INVALID"),
])
def test_agent_settings_outside_the_allowed_form_are_refused(changes, code):
    with pytest.raises(ValueError) as caught:
        validate_agent_settings(dict({"agent": "codex"}, **changes))
    assert str(caught.value) == code


def test_saving_invalid_agent_settings_keeps_the_saved_ones(workspace):
    before = workspace.settings()
    with pytest.raises(ValueError, match="AGENT_APP_PATH_INVALID"):
        workspace.settings_save({"desktop_agents": {"codex": {"path": "agent.exe"}}}, before["revision"])
    assert workspace.settings() == before


# Which command line "hand to the agent" runs on

COMMAND_LINES = [
    {"profile_ref": "cli:codex", "eligible": True, "profile_kind": "CLI", "adapter_id": "codex_cli"},
    {"profile_ref": "cli:claude-off", "eligible": False, "profile_kind": "CLI", "adapter_id": "claude_code"},
    {"profile_ref": "cli:claude", "eligible": True, "profile_kind": "CLI", "adapter_id": "claude_code"},
    {"profile_ref": "cli:gemini", "eligible": True, "profile_kind": "CLI", "adapter_id": "gemini_cli"},
    {"profile_ref": "api:chat", "eligible": True, "profile_kind": "API", "adapter_id": None},
]


@pytest.mark.parametrize("agent, chosen, runs_on", [
    ("codex", None, "cli:codex"),                       # Codex and Claude Code: their first connected command line
    ("claude-code", None, "cli:claude"),
    ("claude-code", "cli:claude", "cli:claude"),
    ("other", "cli:gemini", "cli:gemini"),              # another agent needs its command line chosen
    ("custom-my-agent", "cli:codex", "cli:codex"),      # a custom command-line agent may run on any of them
    ("other", None, "AGENT_MODEL_UNAVAILABLE"),
    ("claude-code", "cli:codex", "AGENT_MODEL_UNAVAILABLE"),     # not this agent's command line
    ("claude-code", "cli:claude-off", "AGENT_MODEL_UNAVAILABLE"),  # not connected
    ("codex", "api:chat", "AGENT_MODEL_UNAVAILABLE"),
])
def test_hand_to_the_agent_runs_on_a_command_line_of_the_chosen_agent(tmp_path, agent, chosen, runs_on):
    ws = ResearchWorkspace(tmp_path / "ws", catalog=lambda: [dict(o) for o in COMMAND_LINES])
    try:
        ws.settings_save({"agent": agent, "custom_agents": [{"id": "custom-my-agent", "name": "My agent", "kind": "cli"}]},
                         ws.settings()["revision"])
        ws.agent_model = object()
        ws.ask = lambda thread_id, question, request_id, agent_profile_ref: agent_profile_ref
        try:
            result = ws.agent_dispatch("thread", "Why?", "request-0001", chosen)
        except ValueError as error:
            result = str(error)
        assert result == runs_on
        ws.agent_model = None
        if runs_on.startswith("cli:"):
            with pytest.raises(ValueError, match="AGENT_RUNNER_UNAVAILABLE"):
                ws.agent_dispatch("thread", "Why?", "request-0002", chosen)
    finally:
        ws.close()


# Bibliography files

RIS = "TY  - JOUR\nTI  - First paper\nAU  - Doe, J\nPY  - 2024\nID  - k1\nER  - \nTY  - JOUR\nTI  - Second paper\nID  - k2\nER  - \n"
BIB = "@article{k1, title={First paper}, author={Doe, J and Roe, R}, year=2024}\n@article{k2, title={Second paper}}\n"
XML = ("<xml><records><record><rec-number>1</rec-number><titles><title>First paper</title></titles></record>"
       "<record><rec-number>2</rec-number><titles><title>Second paper</title></titles></record></records></xml>")


@pytest.mark.parametrize("name, text", [("refs.ris", RIS), ("refs.bib", BIB), ("refs.BibTeX", BIB), ("refs.xml", XML)])
def test_each_bibliography_format_is_read(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    assert [r["title"] for r in parse(path)] == ["First paper", "Second paper"]


def _parse_error(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        parse(path)
    return str(caught.value)


@pytest.mark.parametrize("name, text, code", [
    ("refs.txt", RIS, "BIBLIOGRAPHY_FORMAT_UNSUPPORTED"),
    ("refs.ris", "nothing here\n", "BIBLIOGRAPHY_RECORD_COUNT_INVALID"),
    ("refs.ris", "".join(f"TY  - JOUR\nTI  - Paper {n}\nID  - k{n}\nER  - \n" for n in range(2001)),
     "BIBLIOGRAPHY_RECORD_COUNT_INVALID"),
    ("refs.ris", "TY  - JOUR\nTI  - One\nID  - k1\nER  - \nTY  - JOUR\nTI  - Two\nID  - k1\nER  - \n",
     "BIBLIOGRAPHY_DUPLICATE_RECORD_KEY"),
    ("refs.bib", "@article{k1, title={Same}}\n@article{k1, title={Again}}\n", "BIBLIOGRAPHY_DUPLICATE_RECORD_KEY"),
    ("refs.xml", '<!DOCTYPE x [<!ENTITY e "x">]><xml/>', "XML_DTD_FORBIDDEN"),
    ("refs.bib", "@string{j = {Journal}}\n@article{k1, title={T}}\n", "BIBTEX_MACROS_UNSUPPORTED_EXPORT_RIS"),
    ("refs.bib", "@article{k1, title={T} # {U}}\n", "BIBTEX_CONCATENATION_UNSUPPORTED_EXPORT_RIS"),
    ("refs.bib", "@article{k1, title={T}\n", "BIBTEX_UNBALANCED_ENTRY"),
])
def test_a_bibliography_that_cannot_be_read_as_given_is_refused(tmp_path, name, text, code):
    assert _parse_error(tmp_path, name, text) == code


def test_a_bibliography_over_eight_megabytes_is_refused_before_reading(tmp_path):
    path = tmp_path / "refs.ris"
    with path.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="BIBLIOGRAPHY_TOO_LARGE"):
        parse(path)


# Connected folders

@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    return folder


def test_a_connection_to_a_folder_is_saved_with_its_capabilities(workspace, library):
    row = workspace.connections.save("default", " Papers ", "zotero", library)
    assert (row["name"], row["root"], row["capabilities"]["annotations"]) == ("Papers", str(library.resolve()), True)
    again = workspace.connections.save("default", "Renamed", "zotero", library, connection_id=row["id"],
                                       expected_revision=row["revision"], enabled=False)
    assert (again["name"], again["enabled"]) == ("Renamed", False)


@pytest.mark.parametrize("args, code", [
    (dict(project="missing"), "PROJECT_NOT_FOUND"),
    (dict(provider="dropbox"), "CONNECTION_INVALID"),
    (dict(enabled=1), "CONNECTION_INVALID"),
    (dict(writeback="yes"), "CONNECTION_INVALID"),
    (dict(name=""), "CONNECTION_NAME_REQUIRED"),
    (dict(name="   "), "CONNECTION_NAME_REQUIRED"),
    (dict(name="n" * 161), "CONNECTOR_TEXT_INVALID"),
    (dict(name=None), "CONNECTOR_TEXT_INVALID"),
])
def test_a_connection_with_invalid_settings_is_refused(workspace, library, args, code):
    call = dict(project="default", name="Papers", provider="folder", root=library)
    call.update(args)
    with pytest.raises(ValueError) as caught:
        workspace.connections.save(**call)
    assert str(caught.value) == code
    assert workspace.store.list("connection", "default") == []


def test_a_connection_needs_an_ordinary_existing_folder(workspace, library):
    (library / "file.txt").write_text("x", encoding="utf-8")
    (library / ".hidden").mkdir()
    for root, error in [(library / "missing", FileNotFoundError), (library / "file.txt", ValueError),
                        (library / ".hidden", ValueError), (library.anchor, ValueError)]:
        with pytest.raises(error):
            workspace.connections.save("default", "Papers", "folder", root)
    assert workspace.store.list("connection", "default") == []


def test_a_saved_connection_keeps_its_folder_project_and_kind(workspace, library, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    workspace.project_save("Second project", project="second")
    row = workspace.connections.save("default", "Papers", "folder", library)
    for change in (dict(root=other), dict(provider="obsidian"), dict(project="second")):
        call = dict(project="default", name="Papers", provider="folder", root=library,
                    connection_id=row["id"], expected_revision=row["revision"])
        call.update(change)
        with pytest.raises(ValueError, match="CONNECTION_SCOPE_IMMUTABLE_CREATE_NEW"):
            workspace.connections.save(**call)


# Developer clients

def _client(**changes):
    value = {"name": "Script", "enabled": True, "projects": ["default"], "connection_ids": [], "scopes": ["read"]}
    value.update(changes)
    return value


def test_a_developer_client_is_saved(workspace, library):
    connection = workspace.connections.save("default", "Papers", "folder", library)
    row = workspace.developer.save(_client(name=" Script ", connection_ids=[connection["id"]], scopes=["read", "draft"]))
    assert row["name"] == "Script"


@pytest.mark.parametrize("config, code", [
    ([], "CLIENT_SETTINGS_INVALID"),
    ({k: v for k, v in _client().items() if k != "scopes"}, "CLIENT_SETTINGS_INVALID"),
    (dict(_client(), token="x"), "CLIENT_SETTINGS_INVALID"),
    (_client(name=" "), "CLIENT_SETTINGS_INVALID"),
    (_client(name="n" * 101), "CLIENT_SETTINGS_INVALID"),
    (_client(enabled="true"), "CLIENT_SETTINGS_INVALID"),
    (_client(projects="default"), "CLIENT_SETTINGS_INVALID"),
    (_client(scopes=["read"] * 101), "CLIENT_SETTINGS_INVALID"),
    (_client(connection_ids=[1]), "CLIENT_SETTINGS_INVALID"),
    (_client(projects=[]), "CLIENT_SCOPE_INVALID"),
    (_client(scopes=["admin"]), "CLIENT_SCOPE_INVALID"),
    (_client(projects=["missing"]), "PROJECT_NOT_FOUND"),
    (_client(connection_ids=["conn_missing"]), "CONNECTION_NOT_FOUND"),
])
def test_a_developer_client_outside_the_allowed_form_is_refused(workspace, config, code):
    with pytest.raises(ValueError) as caught:
        workspace.developer.save(config)
    assert str(caught.value) == code
    assert workspace.store.list("developer_client", "") == []


def test_a_developer_client_cannot_reach_a_connection_of_another_project(workspace, library):
    workspace.project_save("Second project", project="second")
    connection = workspace.connections.save("second", "Papers", "folder", library)
    with pytest.raises(ValueError, match="CLIENT_CONNECTION_SCOPE_INVALID"):
        workspace.developer.save(_client(connection_ids=[connection["id"]]))
    with pytest.raises(ValueError, match="CLIENT_NOT_FOUND"):
        workspace.developer.save(_client(), client_id="client_missing")

def test_a_memory_is_forgotten_only_at_its_current_revision(tmp_path):
    workspace = ResearchWorkspace(tmp_path / "forget", catalog=lambda: [])
    try:
        memory = workspace.memory_save("Prefer primary sources")
        for revision in (None, memory["revision"] + 1):
            with pytest.raises(ValueError, match="REVISION_CONFLICT"):
                workspace.memory_forget(memory["id"], revision)
        assert workspace.store.get("memory", memory["id"])["text"] == "Prefer primary sources"
        assert workspace.memory_forget(memory["id"], memory["revision"])["status"] == "FORGOTTEN"
    finally:
        workspace.close()
