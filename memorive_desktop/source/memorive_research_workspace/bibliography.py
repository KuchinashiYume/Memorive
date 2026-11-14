"""Bounded, non-executing bibliographic interchange readers.

Records contain metadata and attachment suggestions, never a download command.
Unsupported BibTeX macros/concatenation are reported instead of guessed.
"""
import re
import xml.etree.ElementTree as ET
from .store import digest

def record(fields, key='', warnings=None):
    title=str(fields.get('title','')).strip()
    if not title:raise ValueError('BIBLIOGRAPHY_TITLE_MISSING')
    return {'source_key':key or digest(fields)[:24], 'title':title[:1000],
        'authors':fields.get('authors',[])[:100], 'year':str(fields.get('year',''))[:40],
        'doi':str(fields.get('doi','')).strip()[:300], 'abstract':str(fields.get('abstract',''))[:40000],
        'attachment_suggestions':fields.get('files',[])[:20], 'warnings':warnings or []}

def ris(text):
    rows=[];fields={};last=None
    def emit():
        if not fields:return
        val=lambda *keys:next((fields[k][0] for k in keys if fields.get(k)),'')
        rows.append(record({'title':val('TI','Initialization','CT'),'authors':fields.get('AU',[])+fields.get('A1',[]),
            'year':val('PY','Y1','DA'),'doi':val('DO'),'abstract':val('AB','N2'),
            'files':fields.get('L1',[])+fields.get('L4',[])},val('ID')))
    for line in text.splitlines():
        m=re.match(r'^([A-Z0-9]{2})\s{2}-\s?(.*)$',line)
        if m:
            key,value=m.groups()
            if key=='TY':
                if fields:emit();fields={}
            if key=='ER':emit();fields={};last=None;continue
            fields.setdefault(key,[]).append(value);last=key
        elif line.strip() and last:fields[last][-1]+='\n'+line.strip()
    emit();return rows

def bibtex(text):
    rows=[];pos=0
    while True:
        match=re.search(r'@([\w-]+)\s*([\{(])',text[pos:])
        if not match:break
        kind=match.group(1).lower();start=pos+match.end();close='}' if match.group(2)=='{' else ')'
        depth=1;quote=False;escape=False;i=start
        while i<len(text) and depth:
            ch=text[i]
            if escape:escape=False
            elif ch=='\\':escape=True
            elif ch=='"' and depth==1:quote=not quote
            elif not quote:
                if ch==match.group(2):depth+=1
                elif ch==close:depth-=1
            i+=1
        if depth:raise ValueError('BIBTEX_UNBALANCED_ENTRY')
        body=text[start:i-1];pos=i
        if kind in {'comment','preamble'}:continue
        if kind=='string':raise ValueError('BIBTEX_MACROS_UNSUPPORTED_EXPORT_RIS')
        if ',' not in body:raise ValueError('BIBTEX_FIELDS_MISSING')
        key,body=body.split(',',1);fields={};j=0
        while j<len(body):
            while j<len(body) and (body[j].isspace() or body[j]==','):j+=1
            if j==len(body):break
            m=re.match(r'([\w-]+)\s*=\s*',body[j:])
            if not m:raise ValueError('BIBTEX_FIELD_INVALID')
            name=m.group(1).lower();j+=m.end();begin=j
            if j<len(body) and body[j] in '{"':
                opener=body[j];end='}' if opener=='{' else '"';j+=1;begin=j;d=1;esc=False
                while j<len(body):
                    ch=body[j]
                    if esc:esc=False
                    elif ch=='\\':esc=True
                    elif opener=='{' and ch=='{':d+=1
                    elif ch==end:
                        d-=1
                        if d==0:break
                    j+=1
                if d:raise ValueError('BIBTEX_UNBALANCED_FIELD')
                value=body[begin:j];j+=1
            else:
                while j<len(body) and body[j]!=',':j+=1
                value=body[begin:j].strip()
                if value and not re.fullmatch(r'[0-9]+',value):raise ValueError('BIBTEX_MACROS_UNSUPPORTED_EXPORT_RIS')
            while j<len(body) and body[j].isspace():j+=1
            if j<len(body) and body[j]=='#':raise ValueError('BIBTEX_CONCATENATION_UNSUPPORTED_EXPORT_RIS')
            fields[name]=value
        files=[]
        for value in fields.get('file','').split(';'):
            if not value.strip():continue
            # Common JabRef/Zotero attachment encoding: label:path:PDF.
            value=re.sub(r':(?:PDF|application/pdf)$','',value.strip(),flags=re.I)
            value=re.sub(r'^[^:/\\]*:(?=[A-Za-z]:[\\/]|/)', '',value)
            files.append(value)
        rows.append(record({'title':fields.get('title',''),'authors':re.split(r'\s+and\s+',fields.get('author','')),
            'year':fields.get('year',''),'doi':fields.get('doi',''),'abstract':fields.get('abstract',''),'files':files},key.strip()))
    return rows

def endnote_xml(text):
    if re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise ValueError('XML_DTD_FORBIDDEN')
    root=ET.fromstring(text);rows=[]
    def value(node,path):
        e=node.find(path);return ''.join(e.itertext()).strip() if e is not None else ''
    for node in root.findall('.//record'):
        rows.append(record({'title':value(node,'titles/title'),
            'authors':[''.join(e.itertext()) for e in node.findall('contributors/authors/author')],
            'year':value(node,'dates/year'),'doi':value(node,'electronic-resource-num'),
            'abstract':value(node,'abstract'),'files':[''.join(e.itertext()) for e in node.findall('urls/pdf-urls/url')]},value(node,'rec-number')))
    return rows

def parse(path):
    if path.stat().st_size>8*1024*1024:raise ValueError('BIBLIOGRAPHY_TOO_LARGE')
    text=path.read_text(encoding='utf-8-sig')
    reader={'.ris':ris,'.bib':bibtex,'.bibtex':bibtex,'.xml':endnote_xml}.get(path.suffix.lower())
    if reader is None:raise ValueError('BIBLIOGRAPHY_FORMAT_UNSUPPORTED')
    rows=reader(text)
    if not rows or len(rows)>2000:raise ValueError('BIBLIOGRAPHY_RECORD_COUNT_INVALID')
    keys=[r['source_key'] for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError('BIBLIOGRAPHY_DUPLICATE_RECORD_KEY')
    return rows
