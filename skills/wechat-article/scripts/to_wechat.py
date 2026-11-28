#!/usr/bin/env python3
"""Convert an article HTML page (e.g. paper-analyzer output) into a WeChat-ready page.

The WeChat Official Account editor keeps only inline styles: it drops <style>, <script>,
class attributes, KaTeX/Mermaid rendering and links to non-WeChat sites. This script
rewrites the article body accordingly:

* every element gets inline styles (theme colour configurable with --accent);
* local images are embedded as base64 JPEG so the page is one self-contained file;
* LaTeX ($...$ / $$...$$) becomes plain HTML text with <sub>/<sup> (override per formula
  with --formula-map);
* Mermaid blocks are dropped, or replaced by PNGs given with --mermaid-image;
* links become plain text, http(s) targets are collected into a "参考资料" list;
* the <h1> becomes the WeChat title field, the .meta block becomes a small info card;
* a "点击上方蓝字关注" follow prompt opens the article (--account NAME / --follow TEXT /
  --no-follow).

The output page shows title / digest / cover hints and a "复制正文" button that copies the
article as rich text for pasting into the WeChat editor.

Usage:
  to_wechat.py article.html wechat.html [--digest TEXT] [--cover a.png --cover b.png]
               [--formula-map map.json] [--mermaid-image m1.png ...] [--accent #1f4e9e]
               [--note TEXT] [--account NAME | --follow TEXT | --no-follow]
A JSON report (title length, digest length, formulas, warnings, ...) is printed to stdout.
"""
import argparse
import base64
import html
import io
import json
import pathlib
import re
import sys
from html.parser import HTMLParser

try:
    from PIL import Image
except ImportError:  # Pillow is optional; without it images are embedded unchanged
    Image = None

TITLE_MAX = 64
DIGEST_MAX = 120
FOLLOW_TAIL = "，及时获取更多论文解读"
VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}

# ---------------------------------------------------------------- LaTeX -> HTML text

