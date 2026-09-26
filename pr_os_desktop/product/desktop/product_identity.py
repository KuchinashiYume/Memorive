"""Display identity and verified profile selection; legacy data identities stay fixed."""
import hashlib,json,sys,os,re,shutil,uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
def load():
    binding=json.loads((ROOT/'release_identity_binding.json').read_text(encoding='utf-8'))
    raw=(ROOT/'brand_profile.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=binding['profile_sha256']:raise ValueError('BRAND_PROFILE_HASH_MISMATCH')
    profile=json.loads(raw)
    if profile['document_id']!=binding['profile_document_id'] or profile['revision']!=binding['profile_revision']:raise ValueError('BRAND_PROFILE_IDENTITY_MISMATCH')
    return profile['product'],binding

PRODUCT,BINDING=load()
BRAND=PRODUCT['name']

INSTALL_OWNER='PR-OS-INSTALLER-TRIAL-V1'
INSTALL_CONTRACT='p08-desktop-review-v1'
PRIVATE_PROFILE_SCHEMA=1


def _atomic_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.next')
    if temporary.exists():
        raise FileExistsError('PRIVATE_TEST_PROFILE_MANIFEST_TEMP_COLLISION')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
    temporary.replace(path)


def _private_profile_build_id(value):
    if not isinstance(value,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',value) is None:
        raise ValueError('PRIVATE_TEST_PROFILE_BUILD_ID_INVALID')
    return value


def _read_private_profile_identity(state_root,expected_build=None):
    state_root=_owned_path(state_root)
    manifest=state_root/'profile'/'profile_identity.json'
    if not manifest.is_file():
        raise ValueError('PRIVATE_TEST_PROFILE_IDENTITY_MISSING')
    value=_binding_json(manifest)
    required={
        'profile_instance_id','created_by_build','last_opened_by_build',
        'data_schema_version','channel','migration_revision','migration_history',
    }
    if not required.issubset(value) or value.get('channel')!='PRIVATE_TEST':
        raise ValueError('PRIVATE_TEST_PROFILE_IDENTITY_INVALID')
    try:uuid.UUID(str(value['profile_instance_id']))
    except (ValueError,AttributeError) as error:raise ValueError('PRIVATE_TEST_PROFILE_INSTANCE_ID_INVALID') from error
    schema=value.get('data_schema_version')
    if not isinstance(schema,int) or isinstance(schema,bool) or schema<1:
        raise ValueError('PRIVATE_TEST_PROFILE_SCHEMA_INVALID')
    if schema>PRIVATE_PROFILE_SCHEMA:
        raise ValueError('PRIVATE_TEST_PROFILE_SCHEMA_NEWER_THAN_RUNTIME')
    if schema<PRIVATE_PROFILE_SCHEMA:
        raise ValueError('PRIVATE_TEST_PROFILE_SCHEMA_MIGRATION_REQUIRED')
    if expected_build is not None and value.get('created_by_build')!=expected_build:
        raise ValueError('PRIVATE_TEST_PROFILE_BUILD_OWNERSHIP_MISMATCH')
    if not isinstance(value.get('migration_revision'),int) or not isinstance(value.get('migration_history'),list):
        raise ValueError('PRIVATE_TEST_PROFILE_MIGRATION_HISTORY_INVALID')
    return value


def _private_profile_tree_safe(root):
    root=_owned_path(root)
    if not root.is_dir():raise NotADirectoryError(root)
    reparse_mask=0x400
    for path in (root,*root.rglob('*')):
        info=path.lstat()
        if path.is_symlink() or getattr(info,'st_file_attributes',0)&reparse_mask:
            raise ValueError('PRIVATE_TEST_PROFILE_REPARSE_POINT_BLOCKED')


def select_private_test_state(data_root,build_id,*,migrate_from=None,reset_current=False):
    """Select one build-owned private-test state before any service starts.

    A new build always gets a clean state.  Importing an older private-test
    state or clearing the current one requires an explicit launch option and
    creates a complete backup before the target is changed.
    """
    data_root=_owned_path(data_root);build_id=_private_profile_build_id(build_id)
    profiles=_owned_path(data_root/'state'/'private-profiles')
    profiles.mkdir(parents=True,exist_ok=True)
    target=_owned_path(profiles/build_id)
    backups=_owned_path(data_root/'state'/'private-profile-backups')
    mode='EXISTING' if target.exists() else 'NEW'
    backup_root=None
    prior_identity=None
    if migrate_from is not None:
        source_build=_private_profile_build_id(migrate_from)
        if source_build==build_id:raise ValueError('PRIVATE_TEST_PROFILE_MIGRATION_SELF_FORBIDDEN')
        if target.exists():raise FileExistsError('PRIVATE_TEST_PROFILE_MIGRATION_TARGET_EXISTS')
        source=_owned_path(profiles/source_build)
        prior_identity=_read_private_profile_identity(source,source_build)
        _private_profile_tree_safe(source)
        backup_root=_owned_path(backups/source_build/str(uuid.uuid4()))
        backup_root.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(source,backup_root)
        shutil.copytree(source,target)
        mode='EXPLICIT_MIGRATION'
    elif reset_current and target.exists():
        prior_identity=_read_private_profile_identity(target,build_id)
        _private_profile_tree_safe(target)
        backup_root=_owned_path(backups/build_id/str(uuid.uuid4()))
        backup_root.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(target,backup_root)
        shutil.rmtree(target)
        mode='EXPLICIT_RESET'
    target.mkdir(parents=True,exist_ok=True)
    manifest=target/'profile'/'profile_identity.json'
    now=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    if manifest.is_file() and mode=='EXISTING':
        identity=_read_private_profile_identity(target,build_id)
        identity['last_opened_by_build']=build_id
        identity['last_opened_at']=now
    else:
        history=[]
        revision=0
        if prior_identity is not None:
            history=list(prior_identity.get('migration_history') or [])
            history.append({
                'action':mode,
                'source_build':str(prior_identity.get('created_by_build') or build_id),
                'source_profile_instance_id':prior_identity['profile_instance_id'],
                'backup_root':str(backup_root),
                'completed_at':now,
            })
            revision=int(prior_identity.get('migration_revision') or 0)+1
        identity={
            'profile_instance_id':str(uuid.uuid4()),
            'created_by_build':build_id,
            'last_opened_by_build':build_id,
            'created_at':now,
            'last_opened_at':now,
            'data_schema_version':PRIVATE_PROFILE_SCHEMA,
            'channel':'PRIVATE_TEST',
            'migration_revision':revision,
            'migration_history':history,
        }
    _atomic_json(manifest,identity)
    receipt={
        'schema_version':'MemorivePrivateTestProfileSelection-v1',
        'status':'READY','selection_mode':mode,'build_id':build_id,
        'profile_instance_id':identity['profile_instance_id'],
        'data_schema_version':PRIVATE_PROFILE_SCHEMA,
        'state_root':str(target),'backup_root':str(backup_root) if backup_root else None,
    }
    _atomic_json(data_root/'state'/'private-profile-selection.json',receipt)
    return target,receipt

def _owned_path(value):
    if not isinstance(value,(str,Path)) or not str(value):
        raise ValueError('INSTALL_PROFILE_PATH_INVALID')
    path=Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\','//')):
        raise ValueError('INSTALL_PROFILE_LOCAL_ABSOLUTE_PATH_REQUIRED')
    for part in (path,*path.parents):
        if part.exists() or part.is_symlink():
            info=part.lstat()
            if part.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('INSTALL_PROFILE_REPARSE_POINT_BLOCKED')
    path=path.resolve()
    if path.parent==path:
        raise ValueError('INSTALL_PROFILE_VOLUME_ROOT_FORBIDDEN')
    return path

def _binding_json(path):
    path=_owned_path(path)
    if not path.is_file() or path.stat().st_size>4*1024*1024:
        raise ValueError('INSTALL_PROFILE_BINDING_FILE_REQUIRED')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):
        raise ValueError('INSTALL_PROFILE_BINDING_OBJECT_REQUIRED')
    return value

def _same_path(value,path):
    return os.path.normcase(str(_owned_path(value)))==os.path.normcase(str(path))

def _webview_owner_present(webview,data):
    marker=webview/'data-owner.json'
    if not webview.exists():return False
    if not webview.is_dir():raise ValueError('INSTALL_PROFILE_WEBVIEW_DIRECTORY_REQUIRED')
    if not marker.exists():
        if any(webview.iterdir()):raise ValueError('INSTALL_PROFILE_WEBVIEW_UNOWNED_CONTENT')
        return False
    owner=_binding_json(marker)
    if (owner.get('owner')!=INSTALL_OWNER or owner.get('role')!='WEBVIEW2_PROFILE'
        or not _same_path(owner.get('data_root'),data)):
        raise ValueError('INSTALL_PROFILE_WEBVIEW_OWNER_MISMATCH')
    return True

def _restore_webview_cache_owner(webview,data):
    # private_mode removes browser storage on normal exit. Ownership remains
    # bound by the durable installation records, verified before this call.
    if _webview_owner_present(webview,data):return
    webview.mkdir(parents=True,exist_ok=True)
    _owned_path(webview)
    if _webview_owner_present(webview,data):return
    marker=webview/'data-owner.json'
    try:
        with marker.open('x',encoding='utf-8') as stream:
            json.dump({'owner':INSTALL_OWNER,'data_root':str(data),'role':'WEBVIEW2_PROFILE'},stream,ensure_ascii=False)
    except FileExistsError:
        if not _webview_owner_present(webview,data):raise ValueError('INSTALL_PROFILE_WEBVIEW_OWNER_MISMATCH')

def installed_environment(executable,environ):
    """Consume the existing installer ownership contract, also on direct launch."""
    original=Path(executable)
    if original.parent.name.lower()!='app' or original.parent.parent.parent.name.lower()!='versions':
        return None
    exe=_owned_path(original)
    version=exe.parent.parent;program=version.parent.parent
    owner=_binding_json(program/'install-owner.json')
    current=_binding_json(program/'current.json')
    state=_binding_json(version/'version-state.json')
    manifest=_binding_json(version/'manifest.json')
    if owner.get('owner')!=INSTALL_OWNER or not _same_path(owner.get('root'),program):
        raise ValueError('INSTALL_PROFILE_PROGRAM_OWNER_MISMATCH')
    for binding in (current,state):
        if binding.get('schema')!=1 or binding.get('owner')!=INSTALL_OWNER or binding.get('contract')!=INSTALL_CONTRACT:
            raise ValueError('INSTALL_PROFILE_CONTRACT_MISMATCH')
        if not _same_path(binding.get('program_root'),program) or not _same_path(binding.get('version'),version):
            raise ValueError('INSTALL_PROFILE_ACTIVE_VERSION_MISMATCH')
        if binding.get('build_id')!=BINDING['build_id']:
            raise ValueError('INSTALL_PROFILE_BUILD_MISMATCH')
    data=_owned_path(current.get('data_root'))
    webview=_owned_path(current.get('webview_root'))
    if data==program or data.is_relative_to(program) or program.is_relative_to(data):
        raise ValueError('INSTALL_PROFILE_PROGRAM_DATA_OVERLAP')
    if not _same_path(state.get('data_root'),data) or not _same_path(state.get('webview_root'),webview):
        raise ValueError('INSTALL_PROFILE_VERSION_DATA_MISMATCH')
    data_owner=_binding_json(data/'data-owner.json')
    if data_owner.get('owner')!=INSTALL_OWNER or not _same_path(data_owner.get('data_root'),data):
        raise ValueError('INSTALL_PROFILE_DATA_OWNER_MISMATCH')
    # The installer binds the cache leaf to its recorded path spelling.  A
    # SUBST drive or an 8.3 alias resolves to the same owned directory but has
    # a different spelling.  Validate physical ownership above, then replay
    # the stored spelling rather than minting a different profile identity.
    recorded_data=os.path.abspath(str(current['data_root'])).rstrip('\\/')
    expected_wv=hashlib.sha256(recorded_data.upper().encode('utf-8')).hexdigest()[:16].upper()
    if len(str(webview).encode('utf-16-le'))//2>96 or webview.name.upper()!=expected_wv:
        raise ValueError('INSTALL_PROFILE_WEBVIEW_PATH_MISMATCH')
    if webview==program or webview.is_relative_to(program) or program.is_relative_to(webview):
        raise ValueError('INSTALL_PROFILE_WEBVIEW_PROGRAM_OVERLAP')
    _webview_owner_present(webview,data)
    if (manifest.get('schema')!=1 or manifest.get('contract')!=INSTALL_CONTRACT
        or manifest.get('build_id')!=BINDING['build_id'] or manifest.get('app_exe')!=exe.name
        or exe.name!=BINDING['main_executable'] or not isinstance(manifest.get('members'),list)):
        raise ValueError('INSTALL_PROFILE_MANIFEST_MISMATCH')
    members=[row for row in manifest['members'] if isinstance(row,dict) and row.get('path')=='app/'+exe.name]
    if len(members)!=1 or not exe.is_file() or members[0].get('size')!=exe.stat().st_size:
        raise ValueError('INSTALL_PROFILE_EXECUTABLE_BINDING_MISMATCH')
    with exe.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if str(members[0].get('sha256','')).lower()!=digest:
        raise ValueError('INSTALL_PROFILE_EXECUTABLE_HASH_MISMATCH')
    private_hash=BINDING.get('private_test_capsule_sha256')
    if private_hash:
        if (not isinstance(private_hash,str) or re.fullmatch(r'[0-9a-f]{64}',private_hash) is None
            or manifest.get('private_test_only') is not True
            or manifest.get('contains_authorized_private_capsule') is not True
            or manifest.get('private_test_capsule_sha256')!=private_hash):
            raise ValueError('INSTALL_PROFILE_PRIVATE_CAPSULE_CONTRACT_MISMATCH')
        private_members=[row for row in manifest['members'] if isinstance(row,dict) and row.get('path')=='app/private-test/manifest.json']
        private_manifest=exe.parent/'private-test'/'manifest.json'
        if (len(private_members)!=1 or not private_manifest.is_file()
            or hashlib.sha256(private_manifest.read_bytes()).hexdigest()!=private_hash
            or str(private_members[0].get('sha256','')).lower()!=private_hash):
            raise ValueError('INSTALL_PROFILE_PRIVATE_CAPSULE_MISMATCH')
    # Check every directory before creating any; a broken contract cannot fall
    # back to a fresh, unrelated profile. Credentials are left in their family.
    paths={name:_owned_path(data/child) for name,child in {
        'APPDATA':'appdata','LOCALAPPDATA':'localappdata','TEMP':'temp','TMP':'temp',
        'PROS_WORKSPACE_ROOT':'workspace'}.items()}
    _restore_webview_cache_owner(webview,data)
    for path in paths.values():path.mkdir(parents=True,exist_ok=True)
    _clear_inherited_runtime_paths(environ)
    environ.update({name:str(path) for name,path in paths.items()})
    environ['PR_OS_WEBVIEW2_STORAGE_PATH']=str(webview)
    return data

def _clear_inherited_runtime_paths(environ):
    for name in ('PROS_VAULT_ROOT','PROS_M1_VAULT_ROOT','PROS_M1_SANDBOX_ROOT','PROS_OPS_ROOT','PROS_M1_CHROMA_ROOT',
        'PROS_M1_SANDBOX_CHROMA_DIR','PROS_M9_CACHE_DIR','PROS_M11_LOG_DIR','PROS_M6_REPORT_DIR','PROS_M6_REPAIR_DIR',
        'PROS_M6_JUDGMENT_DEBT_DIR','PROS_M8_LOG_DIR','PROS_M3_RERANK_ENABLED','PROS_M1_OCR_VIA_M9_READY'):
        environ.pop(name,None)

def sandbox_environment(executable,environ,profile_namespace=None):
    """Keep portable data adjacent; isolate hash-bound private test profiles."""
    installed=installed_environment(executable,environ)
    if installed is not None:
        if profile_namespace is not None:
            private_hash=BINDING.get('private_test_capsule_sha256')
            if not isinstance(private_hash,str) or profile_namespace!=private_hash[:16]:
                raise ValueError('PRIVATE_TEST_INSTALL_PROFILE_NAMESPACE_MISMATCH')
        return installed
    base=Path(executable).resolve().parent/'sandbox_profile'
    if profile_namespace is not None:
        if (not isinstance(profile_namespace,str) or len(profile_namespace)!=16
            or any(character not in '0123456789abcdef' for character in profile_namespace)):
            raise ValueError('SANDBOX_PROFILE_NAMESPACE_INVALID')
        base=base.with_name(base.name+'_'+profile_namespace)
    original_temp=Path(environ.get('TEMP') or environ.get('TMP') or str(base/'temp'))
    for name,child in {'APPDATA':'appdata','LOCALAPPDATA':'localappdata','TEMP':'temp','TMP':'temp',
        'PROS_WORKSPACE_ROOT':'workspace'}.items():
        path=base/child;path.mkdir(parents=True,exist_ok=True);environ[name]=str(path)
    # The existing executor derives these from the isolated profile and validates them.
    _clear_inherited_runtime_paths(environ)
    short=original_temp/('memo113-wv-'+hashlib.sha256(str(base).encode()).hexdigest()[:10])
    short.mkdir(parents=True,exist_ok=True);environ['PR_OS_WEBVIEW2_STORAGE_PATH']=str(short)
    return base
