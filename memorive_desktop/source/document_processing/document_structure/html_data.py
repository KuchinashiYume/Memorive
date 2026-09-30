"""Parse HTML as inert evidence; preserve source HTML and never load assets."""
from html.parser import HTMLParser

from .contract import fail


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "p", "div", "tr", "pre"}:
            self.parts.append("\n")
        elif not self.hidden and tag in {"td", "th"}:
            self.parts.append("\t")
        elif not self.hidden and tag in {"sup", "sub"}:
            self.parts.append("^{" if tag == "sup" else "_{")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "tr", "pre"}:
            self.parts.append("\n")
        elif not self.hidden and tag in {"sup", "sub"}:
            self.parts.append("}")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "".join(parser.parts).strip()


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = None
        self.cell = None
        self.in_row = False
        self.row = -1
        self.column = 0
        self.occupied = set()
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag == "table":
            if self.table is not None:
                fail("STRUCTURE_NESTED_TABLE_UNSUPPORTED", "Nested tables require separate review")
            self.table = {"table_index": len(self.tables), "cells": []}
            self.row, self.column = -1, 0
            self.occupied = set()
        elif tag == "tr" and self.table is not None:
            if self.in_row or self.cell is not None:
                fail("STRUCTURE_TABLE_INVALID", "Unclosed table row/cell")
            self.row += 1
            if self.row >= 4096:
                fail("STRUCTURE_TABLE_TOO_LARGE", "Too many table rows")
            self.column, self.in_row = 0, True
        elif tag in {"td", "th"} and self.table is not None:
            if not self.in_row or self.cell is not None:
                fail("STRUCTURE_TABLE_INVALID", "Cell outside row or unclosed cell")
            attributes = dict(attrs)
            spans = []
            for key, maximum in (("rowspan", 4096), ("colspan", 512)):
                value = attributes.get(key, "1")
                if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 4:
                    fail("STRUCTURE_TABLE_SPAN_INVALID", "Table spans must be bounded positive integers")
                n = int(value)
                if not 1 <= n <= maximum:
                    fail("STRUCTURE_TABLE_SPAN_INVALID", "Table span exceeds bounds")
                spans.append(n)
            rows, cols = spans
            while (self.row, self.column) in self.occupied:
                self.column += 1
            if self.row + rows > 4096 or self.column + cols > 512 or len(self.occupied) + rows * cols > 200000:
                fail("STRUCTURE_TABLE_TOO_LARGE", "Expanded table grid exceeds limit")
            area = {(r, c) for r in range(self.row, self.row + rows)
                    for c in range(self.column, self.column + cols)}
            if area & self.occupied:
                fail("STRUCTURE_TABLE_OVERLAP", "Cells overlap a spanning cell")
            self.occupied.update(area)
            self.cell = {"row": self.row, "column": self.column, "rowspan": rows,
                         "colspan": cols, "header": tag == "th", "parts": []}
            self.column += cols
        elif tag == "br" and self.cell is not None:
            self.cell["parts"].append("\n")
        elif tag in {"sup", "sub"} and self.cell is not None:
            self.cell["parts"].append("^{" if tag == "sup" else "_{")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag in {"sup", "sub"} and self.cell is not None:
            self.cell["parts"].append("}")
        elif tag in {"td", "th"} and self.table is not None:
            if self.cell is None:
                fail("STRUCTURE_TABLE_INVALID", "Closing an absent cell")
            self.cell["text"] = "".join(self.cell.pop("parts")).strip()
            self.table["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.table is not None:
            if self.cell is not None or not self.in_row:
                fail("STRUCTURE_TABLE_INVALID", "Malformed table row")
            self.in_row = False
        elif tag == "table" and self.table is not None:
            if self.cell is not None or self.in_row:
                fail("STRUCTURE_TABLE_INVALID", "Unclosed table content")
            self.table["rows"] = self.row + 1
            self.table["columns"] = max((c + 1 for _, c in self.occupied), default=0)
            if any(r > self.row for r, _ in self.occupied):
                fail("STRUCTURE_TABLE_SPAN_INVALID", "Rowspan extends beyond the table")
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, data):
        if self.cell is not None and not self.hidden:
            self.cell["parts"].append(data)


def tables(html: str) -> list[dict]:
    parser = _Tables()
    parser.feed(html)
    parser.close()
    if parser.table is not None:
        fail("STRUCTURE_TABLE_INVALID", "Unclosed table")
    return parser.tables