SYMBOLS = {
    "approx": "≈", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "sum": "Σ", "prod": "Π",
    "int": "∫", "infty": "∞", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "iff": "⇔", "implies": "⇒", "in": "∈",
    "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "forall": "∀",
    "exists": "∃", "partial": "∂", "nabla": "∇", "ldots": "…", "cdots": "⋯", "dots": "…",
    "propto": "∝", "sim": "∼", "equiv": "≡", "lceil": "⌈", "rceil": "⌉", "lfloor": "⌊",
    "rfloor": "⌋", "langle": "⟨", "rangle": "⟩", "mid": "|", "vert": "|", "|": "‖",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ",
    "Theta": "Θ", "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}
FUNCS = {"max", "min", "log", "ln", "exp", "sin", "cos", "tan", "lim", "arg", "det", "sup",
         "inf", "argmax", "argmin", "Pr", "E"}
TEXT_CMDS = {"text", "mathrm", "textrm", "mathit", "textit", "mathbf", "textbf", "mathsf",
             "texttt", "operatorname", "mbox", "boldsymbol", "mathcal", "mathbb"}
SPACES = {"quad": " ", "qquad": "  ", ",": " ", ";": " ", ":": " ",
          "!": "", " ": " "}
IGNORE = {"left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits",
          "nolimits", "textstyle"}


class Latex:
    def __init__(self, src, warnings):
        self.s, self.i, self.warnings = src, 0, warnings

    def _cmd(self):
        self.i += 1  # backslash
        m = re.match(r"[A-Za-z]+", self.s[self.i:])
        if m:
            self.i += m.end()
            return m.group(0)
        ch = self.s[self.i:self.i + 1]
        self.i += 1
        return ch

    def _group(self):
        """Read one argument: {...}, a \\command, or a single character."""
        while self.i < len(self.s) and self.s[self.i] == " ":
            self.i += 1
        if self.i >= len(self.s):
            return ""
        c = self.s[self.i]
        if c == "{":
            depth, j = 0, self.i
            while j < len(self.s):
                if self.s[j] == "{":
                    depth += 1
                elif self.s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = self.s[self.i + 1:j]
            self.i = j + 1
            return inner
        if c == "\\":
            start = self.i
            self._cmd()
            return self.s[start:self.i]
        self.i += 1
        return c

    @staticmethod
    def _wrap(h):
        plain = re.sub(r"<[^>]+>", "", h)
        return f"({h})" if re.search(r"[\s+\-−×·/=]", plain.strip()) else h

    def convert(self):
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                name = self._cmd()
                if name in TEXT_CMDS:
                    out.append(html.escape(self._group()))
                elif name in ("frac", "dfrac", "tfrac"):
                    a = Latex(self._group(), self.warnings).convert()
                    b = Latex(self._group(), self.warnings).convert()
                    out.append(f"{self._wrap(a)}/{self._wrap(b)}")
                elif name == "sqrt":
                    out.append("√" + self._wrap(Latex(self._group(), self.warnings).convert()))
                elif name in SYMBOLS:
                    out.append(SYMBOLS[name])
                elif name in FUNCS:
                    out.append(name)
                elif name in SPACES:
                    out.append(SPACES[name])
                elif name in IGNORE:
                    pass
                elif name in "{}%$_&#":
                    out.append(html.escape(name))
                else:
                    self.warnings.append(f"unknown LaTeX command \\{name} kept as text")
                    out.append(html.escape(name))
            elif c in "^_":
                self.i += 1
                arg = Latex(self._group(), self.warnings).convert()
                tag = "sup" if c == "^" else "sub"
                out.append(f'<{tag} style="font-size:0.75em;">{arg}</{tag}>')
            elif c in "{}":
                self.i += 1
            elif c == "~":
                out.append(" ")
                self.i += 1
            else:
                out.append(html.escape(c))
                self.i += 1
        return re.sub(r" {2,}", " ", "".join(out)).strip()


def latex_to_html(src, warnings):
    return Latex(src, warnings).convert()


# ---------------------------------------------------------------- styles


def make_styles(accent):
    text, muted = "#333333", "#888888"
    p = f"margin:0 0 20px;font-size:16px;line-height:1.85;color:{text};letter-spacing:0.5px;text-align:justify;"
    cell = "border:1px solid #dcdfe6;padding:6px 8px;text-align:left;vertical-align:top;"
    return {
        "text": text, "muted": muted, "accent": accent,
        "p": p,
        "p.ps": p + "padding:12px 14px;background:#fff6ee;border-radius:4px;font-size:15px;color:#5a4a3a;",
        "h2": f"margin:36px 0 18px;padding:0 0 6px;font-size:19px;font-weight:bold;line-height:1.5;color:{accent};border-bottom:2px solid {accent};",
        "h3": f"margin:28px 0 14px;padding-left:10px;font-size:17px;font-weight:bold;line-height:1.5;color:{text};border-left:4px solid {accent};",
        "h4": f"margin:22px 0 12px;font-size:16px;font-weight:bold;line-height:1.5;color:{text};",
        "summary": f"margin:0 0 28px;padding:16px 18px;background:#f0f4fb;border-left:4px solid {accent};border-radius:4px;",
        "summary h3": f"margin:0 0 8px;font-size:16px;font-weight:bold;color:{accent};",
        "summary p": f"margin:0;font-size:15px;line-height:1.8;color:{text};text-align:justify;",
        "golden": f"margin:32px 0;padding:22px 12px;border-top:2px solid {accent};border-bottom:2px solid {accent};text-align:center;font-size:18px;font-weight:bold;line-height:1.6;color:{accent};",
        "section": "margin:0 0 20px;",
        "blockquote": f"margin:0 0 20px;padding:12px 16px;background:#f7f8fa;border-left:3px solid {accent};font-size:14px;line-height:1.8;color:#555555;text-align:justify;",
        "ul": f"margin:0 0 20px;padding-left:24px;font-size:16px;line-height:1.85;color:{text};",
        "ol": f"margin:0 0 20px;padding-left:24px;font-size:16px;line-height:1.85;color:{text};",
        "li": "margin:0 0 6px;",
        "table_wrap": "margin:0 0 22px;overflow-x:auto;",
        "table": "width:100%;border-collapse:collapse;font-size:13px;line-height:1.6;",
        "th": cell + f"background:#eef2f9;color:{accent};font-weight:bold;",
        "td": cell + f"color:{text};",
        "strong": f"color:{accent};font-weight:bold;",
        "em": "font-style:italic;",
        "code": "padding:1px 4px;background:#f2f3f5;border-radius:3px;font-size:14px;color:#c7254e;word-break:break-all;",
        "pre": "margin:0 0 20px;padding:12px 14px;background:#f6f8fa;border-radius:4px;overflow-x:auto;font-size:13px;line-height:1.6;color:#24292e;white-space:pre-wrap;word-break:break-all;",
        "figure": "margin:0 0 24px;",
        "figcaption": f"margin:8px 0 0;font-size:13px;line-height:1.6;color:{muted};text-align:center;",
        "img": "display:block;width:100%;height:auto;margin:0 auto;border-radius:4px;",
        "hr": "margin:28px 0;border:0;border-top:1px solid #e5e7eb;",
        "formula": "margin:0 0 20px;padding:14px 16px;background:#f4f6fa;border-radius:6px;text-align:center;font-size:15px;line-height:1.8;",
        "info": "margin:0 0 24px;padding:12px 14px;background:#f7f8fa;border-radius:4px;",
        "info p": f"margin:0;font-size:13px;line-height:1.8;color:{muted};",
        "refs": "margin:32px 0 0;padding-top:12px;border-top:1px solid #e5e7eb;",
        "refs h": f"margin:0 0 6px;font-size:14px;font-weight:bold;color:{text};",
        "refs p": f"margin:0;font-size:12px;line-height:1.8;color:{muted};word-break:break-all;",
        "sup": "font-size:0.75em;",
        "follow": "margin:0 0 20px;padding:10px 12px;background:#f7f8fa;border-radius:4px;text-align:center;",
        "follow p": f"margin:0;font-size:14px;line-height:1.7;color:{muted};letter-spacing:0.5px;",
        "follow em": f"color:{accent};font-weight:bold;font-style:normal;",
    }


# ---------------------------------------------------------------- converter


class Converter(HTMLParser):
    def __init__(self, base_dir, styles, formula_map, mermaid_images):
        super().__init__(convert_charrefs=True)
        self.base, self.st = base_dir, styles
        self.formula_map, self.mermaid_images = formula_map, list(mermaid_images)
        self.out, self.stack = [], []  # stack of (tag, closing_html)
        self.skip = None  # [tag, depth, kind]
        self.capture = []
        self.title, self.meta_lines, self.meta_links = "", [], []
        self.refs, self.formulas, self.warnings = [], [], []
        self.images = self.mermaid_dropped = self.mermaid_replaced = 0
        self.in_pre = self.in_code = 0
        self.ctx = []  # semantic context: 'summary', 'golden', 'body'

    # -- helpers
    def _style(self, key):
        return f' style="{self.st[key]}"'

    def _ref(self, href, label):
        for n, (h, _) in enumerate(self.refs, 1):
            if h == href:
                return n
        self.refs.append((href, label))
        return len(self.refs)

    def _embed(self, src, alt):
        if re.match(r"^(https?:|data:)", src):
            return f'<img src="{html.escape(src)}" alt="{html.escape(alt)}"{self._style("img")}>'
        path = (self.base / src).resolve()
        if not path.is_file():
            self.warnings.append(f"image not found, replaced by its alt text: {src}")
            return f'<p{self._style("figcaption")}>［图片缺失：{html.escape(alt or src)}］</p>'
        if Image is not None:
            im = Image.open(path)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            if im.width > 1280:
                im = im.resize((1280, round(im.height * 1280 / im.width)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88, optimize=True)
            data, mime = buf.getvalue(), "image/jpeg"
        else:
            data = path.read_bytes()
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        self.images += 1
        b64 = base64.b64encode(data).decode()
        return f'<img src="data:{mime};base64,{b64}" alt="{html.escape(alt)}"{self._style("img")}>'

    def _emit_formula(self, latex, display):
        key = latex.strip()
        if key in self.formula_map:
            body = self.formula_map[key]
        else:
            body = latex_to_html(key, self.warnings)
        self.formulas.append({"latex": key, "display": display, "html": body})
        if display:
            return f'<section{self._style("formula")}><span style="color:{self.st["text"]};">{body}</span></section>'
        return body

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = set((a.get("class") or "").split())
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] += 1
            if self.skip[2] == "meta":
                if tag == "br":
                    self.capture.append("\n")
                elif tag == "a" and a.get("href"):
                    self.meta_links.append(a["href"])
            if tag == self.skip[0] and tag in VOID:
                self.skip[1] -= 1
            return
        if tag in ("script", "style", "head", "title"):
            self.skip, self.capture = [tag, 1, "drop"], []
            return
        if tag == "h1":
            self.skip, self.capture = [tag, 1, "title"], []
            return
        if tag == "div" and "meta" in cls:
            self.skip, self.capture = [tag, 1, "meta"], []
            return
        if tag == "pre" and "mermaid" in cls:
            self.skip, self.capture = [tag, 1, "mermaid"], []
            return

        if tag == "img":
            self.out.append(self._embed(a.get("src", ""), a.get("alt", "")))
            return
        if tag == "br":
            self.out.append("<br/>")
            return
        if tag == "hr":
            self.out.append(f'<hr{self._style("hr")}/>')
            return
        if tag == "a":
            self.stack.append(("a", self._link_close(a.get("href", ""))))
            return

        open_html, close_html = self._open(tag, cls, a)
        self.out.append(open_html)
        self.stack.append((tag, close_html))

    def _link_close(self, href):
        if re.match(r"^https?://", href) and "mp.weixin.qq.com" not in href:
            return ("ref", href)
        return ("", "")

    def _open(self, tag, cls, a):
        in_summary = "summary" in self.ctx
        if tag == "div":
            if "summary-box" in cls:
                self.ctx.append("summary")
                return f'<section{self._style("summary")}>', ("</section>", "summary")
            if "golden" in cls:
                self.ctx.append("golden")
                return f'<section{self._style("golden")}>', ("</section>", "golden")
            return "<section>", ("</section>", None)
        if tag == "p":
            key = "summary p" if in_summary else ("p.ps" if "ps" in cls else "p")
            return f'<p{self._style(key)}>', ("</p>", None)
        if tag in ("h2", "h3", "h4", "h5", "h6"):
            key = "summary h3" if in_summary else ("h4" if tag in ("h5", "h6") else tag)
            if in_summary:
                return f'<p{self._style(key)}>', ("</p>", None)
            return f'<{tag}{self._style(key)}>', (f"</{tag}>", None)
        if tag == "table":
            return (f'<section{self._style("table_wrap")}><table{self._style("table")}>',
                    ("</table></section>", None))
        if tag in ("th", "td"):
            span = "".join(f' {k}="{html.escape(a[k])}"' for k in ("colspan", "rowspan") if a.get(k))
            return f"<{tag}{span}{self._style(tag)}>", (f"</{tag}>", None)
        if tag in ("strong", "b"):
            return f'<strong{self._style("strong")}>', ("</strong>", None)
        if tag in ("em", "i"):
            return f'<em{self._style("em")}>', ("</em>", None)
        if tag == "code":
            self.in_code += 1
            if self.in_pre:
                return "<code>", ("</code>", "code")
            return f'<code{self._style("code")}>', ("</code>", "code")
        if tag == "pre":
            self.in_pre += 1
            return f'<pre{self._style("pre")}>', ("</pre>", "pre")
        if tag == "figure":
            return f'<section{self._style("figure")}>', ("</section>", None)
        if tag == "figcaption":
            return f'<p{self._style("figcaption")}>', ("</p>", None)
        if tag in ("blockquote", "ul", "ol", "li"):
            return f"<{tag}{self._style(tag)}>", (f"</{tag}>", None)
        if tag in ("sup", "sub"):
            return f"<{tag}{self._style('sup')}>", (f"</{tag}>", None)
        if tag in ("tr", "thead", "tbody", "span", "section", "article", "main"):
            return f"<{tag}>", (f"</{tag}>", None)
        if tag in ("body", "html"):
            return "", ("", None)
        self.warnings.append(f"unhandled tag <{tag}> kept without attributes")
        return f"<{tag}>", (f"</{tag}>", None)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] -= 1
                if self.skip[1] == 0:
                    self._finish_skip()
            return
        if tag in VOID:
            return
        # pop until the matching tag (tolerates sloppy nesting)
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                break
        else:
            return
        while len(self.stack) > idx:
            t, close = self.stack.pop()
            if t == "a":
                kind, href = close
                if kind == "ref":
                    n = self._ref(href, "")
                    self.out.append(f'<sup{self._style("sup")}>[{n}]</sup>')
                continue
            html_close, marker = close
            self.out.append(html_close)
            if marker in ("summary", "golden") and self.ctx:
                self.ctx.pop()
            elif marker == "pre":
                self.in_pre -= 1
            elif marker == "code":
                self.in_code -= 1

    def _finish_skip(self):
        kind = self.skip[2]
        text = "".join(self.capture)
        self.skip, self.capture = None, []
        if kind == "title":
            self.title = re.sub(r"\s+", " ", text).strip()
        elif kind == "meta":
            self.meta_lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
            self.out.append("__META__")
        elif kind == "mermaid":
            if self.mermaid_images:
                self.out.append(f'<section{self._style("figure")}>' + self._embed(self.mermaid_images.pop(0), "diagram") + "</section>")
                self.mermaid_replaced += 1
            else:
                self.mermaid_dropped += 1

    def handle_data(self, data):
        if self.skip:
            if self.skip[2] in ("title", "meta"):
                self.capture.append(data)
            return
        if self.in_pre or self.in_code:
            self.out.append(html.escape(data, quote=False))
            return
        parts = re.split(r"(\$\$.+?\$\$|\$[^$\n]+?\$)", data, flags=re.S)
        for part in parts:
            if part.startswith("$$") and part.endswith("$$") and len(part) > 4:
                self.out.append(self._emit_formula(part[2:-2], True))
            elif part.startswith("$") and part.endswith("$") and len(part) > 2:
                self.out.append(self._emit_formula(part[1:-1], False))
            else:
                self.out.append(html.escape(part, quote=False))


