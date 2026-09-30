"""Copy selected settings into a new profile without reading the credential vault."""
from pathlib import Path
import argparse,hashlib,json,shutil,sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
from memorive_settings.contracts import canonical_sha256,scan_sensitive,validate_settings,STORE_SCHEMA_VERSION

SETTINGS_FILES=(
 'profile/settings/settings.json',
 'profile/settings/user_preferences.json',
 'profile/settings/research_preferences.json',
 'profile/settings/leaderboard_preferences.json',
 'profile/settings/external_data/external_sources.json',
 'profile/settings/external_data/refresh_schedule.json',
 'profile/ui/assistant_preferences_v3.json',
 'local_models/local_models_v1.json',
)

# Bind the two historical schemas by digest so obsolete product identifiers
# remain migration input rather than names used by the current application.
SCHEMA_MIGRATIONS={
 'local_models/local_models_v1.json':('b757649efb1855ad1b7f2cd2431f332e4ad485adeeebf07826527b19f02fff9c','DesktopLocalModelState-v1'),
 'profile/ui/assistant_preferences_v3.json':('1e75737d12dc4beb4d2915f966efaa0af26393cfa562e2465d63b8d931a1b4b8','DesktopAssistantPreferences-v3'),
}

def prepare_import_directories(settings, source:Path, destination:Path):
 """Rebase profile-owned defaults, preserve custom paths and report availability.

 A settings-only import never moves documents or creates an old custom path.
 """
 from copy import deepcopy
 settings=deepcopy(settings);changes=[];warnings=[];create=[]
 for role,folder in [('workspace_root','workspace'),('artifact_root','artifacts')]:
  original=settings['directories'][role];path=Path(original)
  own_default=(source/'profile'/folder).resolve(strict=False)
  resolved=path.resolve(strict=False)
  # Recognize only the exact old portable-profile default layout, and only if
  # it no longer exists. An existing external directory is never remapped.
  parts=[part.lower() for part in path.parts]
  legacy_default=(len(parts)>=5 and parts[-5:-3]==['state','private-profiles']
                  and parts[-2:]==['profile',folder])
  if resolved==own_default or (not path.exists() and legacy_default):
   target=destination/'profile'/folder
   settings['directories'][role]=str(target)
   changes.append({'role':role,'old':original,'new':str(target),'reason':'PROFILE_DEFAULT_REBASED','documents_moved':False})
   create.append(target)
  elif not path.is_dir():
   warnings.append({'role':role,'path':original,'reason':'CUSTOM_DIRECTORY_MISSING','action':'RESELECT_DIRECTORY','preserved':True})
 external=settings['directories']['external_library']
 if external['enabled'] and not Path(external['root']).is_dir():
  warnings.append({'role':'external_library','path':external['root'],'reason':'EXTERNAL_LIBRARY_MISSING','action':'RESELECT_DIRECTORY','preserved':True})
 return settings,changes,warnings,create

def import_settings(source:Path,destination:Path):
 source=source.resolve(strict=True);destination=destination.resolve()
 if not source.is_dir() or destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
  raise ValueError('NEW_SEPARATE_DESTINATION_REQUIRED')
 inputs=[];migrations=[];directory_changes=[];directory_warnings=[];directories_to_create=[]
 for relative in SETTINGS_FILES:
  path=source/relative
  if not path.exists():continue
  if path.is_symlink() or not path.resolve().is_relative_to(source):raise ValueError('SETTINGS_LINK_REJECTED')
  raw=path.read_bytes();value=json.loads(raw.decode('utf-8-sig'));scan_sensitive(value)
  if relative in SCHEMA_MIGRATIONS:
   old_digest,current_schema=SCHEMA_MIGRATIONS[relative]
   schema=value.get('schema_version')
   if not isinstance(schema,str):raise ValueError('SETTINGS_SCHEMA_REQUIRED')
   if schema!=current_schema:
    if hashlib.sha256(schema.encode()).hexdigest()!=old_digest:raise ValueError('SETTINGS_SCHEMA_UNSUPPORTED')
    value['schema_version']=current_schema
    migrations.append({'path':relative,'source_sha256':hashlib.sha256(raw).hexdigest(),'schema_version':current_schema})
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')
   if relative.startswith('local_models/'):
    from memorive_local_models.product_controller import LocalModelProductController
    LocalModelProductController._validate_state(value)
   elif set(value)!={'schema_version','enabled','shortcut_entries','reminder_scope','hide_sensitive_names'}:
    raise ValueError('ASSISTANT_SETTINGS_FIELDS_INVALID')
  if relative==SETTINGS_FILES[0]:
   if value.get('schema_version')!=STORE_SCHEMA_VERSION:raise ValueError('SETTINGS_VERSION_UNSUPPORTED')
   settings=validate_settings(value['settings'])
   if value.get('settings_sha256')!=canonical_sha256(settings):raise ValueError('SETTINGS_CHECKSUM_MISMATCH')
   settings,directory_changes,directory_warnings,directories_to_create=prepare_import_directories(settings,source,destination)
   if directory_changes:
    value['settings']=validate_settings(settings)
    value['settings_sha256']=canonical_sha256(value['settings'])
    value['revision']=int(value['revision'])+1
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')
  inputs.append((relative,raw))
 if not any(p==SETTINGS_FILES[0] for p,_ in inputs):raise ValueError('MAIN_SETTINGS_REQUIRED')
 destination.mkdir(parents=True,exist_ok=False)
 for directory in directories_to_create:
  if not directory.resolve().is_relative_to(destination):raise ValueError("IMPORT_DIRECTORY_ESCAPE")
  directory.mkdir(parents=True,exist_ok=True)
 files=[]
 for relative,raw in inputs:
  target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as stream:stream.write(raw)
  files.append({'path':relative,'sha256':hashlib.sha256(raw).hexdigest()})
 receipt={'schema':'MemoriveSettingsImport-v1','files':files,'schema_migrations':migrations,'directory_changes':directory_changes,'directory_warnings':directory_warnings,'directory_preflight':'WARNING' if directory_warnings else 'PASS','credential_values_read':0,'credential_values_copied':0,'credential_references_preserved':True,'conversations_or_documents_copied':False}
 (destination/'settings-import.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 return receipt

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--source-profile',required=True,type=Path)
 parser.add_argument('--new-profile',required=True,type=Path)
 args=parser.parse_args();result=import_settings(args.source_profile,args.new_profile)
 print(json.dumps({'status':'IMPORTED','files':len(result['files']),'credential_values_read':0,'conversations_or_documents_copied':False}))
