#!/usr/bin/env python3
"""Syntax highlighting for the website's code blocks, applied to the HTML itself (no JavaScript).

  python3 tools/highlight_site.py          # rewrites docs/index.html and docs/docs.html in place
  python3 tools/highlight_site.py --check  # fails when a page is not highlighted as this script would do it

Every <pre><code> block is tokenized by language and its tokens wrapped in <span class="hl-…">. The
language comes from the code element's `language-…` class, else from the block's label (sql, python, sh,
json), else from its first line. Running it again gives the same bytes: existing hl spans are removed
before tokenizing. The colours are defined once per page, in a style block marked `syntax highlighting`.
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "docs" / "index.html", ROOT / "docs" / "docs.html"]

CSS = ("/* syntax highlighting (tools/highlight_site.py) */"
       ".hl-k{color:#7fc8f8}.hl-f{color:#a8df73}.hl-s{color:#e6c07b}.hl-n{color:#f5a97f}"
       ".hl-c{color:#86998d;font-style:italic}.hl-t{color:#82d4c3}.hl-v{color:#f5a97f}.hl-o{color:#d7e3d0}"
       ".hl-p{color:#c3a6f2}")

SQL_KW = """select from where and or not null is in as on join left right inner outer cross lateral create
extension if exists table index view function returns return returns language begin end declare grant
revoke role login password usage schema schemas tables all to with insert into values update set delete
order by group having limit offset union distinct case when then else true false array alter drop
replace cascade policy using check primary key references default unique""".split()
SQL_TYPES = "bigint integer int float8 float4 double precision text bytea jsonb json boolean regclass timestamptz".split()
PY_KW = """from import as def return if elif else for while in not and or is with try except finally raise
class lambda yield None True False pass break continue global nonlocal assert async await del""".split()
PY_BUILTIN = "print len range int float str list dict set tuple open isinstance sorted min max sum abs".split()


def tokens_sql(text):
    spec = [
        ("c", r"--[^\n]*"),
        ("s", r"'(?:[^']|'')*'"),
        ("s", r'"(?:[^"])*"'),
        ("t", r"(?<=::)[A-Za-z_][A-Za-z0-9_]*(?:\[\])?"),
        ("n", r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
        ("w", r"[A-Za-z_][A-Za-z0-9_.]*"),
        ("o", r"::|=>|<>|<=|>=|[=<>@*+\-/|]"),
    ]
    for kind, t, end in _lex(text, spec):
        if kind == "w":
            low = t.lower()
            kind = "k" if low in SQL_KW else ("t" if low in SQL_TYPES else ("f" if _call(text, end) else None))
        yield kind, t


def tokens_python(text):
    spec = [
        ("c", r"#[^\n]*"),
        ("s", r'[rbfRBF]?"""[\s\S]*?"""|[rbfRBF]?"(?:\\.|[^"\\\n])*"|[rbfRBF]?\'(?:\\.|[^\'\\\n])*\''),
        ("p", r"@[A-Za-z_][A-Za-z0-9_.]*"),
        ("n", r"\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
        ("w", r"[A-Za-z_][A-Za-z0-9_]*"),
    ]
    for kind, t, end in _lex(text, spec):
        if kind == "w":
            kind = "k" if t in PY_KW else ("t" if t in PY_BUILTIN else ("f" if _call(text, end) else None))
        yield kind, t


def tokens_sh(text):
    spec = [
        ("c", r"(?<![^\s])#[^\n]*"),
        ("s", r'"(?:\\.|[^"\\])*"|\'[^\']*\''),
        ("v", r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"),
        ("t", r"(?<![^\s])--?[A-Za-z][A-Za-z0-9-]*"),
        ("w", r"[A-Za-z_][A-Za-z0-9_.-]*"),
        ("o", r"\\$|\\(?=\n)|&&|\|\||[|>]"),
    ]
    command_next, continued = True, False
    for kind, t, _ in _lex(text, spec):
        if kind == "w":
            kind = "f" if command_next else None
        if kind is None and "\n" in t:
            command_next = not continued
        elif t.strip():
            command_next = t in ("&&", "||", "|")
        continued = t == "\\"
        yield kind, t


def tokens_json(text):
    spec = [
        ("k", r'"(?:\\.|[^"\\])*"(?=\s*:)'),
        ("s", r'"(?:\\.|[^"\\])*"'),
        ("n", r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
        ("t", r"\b(?:true|false|null)\b"),
    ]
    for kind, t, _ in _lex(text, spec):
        yield kind, t


LEXERS = {"sql": tokens_sql, "python": tokens_python, "sh": tokens_sh, "json": tokens_json}


def _lex(text, spec):
    """(kind, token, end) for every token; kind None for text between matches."""
    rx = re.compile("|".join(f"(?P<g{i}>{p})" for i, (_, p) in enumerate(spec)))
    pos = 0
    for m in rx.finditer(text):
        if m.start() > pos:
            yield None, text[pos:m.start()], m.start()
        yield spec[int(m.lastgroup[1:])][0], m.group(0), m.end()
        pos = m.end()
    if pos < len(text):
        yield None, text[pos:], len(text)


def _call(text, end):
    """Whether the word ending at `end` is followed by an opening parenthesis."""
    return re.match(r"[ \t]*\(", text[end:]) is not None


def render(text, lang):
    out = []
    for kind, t in LEXERS[lang](text):
        e = html.escape(t, quote=False)
        out.append(f'<span class="hl-{kind}">{e}</span>' if kind else e)
    return "".join(out)


BLOCK = re.compile(r'(<pre[^>]*>)<code([^>]*)>(.*?)</code></pre>', re.S)
SPAN = re.compile(r'</?span(?: class="hl-[a-z]")?>')
LABEL = re.compile(r'<span class="code-label">([a-z]+)</span>\s*$')


def language(page, start, attrs, text):
    m = re.search(r'language-([a-z]+)', attrs)
    if m:
        return m.group(1)
    m = LABEL.search(page[max(0, start - 80):start])
    if m:
        return {"bash": "sh", "shell": "sh", "console": "sh"}.get(m.group(1), m.group(1))
    first = text.lstrip().split("\n", 1)[0].upper()
    return "sql" if first.startswith(("--", "SELECT", "CREATE", "INSERT", "WITH")) else "sh"


def highlight(page):
    def sub(m):
        inner = SPAN.sub("", m.group(3))
        text = html.unescape(inner)
        lang = language(page, m.start(), m.group(2), text)
        if lang not in LEXERS:
            return m.group(0)
        attrs = m.group(2)
        if "language-" not in attrs:
            attrs = f' class="language-{lang}"' if "class=" not in attrs else attrs.replace('class="', f'class="language-{lang} ', 1)
        return f"{m.group(1)}<code{attrs}>{render(text, lang)}</code></pre>"
    page = BLOCK.sub(sub, page)
    page = re.sub(r"/\* syntax highlighting \(tools/highlight_site\.py\) \*/[^<]*?\.hl-p\{[^}]*\}", "", page)
    return page.replace("</style>", CSS + "</style>", 1)


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    for p in PAGES:
        old = p.read_text()
        new = highlight(old)
        if new != old:
            stale.append(p.name)
            if not check:
                p.write_text(new)
    if check and stale:
        sys.exit(f"not highlighted: {', '.join(stale)} (run tools/highlight_site.py)")
    print("highlighted" if not check else "highlighting up to date", ", ".join(x.name for x in PAGES))


if __name__ == "__main__":
    main()
