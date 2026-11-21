"""Counts and page numbers in the research chat, as the English and Japanese interfaces show them.

The chat page builds "3 轮对话", "12 页" and "页 4" in Chinese and hands them to the page translator
(ui/integrated/shared/app.js, translated). English showed "1 exchanges" and "pages 4": the word was
translated alone and set after the number. The translator's own code is run with Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "ui" / "integrated" / "shared" / "app.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_counts_and_page_numbers_read_naturally(tmp_path):
    source = APP.read_text(encoding="utf-8")
    table = next(line for line in source.splitlines() if line.lstrip().startswith("const translations = "))
    function = re.search(r"\n  const translated = \(source, language\) => \{\n.*?\n  \};\n", source, re.S).group(0)
    cases = ["1 轮对话", "3 轮对话", "1 页", "12 页", "页 4"]
    runner = tmp_path / "wording.js"
    runner.write_text("const Memorive = {};\n" + table + function
                      + f"console.log(JSON.stringify({json.dumps(cases, ensure_ascii=False)}.map(s => "
                        "[translated(s, 'zh-CN'), translated(s, 'en-US'), translated(s, 'ja-JP')])));\n",
                      encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(runner)], capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [
        ["1 轮对话", "1 exchange", "1 往復の会話"],
        ["3 轮对话", "3 exchanges", "3 往復の会話"],
        ["1 页", "1 page", "1 ページ"],
        ["12 页", "12 pages", "12 ページ"],
        ["页 4", "Page 4", "ページ 4"],
    ]
