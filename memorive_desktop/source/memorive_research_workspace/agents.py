"""Which agent the user works with, and handing a conversation to a desktop agent app.

Settings name an agent: a command-line agent run as a model profile (codex, claude-code, other, or a
custom one), or a desktop app (codex-desktop, claude-desktop, or a custom desktop agent).
validate_agent_settings() checks these settings when they are saved; agent_matches() tells whether a
model profile belongs to the chosen agent.

desktop_handoff() prepares a folder for a desktop agent app: the hand-off pack, copies of the
sources with their text by excerpt, the conversation, and START_HERE.md. Nothing is sent and no model
is called; the user opens the folder in the app.
"""
import re
from pathlib import Path

from memorive.platform.windows_paths import windows_io_path

from .evidence import material_layer, safe_path, source_document_hash
from .store import digest, now, packed, uid

BUILT_IN_AGENTS = {'codex', 'claude-code', 'other', 'codex-desktop', 'claude-desktop'}
DESKTOP_AGENTS = {'codex-desktop', 'claude-desktop'}
# Adapters of the command-line agents; 'other' is any adapter that is none of these.
CLI_ADAPTERS = {'codex': {'codex_cli'}, 'claude-code': {'claude_code', 'claude_code_cli'}}
HISTORY_FILE_CHARS = 80000  # the conversation is split into files of about this size, between messages


def agent_matches(option, agent):
    """Whether a model profile (option) is one the chosen agent runs on."""
    adapter = option.get('adapter_id', '')
    if agent.startswith('custom-'):
        return option.get('profile_kind') == 'CLI'
    if agent in CLI_ADAPTERS:
        return adapter in CLI_ADAPTERS[agent]
    return adapter not in CLI_ADAPTERS['codex'] | CLI_ADAPTERS['claude-code']


def desktop_agent(settings):
    """Whether the chosen agent is a desktop app (prepared folders) rather than a command-line agent."""
    agent = settings.get('agent', 'codex')
    return agent in DESKTOP_AGENTS or any(a['id'] == agent and a['kind'] == 'desktop' for a in settings.get('custom_agents', []))


def validate_agent_settings(value):
    """The agent part of the research settings, as saved from the window."""
    custom = value.get('custom_agents', [])
    configs = value.get('desktop_agents', {})
    if not isinstance(custom, list) or len(custom) > 20 or not isinstance(configs, dict):
        raise ValueError('AGENT_CONFIG_INVALID')
    ids = set(BUILT_IN_AGENTS)
    for agent in custom:
        if not isinstance(agent, dict) or set(agent) != {'id', 'name', 'kind'}:
            raise ValueError('AGENT_CONFIG_INVALID')
        if not isinstance(agent['id'], str) or not re.fullmatch(r'custom-[a-z0-9-]{8,48}', agent['id']) or agent['id'] in ids:
            raise ValueError('AGENT_CONFIG_INVALID')
        if (not isinstance(agent['name'], str) or not agent['name'].strip() or len(agent['name']) > 60
                or agent['kind'] not in {'desktop', 'cli'}):
            raise ValueError('AGENT_CONFIG_INVALID')
        ids.add(agent['id'])
    if value.get('agent') not in ids:
        raise ValueError('AGENT_INVALID')
    for key, item in configs.items():
        if key not in ids or not isinstance(item, dict) or set(item) - {'model', 'path'}:
            raise ValueError('AGENT_CONFIG_INVALID')
        if any(not isinstance(v, str) or len(v) > 1024 or '\x00' in v or '\n' in v or '\r' in v for v in item.values()):
            raise ValueError('AGENT_CONFIG_INVALID')
        if len(item.get('model', '')) > 160:
            raise ValueError('AGENT_CONFIG_INVALID')
        path = item.get('path', '')
        if path and (not Path(path).is_absolute() or Path(path).suffix.lower() != '.exe' or path.startswith(('\\\\', '//'))):
            raise ValueError('AGENT_APP_PATH_INVALID')