def build_info(conv, note, styles):
    keep = []
    for ln in conv.meta_lines:
        if not ln:
            continue
        if re.match(r"^(链接|Links?)\s*[：:]", ln):
            continue  # local-file links are useless in WeChat; web links go to 参考资料
        if re.match(r"^(风格|Style)\s*[：:]", ln):
            # "风格：storytelling · 文中引文……" -> keep only the reader-facing remainder
            ln = ln.split(" · ", 1)[1] if " · " in ln else ""
            if not ln:
                continue
        keep.append(html.escape(ln, quote=False))
    for href in conv.meta_links:
        if re.match(r"^https?://", href):
            conv._ref(href, "")
    if note:
        keep.append(html.escape(note, quote=False))
    if not keep:
        return ""
    return (f'<section style="{styles["info"]}"><p style="{styles["info p"]}">'
            + "<br/>".join(keep) + "</p></section>")


def build_follow(account, follow, styles):
    """Follow prompt shown at the very top of the article; "" when disabled.

    follow=None -> default wording built from account; follow="" -> disabled;
    any other string -> used verbatim (plain text, escaped).
    """
    if follow == "":
        return ""
    em = styles["follow em"]
    if follow is not None:
        inner = html.escape(follow, quote=False)
    else:
        name = f'<span style="{em}">「{html.escape(account, quote=False)}」</span>' if account else "我们"
        inner = f'▲ 点击上方<span style="{em}">蓝字</span>关注{name}{FOLLOW_TAIL}'
    return f'<section style="{styles["follow"]}"><p style="{styles["follow p"]}">{inner}</p></section>'


