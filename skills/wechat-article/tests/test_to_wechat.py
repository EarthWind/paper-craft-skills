import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "to_wechat.py"
sys.path.insert(0, str(ROOT / "scripts"))

import to_wechat  # noqa: E402

PIL = pytest.importorskip("PIL.Image")

ARTICLE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>t</title>
<style>p{color:red}</style><script>var x=1;</script></head>
<body>
<h1>一个测试标题</h1>
<div class="meta">
论文：Someone. <em>A Paper</em>. CONF 2026<br>
链接：<a href="https://example.org/paper">会议页面</a> · <a href="paper.pdf">本地 PDF</a><br>
风格：storytelling · 标注"PS"的段落是笔者看法
</div>
<div class="summary-box"><h3>先给结论</h3><p>这是一段完整的结论摘要。</p></div>
<h2>1. 第一节</h2>
<p>正文有 <strong>重点</strong>、<code>code()</code> 和 <a href="https://example.org/more">一个链接</a>。比例 $\\frac{9+6}{9}\\approx1.67$。</p>
$$\\max_t \\sum_j D_j(t) \\le \\sum_j S_j$$
<figure><img src="fig.png" alt="示意图"><figcaption>图 1：说明</figcaption></figure>
<pre class="mermaid">
flowchart LR
  A --> B
</pre>
<table><tr><th>a</th><th>b</th></tr><tr><td colspan="2">合并单元格</td></tr></table>
<p class="ps">PS：个人看法。</p>
<pre><code>x = a &lt; b</code></pre>
<div class="golden">一句金句。</div>
</body></html>
"""


@pytest.fixture
def article(tmp_path):
    PIL.new("RGBA", (40, 20), (31, 78, 158, 255)).save(tmp_path / "fig.png")
    src = tmp_path / "storytelling.html"
    src.write_text(ARTICLE, encoding="utf-8")
    return src


def run(src, out, *args):
    done = subprocess.run([sys.executable, str(SCRIPT), str(src), str(out), *args],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


def article_part(out):
    page = out.read_text(encoding="utf-8")
    return page[page.index('id="article"'):page.index("<script>", page.index('id="article"'))]


def test_converts_to_inline_styled_self_contained_article(article, tmp_path):
    out = tmp_path / "wechat.html"
    report = run(article, out, "--digest", "一句话摘要。", "--cover", "wechat-cover.png")
    body = article_part(out)

    assert report["title"] == "一个测试标题"
    assert report["digest"] == "一句话摘要。"
    assert report["images_embedded"] == 1
    assert report["mermaid_dropped"] == 1
    assert any("Mermaid" in w for w in report["warnings"])

    # nothing WeChat would strip or break
    for bad in ("class=", "<a ", "<style", "$", "flowchart", "<h1", "var x"):
        assert bad not in body, bad
    assert "data:image/jpeg;base64," in body
    assert 'colspan="2"' in body
    # code inside <pre> keeps its text, escaped
    assert "x = a &lt; b" in body
    # title lives outside the article, cover hint is shown
    page = out.read_text(encoding="utf-8")
    assert "一个测试标题" in page and "wechat-cover.png" in page


def test_links_become_numbered_references(article, tmp_path):
    out = tmp_path / "wechat.html"
    report = run(article, out)
    body = article_part(out)
    assert report["references"] == ["https://example.org/more", "https://example.org/paper"]
    assert "一个链接<sup" in body and "[1]</sup>" in body
    assert "参考资料" in body and "https://example.org/paper" in body
    # meta card keeps the paper line and the reader-facing part of the style line, drops links
    assert "论文：Someone." in body
    assert '标注"PS"的段落是笔者看法' in body or "标注&quot;PS&quot;" in body
    assert "链接：" not in body and "本地 PDF" not in body


def test_formulas_become_plain_text_and_can_be_overridden(article, tmp_path):
    out = tmp_path / "wechat.html"
    report = run(article, out)
    inline, display = report["formulas"]
    assert inline["html"] == "(9+6)/9≈1.67" and not inline["display"]
    plain = re.sub(r"<[^>]+>", "", display["html"])
    assert plain == "maxt Σj Dj(t) ≤ Σj Sj" and display["display"]

    fmap = tmp_path / "map.json"
    fmap.write_text(json.dumps({display["latex"]: "总需求峰值 ≤ 总供给"}, ensure_ascii=False), encoding="utf-8")
    report = run(article, out, "--formula-map", str(fmap))
    assert report["formulas"][1]["html"] == "总需求峰值 ≤ 总供给"
    assert "总需求峰值 ≤ 总供给" in article_part(out)


def test_auto_digest_uses_summary_box(article, tmp_path):
    report = run(article, tmp_path / "wechat.html")
    assert report["digest"] == "这是一段完整的结论摘要。"


def test_mermaid_image_replaces_block(article, tmp_path):
    png = tmp_path / "m1.png"
    PIL.new("RGB", (30, 30), (255, 255, 255)).save(png)
    report = run(article, tmp_path / "wechat.html", "--mermaid-image", str(png))
    assert report["mermaid_replaced"] == 1 and report["mermaid_dropped"] == 0
    assert report["images_embedded"] == 2


def test_missing_image_is_reported(tmp_path):
    src = tmp_path / "a.html"
    src.write_text('<body><h1>T</h1><p>x</p><img src="nope.png" alt="缺"></body>', encoding="utf-8")
    report = run(src, tmp_path / "o.html")
    assert any("nope.png" in w for w in report["warnings"])


def test_long_title_warns(tmp_path):
    src = tmp_path / "a.html"
    src.write_text("<body><h1>" + "长" * 70 + "</h1><p>x</p></body>", encoding="utf-8")
    report = run(src, tmp_path / "o.html")
    assert report["title_len"] == 70
    assert any("title" in w for w in report["warnings"])


@pytest.mark.parametrize("latex,expected", [
    (r"\alpha + \beta", "α + β"),
    (r"\sqrt{x+1}", "√(x+1)"),
    (r"\frac{a}{b}", "a/b"),
    (r"\text{disk time} = \sum_{i} t_i", "disk time = Σi ti"),
])
def test_latex_to_html(latex, expected):
    got = re.sub(r"<[^>]+>", "", to_wechat.latex_to_html(latex, []))
    assert got.replace(" ", "") == expected.replace(" ", "")


def test_follow_prompt_opens_article(article, tmp_path):
    out = tmp_path / "wechat.html"
    report = run(article, out, "--account", "论文拆解室")
    body = article_part(out)
    assert report["follow"] == "▲ 点击上方蓝字关注「论文拆解室」，及时获取更多论文解读"
    # first thing inside the article, before the info card
    first = body[body.index(">") + 1:].lstrip()
    assert first.startswith("<section") and "点击上方" in first[:600]
    assert body.index("点击上方") < body.index("论文：Someone.")


def test_follow_prompt_default_custom_and_off(article, tmp_path):
    out = tmp_path / "wechat.html"
    assert run(article, out)["follow"].startswith("▲ 点击上方蓝字关注我们")
    report = run(article, out, "--follow", "关注我，一起读论文 <3")
    assert report["follow"] == "关注我，一起读论文 <3"
    report = run(article, out, "--no-follow")
    assert report["follow"] == "" and "点击上方" not in article_part(out)
