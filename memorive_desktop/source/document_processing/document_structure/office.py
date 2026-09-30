"""Read OOXML structure without running macros, formulas or external links."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import posixpath, re, zipfile
import xml.etree.ElementTree as ET
from .contract import canonical,digest,file_sha,fail,structure_id

OFFICE_SCHEMA='MemoriveOfficeStructure-v1'
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
def tag(prefix,name):return '{'+NS[prefix]+'}'+name
def text(node,prefix):return ''.join(n.text or '' for n in node.iter(tag(prefix,'t')))
def word_text(node):
    return ''.join((n.text or '') if n.tag==tag('w','t') else '\t' if n.tag==tag('w','tab') else '\n'
                   for n in node.iter() if n.tag in {tag('w','t'),tag('w','tab'),tag('w','br'),tag('w','cr')})

class Archive:
    def __init__(self,path):
        if path.stat().st_size>128*1024*1024:fail('OFFICE_INPUT_LIMIT','Office file exceeds 128 MiB')
        self.z=zipfile.ZipFile(path); names=set(); total=0
        for i in self.z.infolist():
            if i.filename in names or i.filename.startswith('/') or '..' in PurePosixPath(i.filename).parts or '\\' in i.filename:
                self.z.close();fail('OFFICE_ARCHIVE_INVALID','Duplicate or unsafe archive path')
            names.add(i.filename);total+=i.file_size
            if i.flag_bits&1 or i.file_size>32*1024*1024 or total>256*1024*1024 or len(names)>10000:
                self.z.close();fail('OFFICE_ARCHIVE_LIMIT','Encrypted or oversized archive')
    def xml(self,name):
        raw=self.z.read(name)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():fail('OFFICE_XML_UNSAFE','Entity declarations rejected')
        return ET.fromstring(raw)
    def relations(self,part):
        name=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
        if name not in self.z.namelist():return {}
        result={}
        for n in self.xml(name):
            if n.get('TargetMode')=='External':continue
            target=posixpath.normpath(posixpath.join(posixpath.dirname(part),n.get('Target',''))).lstrip('/')
            if target.startswith('../'):fail('OFFICE_RELATION_INVALID','Relationship leaves package')
            if n.get('Id') in result:fail('OFFICE_RELATION_INVALID','Duplicate relationship ID')
            result[n.get('Id')]=target
        return result

def _xlsx(z):
    book=z.xml('xl/workbook.xml'); rel=z.relations('xl/workbook.xml')
    shared=[]
    if 'xl/sharedStrings.xml' in z.z.namelist():shared=[text(n,'s') for n in z.xml('xl/sharedStrings.xml')]
    formats={};styles=[0]
    if 'xl/styles.xml' in z.z.namelist():
        style_root=z.xml('xl/styles.xml')
        for n in style_root.findall('s:numFmts/s:numFmt',NS):
            formats[int(n.get('numFmtId'))]=n.get('formatCode','')
        styles=[int(n.get('numFmtId','0')) for n in style_root.findall('s:cellXfs/s:xf',NS)] or [0]
    blocks=[];units=[]
    for sheet in book.findall('s:sheets/s:sheet',NS):
        name=sheet.get('name');part=rel[sheet.get(tag('r','id'))];xml=z.xml(part)
        cells=[];seen=set()
        for c in xml.findall('s:sheetData/s:row/s:c',NS):
            addr=c.get('r','')
            if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}',addr) or addr in seen:fail('OFFICE_CELL_INVALID','Cell reference missing or duplicated')
            seen.add(addr); f=c.find('s:f',NS);v=c.find('s:v',NS);kind=c.get('t','n')
            raw=v.text if v is not None else None
            display=text(c,'s') if kind=='inlineStr' else raw
            if kind=='s':
                if raw is None or not raw.isdecimal() or int(raw)>=len(shared):fail('OFFICE_SHARED_STRING_INVALID','Invalid shared string index')
                display=shared[int(raw)]
            style=int(c.get('s','0'))
            if not 0<=style<len(styles):fail('OFFICE_STYLE_INVALID','Cell style index is out of range')
            number_format_id=styles[style]
            # Scientific review uses only literal, unformatted numeric cells.
            # Boolean/date/percentage-formatted values retain raw data, never
            # silently acquire the semantics of the underlying Excel serial.
            numeric_eligible = kind in {'s','inlineStr','str'} or (kind=='n' and number_format_id==0)
            letters=re.match('[A-Z]+',addr)[0];column=0
            for ch in letters:column=column*26+ord(ch)-64
            cells.append({'row':int(addr[len(letters):])-1,'column':column-1,'rowspan':1,'colspan':1,
                          'header':False,'text':display or '', 'address':addr,'cell_type':kind,
                          'raw_value':raw,'formula':f.text if f is not None else None,
                          'formula_attributes':dict(f.attrib) if f is not None else {},
                          'cached_value':raw if f is not None else None,'cached_value_present':f is not None and raw is not None,
                          'cache_element_present':f is not None and v is not None,
                          'style_index':c.get('s'),'number_format_id':number_format_id,
                          'number_format_code':formats.get(number_format_id),'numeric_review_eligible':numeric_eligible,
                          'source_location':{'kind':'sheet_cell','sheet':name,'cell':addr}})
            if len(cells)>200000:fail('OFFICE_CELL_LIMIT','Too many cells')
        merged=[n.get('ref') for n in xml.findall('s:mergeCells/s:mergeCell',NS)]
        table={'table_index':0,'cells':cells,'merged_ranges':merged,
               'rows':max((c['row']+1 for c in cells),default=0),'columns':max((c['column']+1 for c in cells),default=0)}
        units.append({'kind':'sheet','name':name,'state':sheet.get('state','visible'),'package_part':part})
        blocks.append({'block_type':'Sheet','text':'\n'.join(c['text'] for c in cells),'tables':[table],
                       'source_location':{'kind':'sheet','sheet':name,'part':part},'physical_page':None})
    return units,blocks,['FORMULAS_PRESERVED_NOT_CALCULATED','CACHE_MAY_BE_STALE','FORMATTED_DISPLAY_NOT_RECONSTRUCTED']

def _docx(z):
    body=z.xml('word/document.xml').find('w:body',NS);blocks=[]
    if body is None:fail('OFFICE_BODY_MISSING','DOCX body missing')
    for index,n in enumerate(body):
        if n.tag not in {tag('w','p'),tag('w','tbl')}:continue
        location={'kind':'docx_body','part':'word/document.xml','body_index':index}
        cells=[]
        if n.tag==tag('w','tbl'):
            if n.findall('.//w:tc/w:tbl',NS):fail('OFFICE_NESTED_TABLE_REVIEW_REQUIRED','Nested tables need explicit review')
            for row,tr in enumerate(n.findall('w:tr',NS)):
                column=0
                for tc in tr.findall('w:tc',NS):
                    span=tc.find('w:tcPr/w:gridSpan',NS);vm=tc.find('w:tcPr/w:vMerge',NS)
                    colspan=int(span.get(tag('w','val'),'1')) if span is not None else 1
                    if not 1<=colspan<=512:fail('OFFICE_TABLE_SPAN_INVALID','Invalid table span')
                    cells.append({'row':row,'column':column,'rowspan':1,'colspan':colspan,'header':False,
                                  'text':'\n'.join(word_text(p) for p in tc.findall('w:p',NS)),
                                  'vertical_merge':vm.get(tag('w','val'),'continue') if vm is not None else None,
                                  'source_location':dict(location,row=row,column=column)})
                    column+=colspan
        blocks.append({'block_type':'Table' if n.tag==tag('w','tbl') else 'Paragraph','text':word_text(n),
                       'math_xml':[ET.tostring(m,encoding='unicode') for m in n.iter() if m.tag=='{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath'],
                       'tables':[{'table_index':0,'cells':cells,'rows':len(n.findall('w:tr',NS)),
                                  'columns':max((c['column']+c['colspan'] for c in cells),default=0)}] if n.tag==tag('w','tbl') else [],
                       'source_location':location,'physical_page':None})
    return [{'kind':'document_body','name':'word/document.xml'}],blocks,['DOCX_PAGINATION_UNAVAILABLE','DOCX_VERTICAL_MERGE_RECORDED_NOT_INFERRED']

def _pptx(z):
    presentation=z.xml('ppt/presentation.xml');rel=z.relations('ppt/presentation.xml');blocks=[];units=[]
    for slide_index,ref in enumerate(presentation.findall('p:sldIdLst/p:sldId',NS),1):
        part=rel[ref.get(tag('r','id'))];xml=z.xml(part);units.append({'kind':'slide','number':slide_index,'package_part':part})
        tree=xml.find('p:cSld/p:spTree',NS)
        if tree is None:continue
        for index,shape in enumerate(tree):
            if shape.tag in {tag('p','nvGrpSpPr'),tag('p','grpSpPr')}:continue
            props=next(iter(shape.iter(tag('p','cNvPr'))),None)
            location={'kind':'slide_shape','slide':slide_index,'shape_index':index,
                      'shape_id':props.get('id') if props is not None else None,'part':part}
            tables=[]
            for ti,tbl in enumerate(shape.iter(tag('a','tbl'))):
                cells=[]
                for row,tr in enumerate(tbl.findall('a:tr',NS)):
                    for col,tc in enumerate(tr.findall('a:tc',NS)):
                        cells.append({'row':row,'column':col,'rowspan':int(tc.get('rowSpan','1')),
                                      'colspan':int(tc.get('gridSpan','1')),'header':False,'text':text(tc,'a'),
                                      'horizontal_merge':tc.get('hMerge'),'vertical_merge':tc.get('vMerge'),
                                      'source_location':dict(location,table=ti,row=row,column=col)})
                tables.append({'table_index':ti,'cells':cells,'rows':len(tbl.findall('a:tr',NS)),
                               'columns':max((c['column']+1 for c in cells),default=0)})
            transforms=[{'tag':n.tag.rsplit('}',1)[-1],**n.attrib} for n in shape.iter() if n.tag in {tag('a','off'),tag('a','ext'),tag('a','xfrm')}]
            blocks.append({'block_type':'SlideShape','text':text(shape,'a'),'tables':tables,
                           'source_location':location,'physical_page':None,'transforms_emu':transforms})
    return units,blocks,['SLIDE_ORDER_IS_PRESENTATION_ORDER','GROUP_TRANSFORMS_RETAINED_OVERLAY_NOT_CALIBRATED','NOTES_AND_CHART_VALUES_NOT_EXTRACTED']

def office_structure(source,rawmd):
    source,rawmd=Path(source),Path(rawmd);suffix=source.suffix.lower();parsers={'.xlsx':_xlsx,'.docx':_docx,'.pptx':_pptx}
    if suffix not in parsers:fail('OFFICE_FORMAT_UNSUPPORTED','Structure supports XLSX, DOCX and PPTX')
    z=Archive(source)
    try:units,blocks,warnings=parsers[suffix](z)
    finally:z.z.close()
    source_sha=file_sha(source)
    for i,b in enumerate(blocks):
        b.update(reading_order=i,block_id='Block-'+digest(canonical([source_sha,b['source_location']]))[:32])
    value={'schema_version':OFFICE_SCHEMA,'engine':'ooxml_structure','engine_version':'1',
           'source_sha256':source_sha,'rawmd_sha256':file_sha(rawmd),'units':units,'blocks':blocks,
           'review_status':'CANDIDATE_NOT_ADMITTED','warnings':warnings,
           'capabilities':{'structured_tables':True,'physical_pdf_pages':False,'executes_formulas':False,'external_links_fetched':False}}
    value['structure_id']=structure_id(value)
    return value