def build_refs(conv, styles):
    if not conv.refs:
        return ""
    lines = "<br/>".join(f"[{n}] {html.escape(h)}" for n, (h, _) in enumerate(conv.refs, 1))
    return (f'<section style="{styles["refs"]}"><p style="{styles["refs h"]}">参考资料</p>'
            f'<p style="{styles["refs p"]}">{lines}</p></section>')


def auto_digest(article_html, styles):
    """First paragraph of the summary box if there is one, else the first body paragraph."""
    summary = re.escape(styles["summary p"])
    m = re.search(rf'<p style="{summary}">(.*?)</p>', article_html, re.S)
    if not m:
        m = re.search(rf'<p style="{re.escape(styles["p"])}">(.*?)</p>', article_html, re.S)
    text = re.sub(r"<[^>]+>", "", m.group(1)) if m else ""
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text if len(text) <= DIGEST_MAX else text[:DIGEST_MAX - 1] + "…"


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_esc} — 公众号版</title>
</head>
<body style="margin:0;background:#ededed;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:16px;">
<div style="background:#fffbe6;border:1px solid #f0d78c;border-radius:6px;padding:12px 16px;font-size:14px;line-height:1.8;color:#5c4b00;">
<b>使用方法</b>：点击“复制正文”，到公众号后台新建图文，在正文区粘贴（Ctrl/⌘+V）。<br>
标题（{title_len} 字，上限 {title_max} 字）：<span style="user-select:all;">{title_esc}</span><br>
摘要（{digest_len} 字，上限 {digest_max} 字）：<span style="user-select:all;">{digest_esc}</span><br>
{cover_line}配图已内嵌；粘贴后若个别图片显示失败，删除后从原目录重新上传同名图片即可。<br>
<button id="copy" style="margin-top:8px;padding:6px 18px;font-size:14px;color:#fff;background:#07c160;border:0;border-radius:4px;cursor:pointer;">复制正文</button>
<span id="msg" style="margin-left:8px;"></span>
</div>
<div style="height:16px;"></div>
<div style="background:#fff;padding:24px 18px;">
<section id="article" style="font-size:16px;color:#333333;line-height:1.85;">
{article}
</section>
</div>
</div>
<script>
document.getElementById('copy').onclick = async function () {{
  var el = document.getElementById('article'), msg = document.getElementById('msg');
  try {{
    await navigator.clipboard.write([new ClipboardItem({{
      'text/html': new Blob([el.outerHTML], {{type: 'text/html'}}),
      'text/plain': new Blob([el.innerText], {{type: 'text/plain'}})
    }})]);
    msg.textContent = '已复制，去公众号编辑器粘贴';
  }} catch (e) {{
    var r = document.createRange(); r.selectNode(el);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    var ok = document.execCommand('copy'); s.removeAllRanges();
    msg.textContent = ok ? '已复制，去公众号编辑器粘贴' : '复制失败，请手动全选正文复制';
  }}
}};
</script>
</body>
</html>
"""


def convert(src, out, digest=None, title=None, covers=(), formula_map=None, mermaid_images=(),
            accent="#1f4e9e", note=None, account=None, follow=None):
    src, out = pathlib.Path(src), pathlib.Path(out)
    raw = src.read_text(encoding="utf-8")
    m = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
    body = m.group(1) if m else raw
    # the <h1> may sit outside <body> in sloppy pages; read it from the whole document
    styles = make_styles(accent)
    conv = Converter(src.parent, styles, formula_map or {}, mermaid_images)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
    conv.feed(body)
    conv.close()
    if not conv.title and h1:
        conv.title = html.unescape(re.sub(r"<[^>]+>|\s+", " ", h1.group(1))).strip()
    final_title = (title or conv.title).strip()

    article = "".join(conv.out)
    body_digest = auto_digest(article, styles)
    info = build_info(conv, note, styles)
    if "__META__" in article:
        article = article.replace("__META__", info, 1)
    elif info:
        article = info + article
    follow_html = build_follow(account, follow, styles)
    article = follow_html + "\n" + article.strip() + "\n" + build_refs(conv, styles)
    article = re.sub(r"\n{3,}", "\n\n", article)

    for bad in ("class=", "<a ", "<script", "<div", "$$"):
        if bad in article:
            conv.warnings.append(f"unexpected markup left in article: {bad!r}")
    if conv.mermaid_dropped:
        conv.warnings.append(f"{conv.mermaid_dropped} Mermaid diagram(s) dropped (WeChat cannot render them); pass --mermaid-image to keep them as pictures")
    if conv.mermaid_images:
        conv.warnings.append(f"{len(conv.mermaid_images)} --mermaid-image file(s) unused")

    final_digest = (digest or body_digest).strip()
    if len(final_title) > TITLE_MAX:
        conv.warnings.append(f"title has {len(final_title)} chars, WeChat allows {TITLE_MAX}")
    if len(final_digest) > DIGEST_MAX:
        conv.warnings.append(f"digest has {len(final_digest)} chars, WeChat allows {DIGEST_MAX}")
    cover_line = ""
    if covers:
        cover_line = "封面：" + "、".join(html.escape(c) for c in covers) + "（同目录）。"

    page = PAGE.format(
        title_esc=html.escape(final_title), title_len=len(final_title), title_max=TITLE_MAX,
        digest_esc=html.escape(final_digest), digest_len=len(final_digest), digest_max=DIGEST_MAX,
        cover_line=cover_line, article=article,
    )
    out.write_text(page, encoding="utf-8")
    return {
        "output": str(out),
        "size_mb": round(out.stat().st_size / 1e6, 2),
        "title": final_title,
        "title_len": len(final_title),
        "digest": final_digest,
        "digest_len": len(final_digest),
        "images_embedded": conv.images,
        "mermaid_replaced": conv.mermaid_replaced,
        "mermaid_dropped": conv.mermaid_dropped,
        "formulas": conv.formulas,
        "follow": html.unescape(re.sub(r"<[^>]+>", "", follow_html)),
        "references": [h for h, _ in conv.refs],
        "warnings": conv.warnings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="article HTML, e.g. storytelling.html from paper-analyzer")
    ap.add_argument("out", help="output page, e.g. wechat.html")
    ap.add_argument("--title", help="override the title (default: the <h1>)")
    ap.add_argument("--digest", help=f"WeChat digest, <= {DIGEST_MAX} chars (default: first paragraph)")
    ap.add_argument("--cover", action="append", default=[], help="cover file name to mention; repeatable")
    ap.add_argument("--formula-map", help="JSON file {latex: replacement_html} for formulas the auto conversion gets wrong")
    ap.add_argument("--mermaid-image", action="append", default=[], help="PNG replacing the next Mermaid block; repeatable, in order")
    ap.add_argument("--accent", default="#1f4e9e", help="theme colour (default #1f4e9e)")
    ap.add_argument("--note", help="extra line for the info card, e.g. '配图为重新绘制的示意图，非论文原图'")
    fg = ap.add_mutually_exclusive_group()
    fg.add_argument("--account", help="公众号名称, shown in the follow prompt at the top of the article")
    fg.add_argument("--follow", help="custom follow-prompt text (plain text), replaces the default wording")
    fg.add_argument("--no-follow", action="store_true", help="omit the follow prompt")
    args = ap.parse_args(argv)
    follow = "" if args.no_follow else args.follow
    fmap = json.loads(pathlib.Path(args.formula_map).read_text(encoding="utf-8")) if args.formula_map else {}
    report = convert(args.src, args.out, digest=args.digest, title=args.title, covers=args.cover,
                     formula_map=fmap, mermaid_images=args.mermaid_image, accent=args.accent, note=args.note,
                     account=args.account, follow=follow)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