def desktop_handoff(workspace, thread_id, question, root):
    """Prepare a folder under root for the chosen desktop agent app; returns its id, path and the
    prompt to paste into the app."""
    from memorive_language.handoff import desktop_intro, launch_prompt
    from .skill_bridge import transport
    settings = workspace.settings()
    validate_agent_settings(settings)
    if not desktop_agent(settings):
        raise ValueError('AGENT_DESKTOP_REQUIRED')
    target = settings['agent']
    config = settings.get('desktop_agents', {}).get(target, {})
    model = config.get('model', '').strip()
    if not model:
        raise ValueError('AGENT_DESKTOP_MODEL_REQUIRED')
    thread = workspace.thread_get(thread_id)
    if any(j.get('thread_id') == thread_id and j['status'] in {'RUNNING', 'QUEUED'}
           for j in workspace.store.list('job', thread['project'])):
        raise ValueError('THREAD_BUSY')
    pack = workspace.handoffs.create(thread_id, question.strip() if isinstance(question, str) else '', evidence_ids=[], agent=target)
    question = pack['question']
    admitted = {a['id'] for a in pack['source_versions']}
    artifacts = [a for a in workspace.store.list('artifact', thread['project']) if a['id'] in admitted]
    ident = uid('')[:12]
    folder = Path(root) / ident
    folder.mkdir(parents=True, exist_ok=False)
    material = folder / 'materials'
    material.mkdir()
    warnings = list(pack.get('unavailable_sources', []))
    messages = pack['conversation_summary']
    (folder / 'handoff.json').write_text(packed(pack), encoding='utf8')
    (folder / 'transport.json').write_text(packed(transport(workspace)), encoding='utf8')
    # Reuse the source- and topic-bound history, including withheld-message rules.
    cited = {r['id']: r for m in messages for r in m.get('citations', []) if isinstance(r, dict) and r.get('id')}
    admitted = {a['id'] for a in artifacts}
    for evidence_id, ref in cited.items():
        if ref.get('artifact_id') not in admitted:
            warnings.append({'evidence_id': evidence_id, 'reason': 'OUTSIDE_CURRENT_SCOPE'})
    sources = [_export_source(workspace, artifact, number, material, folder, warnings)
               for number, artifact in enumerate(sorted(artifacts, key=lambda a: a['id']), 1)]
    history = _write_conversation(folder, messages)
    source_text = '# Sources\n\n' + '\n\n'.join(
        f"## {a['title']}\nID: {a['id']}\nSHA256: {a['source_hash']}\nFiles: {', '.join(a['files'])}\nStatus: {a.get('issue', 'READY')}"
        for a in sources)
    (folder / 'sources.md').write_text(source_text, encoding='utf8')
    name = {'codex-desktop': 'Codex Desktop', 'claude-desktop': 'Claude Code Desktop'}.get(
        target, next((a['name'] for a in settings.get('custom_agents', []) if a['id'] == target), target))
    (folder / 'START_HERE.md').write_text(desktop_intro(pack, name, model, history, sources, packed(warnings)), encoding='utf8')
    files = {p.relative_to(folder).as_posix(): {'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size}
             for p in folder.rglob('*') if p.is_file()}
    receipt = {
        'schema_version': 'MemoDesktopHandoff-v2', 'language_context': pack.get('language_context'), 'id': ident,
        'project': thread['project'], 'thread_id': thread_id, 'thread_revision': pack['thread_revision'],
        'handoff_id': pack['id'], 'handoff_hash': pack['content_hash'], 'version_binding': pack['version_binding'],
        'agent': target, 'agent_name': name, 'model': model, 'app_path': config.get('path', ''), 'question': question,
        'message_count': len(messages), 'source_count': len(sources), 'warnings': warnings, 'created_at': now(),
        'files': files, 'external_transmission': False, 'model_called': False}
    (folder / 'manifest.json').write_text(packed(receipt), encoding='utf8')
    with workspace.store.tx() as db:
        # The conversation and its sources must not have changed while the folder was written.
        workspace.handoffs.validate(pack, db)
        record = dict(receipt, path=str(folder), manifest_hash=digest((folder / 'manifest.json').read_bytes()))
        workspace.store.put('desktop_handoff', ident, thread['project'], record, db=db)
    prompt = launch_prompt(pack, folder / 'START_HERE.md', model)
    return {'status': 'PREPARED', 'id': ident, 'path': str(folder), 'prompt': prompt, 'message_count': len(messages),
            'source_count': len(sources), 'warnings': warnings, 'agent_name': name}


def _export_source(workspace, artifact, number, material, folder, warnings):
    """Copy one source (and the original it was made from) into the folder, with its text by excerpt.
    A source that cannot be exported is listed with its issue and added to warnings."""
    row = {'id': artifact['id'], 'title': artifact['title'], 'state': artifact['state'],
           'source_hash': artifact['content_hash'], 'files': []}
    try:
        source = safe_path(workspace.store.root if artifact['kind'] == 'derived' else artifact['root'], artifact['path'])
        data = windows_io_path(source).read_bytes()  # read once: what is written is what was checked
        if digest(data) != artifact['content_hash']:
            raise ValueError('EVIDENCE_STALE')
        copied = material / (f'{number:03d}' + source.suffix.lower())
        windows_io_path(copied).write_bytes(data)
        row['files'].append(copied.relative_to(folder).as_posix())
        with workspace.store.tx() as db:
            excerpts = [dict(r) for r in db.execute('SELECT * FROM chunks WHERE artifact_id=? ORDER BY page,line_start,id',
                                                    (artifact['id'],))]
        text = '\n\n'.join(f"[{c['id']}] page={c['page']} lines={c['line_start']}-{c['line_end']}\n{c['text']}" for c in excerpts)
        extracted = material / f'{number:03d}-text.md'
        extracted.write_text(text, encoding='utf8')
        row['files'].append(extracted.relative_to(folder).as_posix())
        row['chunks'] = len(excerpts)
        if artifact.get('source_original_path'):
            original = safe_path(artifact['root'], artifact['source_original_path'])
            data = original.read_bytes()
            if digest(data) != artifact['source_content_hash']:
                raise ValueError('EVIDENCE_STALE')
            preserved = material / (f'{number:03d}-original' + original.suffix.lower())
            preserved.write_bytes(data)
            row['files'].append(preserved.relative_to(folder).as_posix())
        row.update(material_layer=material_layer(artifact), source_content_hash=source_document_hash(artifact),
                   line_basis='rawmd_global' if artifact.get('skill_source') else 'page_local')
    except (OSError, ValueError) as error:
        row['issue'] = str(error) if isinstance(error, ValueError) else 'SOURCE_UNAVAILABLE'
        warnings.append({'artifact_id': artifact['id'], 'reason': row['issue']})
    return row


def _write_conversation(folder, messages):
    """The conversation as markdown (split between messages into files of about HISTORY_FILE_CHARS)
    and as JSON; returns the markdown file names."""
    lines = []
    for i, m in enumerate(messages, 1):
        lines.append(f"## {i}. {m['role']} · {m.get('created_at', '')}\n\n{m.get('text', '')}\n")
        for ref in m.get('citations', []):
            lines.append(f"Citation: {ref.get('id', '')} · {ref.get('title', '')} · page {ref.get('page', '')} · source {ref.get('artifact_id', '')}\n")
    parts, buffer = [], ''
    for line in lines:
        if len(buffer) + len(line) > HISTORY_FILE_CHARS and buffer:
            parts.append(buffer)
            buffer = ''
        buffer += line + '\n'
    if buffer or not parts:
        parts.append(buffer)
    history = []
    for i, text in enumerate(parts, 1):
        name = 'conversation.md' if len(parts) == 1 else f'conversation-{i:03d}.md'
        (folder / name).write_text(text, encoding='utf8')
        history.append(name)
    (folder / 'conversation.json').write_text(packed(messages), encoding='utf8')
    return history
