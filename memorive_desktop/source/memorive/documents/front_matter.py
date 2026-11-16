"""Front matter at the top of a Markdown file: the part between the first two lines that hold only "---".

A line is a fence only when it is "---" by itself (trailing spaces allowed), so a value such as
"title: A---B" does not end the front matter. RawMD, CleanMD and card files use this.
"""
import re

_FENCE = re.compile(r"(?m)^\ufeff?---[ \t]*(?=\r?$)")  # a byte-order mark may open the file


def front_matter_fences(text):
    """The first two fence lines as regex matches, or None when there are fewer than two."""
    found = _FENCE.finditer(text)
    first = next(found, None)
    second = next(found, None)
    return None if second is None else (first, second)


def split_front_matter(text):
    """(before, front matter, after) around the first two fence lines, or None.

    before is what comes ahead of the first fence ("" in a normal file); after starts right after
    the closing "---", line break included. For a file whose values hold no "---" this gives the
    same three parts as text.split("---", 2)."""
    found = front_matter_fences(text)
    if found is None:
        return None
    first, second = found
    return text[:first.start()], text[first.end():second.start()], text[second.end():]
