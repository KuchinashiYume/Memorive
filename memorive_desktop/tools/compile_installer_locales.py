"""Compile locale-specific HTML and native catalog calls before packaging.

The delivered installer selects an immutable localized resource. It never scans
the rendered UI for Chinese strings or translates user data at runtime.
"""
from pathlib import Path
import base64
import hashlib
import json
import re

LANGUAGES=('zh-CN','en-US','ja-JP')
# Compatibility paths are identifiers, not UI copy. Their exact spellings stay
# fixed so uninstall can recognize old entries and protected data locations.
NATIVE_IDENTIFIERS={'Memorive 安装测试版.lnk','Memorive 测试版.lnk','维护 Memorive 测试版.lnk','维护 Memorive.lnk',
                    r'G:\Memorive-沙盒',r'G:\Memorive-运维'}


def compile_locales(installer: Path, output: Path, release: dict):
    catalog=json.loads((installer/'installer-catalog.json').read_text('utf8'))
    for key,row in catalog.items():
        if any(not isinstance(row.get(language),str) or not row[language] for language in LANGUAGES):
            raise ValueError('INSTALLER_TRANSLATION_MISSING:'+key)
    literals={row['source_token']:(key,row) for key,row in catalog.items() if 'source_token' in row}
    pattern=re.compile('|'.join(re.escape(key) for key in sorted(literals,key=len,reverse=True)))
    output.mkdir(parents=True,exist_ok=True)
    generated=[]
    for name in ('install.html','uninstall.html'):
        original=(installer/name).read_text('utf8')
        for index,label in enumerate(('简体中文','English','日本語')):
            original=original.replace('>'+label+'</button>','>__MEMORIVE_LANGUAGE_NAME_'+str(index)+'__</button>')
        # Source-token translation is confined to build inputs, never user data.
        for language in LANGUAGES:
            def replace(match):
                row=literals[match.group()][1]
                return row.get('source_token') if language=='zh-CN' else row[language].replace('\n',r'\n').replace('\r',r'\r')
            localized=pattern.sub(replace,original)
            for index,label in enumerate(('简体中文','English','日本語')):
                localized=localized.replace('__MEMORIVE_LANGUAGE_NAME_'+str(index)+'__',label)
            localized=re.sub(r'<html lang="[^"]+"', '<html lang="'+language+'"', localized, count=1)
            localized=localized.replace('v1.01',release['display_version'])
            scripts=re.findall(r'<script>(.*?)</script>',localized,re.S)
            if len(scripts)!=1:
                raise ValueError('INSTALLER_SCRIPT_SHAPE_CHANGED')
            digest=base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
            localized,count=re.subn(r"(script-src (?:'|&#x27;)sha256-)[A-Za-z0-9+/=]+",
                                    lambda match:match[1]+digest,localized)
            if count!=1:
                raise ValueError('INSTALLER_SCRIPT_POLICY_SHAPE_CHANGED')
            target=output/(Path(name).stem+'.'+language+'.html')
            target.write_text(localized,encoding='utf8',newline='\n');generated.append(target)
    # C# source literals are converted to catalog IDs at build time, while all
    # paths, immutable data and source files stay in the bound input manifest.
    expression=re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"')
    for name in ('installation_cross_setup.cs','lifecycle.cs','html_host.cs','prerequisite_wizard.cs'):
        def replace_native(match):
            token=match.group();raw=token[2:-1] if token.startswith('@') else token[1:-1]
            if not re.search(r'[\u3400-\u9fff]',raw) or raw in NATIVE_IDENTIFIERS:
                return token
            trimmed=raw.strip()
            if trimmed not in literals:
                raise ValueError('NATIVE_TRANSLATION_MISSING:'+name+':'+trimmed)
            key=literals[trimmed][0]
            prefix=raw[:len(raw)-len(raw.lstrip())];suffix=raw[len(raw.rstrip()):]
            return '('+json.dumps(prefix)+'+InstallerLocale.Text('+json.dumps(key)+')+'+json.dumps(suffix)+')'
        content=expression.sub(replace_native,(installer/name).read_text('utf8'))
        content=content.replace('v1.01',release['display_version'])
        target=output/name;target.write_text(content,encoding='utf8',newline='\n');generated.append(target)
    return generated
