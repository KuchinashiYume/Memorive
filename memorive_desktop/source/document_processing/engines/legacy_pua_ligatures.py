"""Restore audited private-use ligatures using embedded font and glyph evidence.

No global PUA substitution: every occurrence on a page must carry the same
verified font-program / Unicode / glyph-id binding. Unknown fonts are preserved.
"""
from __future__ import annotations
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# PDF source crops and font program hashes: research-context failure_diagnostics123.json
# and b2p3_font_probe123.json. Glyphs visually verified as fl, fi and ft.
_RULES = {
    'da298a88d00c01b5a832ef4cd325a7a55a214d18f35453b8270c02d426038c70': { (0xE104,1):'fl', (0xE103,2):'fi' },
    '439965efb175e5de34f29a1fd7b85657e1c9d6bcce69b9f5439aeae02e5a9190': { (0xE103,1):'fi' },
    'b18f3206ecf66de20b8940475e6a3c7a07660bf3e16328e0a1e5635341a30238': { (0xE09D,1):'ft' },
    'bea4e72412b5a81d11a31f8b89011c8fcbe1e01ac8015022f1bb927a80eee823': { (0xE09D,1):'ft' },
    '29786f448a2ea0ea661c285fbe287877c6500d773cb3620377c33a548823e936': { (0xE104,1):'fl' },
}

@dataclass(frozen=True)
class PuaLigatureRecovery:
    pages: list[dict]
    recovered_chars: int
    font_sha256s: tuple[str,...]

def recover_pua_ligatures(path: Path, pages: list[dict]) -> PuaLigatureRecovery | None:
    if not any(any(0xE000 <= ord(c) <= 0xF8FF for c in p.get('text','')) for p in pages):
        return None
    import pymupdf
    result=[];total=0;used=set()
    with pymupdf.open(path) as doc:
        if len(doc)!=len(pages): raise ValueError('PUA_RECOVERY_PAGE_COUNT_MISMATCH')
        for page, markdown in zip(doc,pages):
            fonts=defaultdict(set)
            for row in page.get_fonts(full=True):
                name=row[3];program=doc.extract_font(row[0])[3]
                digest=hashlib.sha256(program).hexdigest()
                fonts[name].add(digest)
                if len(name)>7 and name[6]=='+': fonts[name[7:]].add(digest)
            bindings=defaultdict(set);counts=Counter()
            for span in page.get_texttrace():
                hashes=fonts.get(span['font'],set())
                for cp,gid,*_ in span['chars']:
                    if not 0xE000<=cp<=0xF8FF:continue
                    replacement=None
                    if len(hashes)==1:
                        digest=next(iter(hashes));rules=_RULES.get(digest)
                        if rules is not None:
                            if (cp,gid) not in rules:raise ValueError('PUA_RECOVERY_UNKNOWN_GLYPH')
                            replacement=rules[cp,gid];used.add(digest)
                    bindings[chr(cp)].add(replacement);counts[chr(cp)]+=1
            text=markdown.get('text','')
            for char,values in bindings.items():
                if values=={None}:continue
                if len(values)!=1 or None in values:raise ValueError('PUA_RECOVERY_AMBIGUOUS_FONT')
                count=text.count(char)
                if count>counts[char]:raise ValueError('PUA_RECOVERY_COUNT_MISMATCH')
                text=text.replace(char,next(iter(values)));total+=count
            result.append({**markdown,'text':text})
    return PuaLigatureRecovery(result,total,tuple(sorted(used))) if total else None
