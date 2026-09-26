"""Profile-local console consent. Missing/corrupt values fail closed."""
import json,os,uuid
from pathlib import Path
def permission_path(state_dir):
    root=Path(state_dir).resolve()
    path=root/'profile/ui/console_access_v1.json'
    if not path.resolve().is_relative_to(root):raise ValueError('CONSOLE_PERMISSION_PATH_INVALID')
    return path
def read_permission(state_dir):
    try:
        path=permission_path(state_dir)
        if path.stat().st_size>512:return False
        value=json.loads(path.read_text(encoding='utf8'))
        return (isinstance(value,dict) and set(value)=={'schema_version','enabled'}
                and value['schema_version']=='DesktopConsoleAccess-v1' and value['enabled'] is True)
    except (OSError,ValueError):return False
def write_permission(state_dir,enabled):
    if type(enabled) is not bool:raise ValueError('CONSOLE_ACCESS_BOOLEAN_REQUIRED')
    path=permission_path(state_dir);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.console_access-'+uuid.uuid4().hex+'.tmp')
    with tmp.open('x',encoding='utf8') as f:json.dump({'schema_version':'DesktopConsoleAccess-v1','enabled':enabled},f)
    os.replace(tmp,path)
