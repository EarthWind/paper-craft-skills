---
name: wechat-article
description: |
  把文章 HTML（如 paper-analyzer 生成的 storytelling/academic/concise 页面）转换成可一键复制、直接粘贴进微信公众号编辑器的图文，
  并生成配套的极简封面图（2.35:1 头条封面 + 1:1 转发缩略图）。当用户提到"公众号文章""微信公众号""转成公众号""公众号排版""公众号封面"时使用。
---

# WeChat Article — 论文解读转公众号图文

把一篇已经写好的 HTML 长文变成**复制即可发布**的公众号图文，再配一张**简洁**的封面。

```
/wechat-article path/to/storytelling.html
/wechat-article path/to/storytelling.html 只要封面
```

不负责写文章本身：先用 `paper-analyzer` 写出 HTML，再用本技能转换。

## 为什么不能直接粘贴原 HTML

公众号编辑器只保留行内样式，会丢掉：

| 原页面里的东西 | 公众号里的结果 | 本技能的处理 |
|---|---|---|
| `<style>`、`class` | 全部丢失，排版塌掉 | 每个元素写成行内 `style` |
| 本地图片 `src="01.png"` | 粘贴后裂图 | 转 JPEG 后 base64 内嵌，整页一个文件 |
| KaTeX 公式 `$...$` | 显示成源码 | 转成纯文本 + `<sub>`/`<sup>`；可逐条覆盖 |
| Mermaid 图 | 显示成源码 | 默认去掉并告警；可用 PNG 替换 |
| 外部链接 | 正文不允许非公众号链接 | 去掉链接，网址收进文末"参考资料"并加角标 |
| `<h1>` 标题 | 与公众号标题栏重复 | 移出正文，作为标题字段给出 |

## 工作流程

### Step 1：确认输入

- 找到要转换的 HTML（通常是论文目录里的 `storytelling.html`，也可以是 academic/concise）。
- 用户没说要不要封面时，问一句；只说"转公众号"时默认两样都做。

### Step 2：写摘要，转换正文

先读文章，自己写一句**完整的**摘要（≤120 字，公众号上限；不要用截断的半句话），再运行：

```bash
python3 scripts/to_wechat.py <article.html> <out_dir>/wechat.html \
  --digest "<一句话摘要>" \
  --cover wechat-cover.png --cover wechat-cover-1x1.png \
  --note "配图为依据论文重新绘制的示意图，非论文原图"
```

- `--note`：文中配图是重新绘制的示意图时加上，写进文首信息卡。
- `--accent '#1f4e9e'`：主题色，封面用同一个颜色。
- `--title`：覆盖 `<h1>` 标题（公众号标题上限 64 字）。
- 依赖：Python 3 + Pillow（没有 Pillow 时图片原样内嵌，体积更大）。

脚本输出 JSON 报告。**逐项看完再继续**：

- `formulas`：每条公式转换后的 `html` 是否读得通。读不通的写进一个 JSON（`{"原 LaTeX": "替换用的 HTML"}`），用 `--formula-map map.json` 重跑。复杂公式（矩阵、多行推导）宁可改写成一句文字说明。
- `mermaid_dropped` > 0：判断这张图是否只是重复了旁边的配图。重复就接受去掉；不重复就把它渲染成 PNG（例如有 `mmdc` 时用 mermaid-cli，或用无头浏览器截图），再用 `--mermaid-image` 按出现顺序传入。
- `title_len` ≤ 64、`digest_len` ≤ 120。
- `warnings` 为空，或每条都已处理/说明。

### Step 3：渲染检查

用无头浏览器截图看一遍（例如 `google-chrome --headless=new --screenshot=shot.png --window-size=720,4000 file://.../wechat.html`），确认：标题层级、表格、引用、图片、金句都在；没有残留的 `$`、Mermaid 源码或裂图。

### Step 4：封面（简洁优先）

1. 按 `references/cover-prompt.md` 写提示词：**一个隐喻 + 最多一个短词**（通常是系统名），白底，主色与正文 `--accent` 一致，主体紧凑居中。
2. 用当前环境的生图后端生成一张横版宽图（如 1536x1024）。后端自动检测，不写死任何 API：

   | 环境 | 用什么 |
   |---|---|
   | Codex | 内置 `imagegen` |
   | Claude Code 等 | 已安装的生图技能（如本仓库的 `scientific-image-generation`）；本机装了 Codex CLI 时也可以 `codex exec` 调用其内置 imagegen |
   | 都没有 | 按同一份提示词手写 SVG，再用无头浏览器渲染成 PNG |

3. 裁切成公众号尺寸：

   ```bash
   python3 scripts/make_cover.py generated.png <out_dir>
   # -> <out_dir>/wechat-cover.png (900x383) 和 <out_dir>/wechat-cover-1x1.png (500x500)
   ```

   脚本按背景色找出主体位置再居中裁切；任何一张放不下主体时返回非零并告警，这时改提示词重新生成，不要硬裁。

4. 打开两张裁切结果亲眼看：文字拼写、有无多余文字/水印、缩略图是否还能认出主体。

> 🔒 生图时提示词会发给生图后端的服务商。封面提示词通常只含系统名和抽象图形，风险很低；但如果文章涉及未公开稿件，仍要先提醒用户。

### Step 5：交付

告诉用户：

- `wechat.html` 的路径和用法：浏览器打开 → 点"复制正文" → 公众号后台新建图文 → 正文区粘贴；页面顶部有标题、摘要可直接复制。
- 封面两张图的路径和各自用途。
- 报告里改动过/去掉的内容（公式改写、Mermaid 去留、外链移到文末）。
- **没有在真实公众号编辑器里粘贴验证过**（除非确实做过）。编辑器通常会自动转存内嵌图片；个别图片失败时删掉后手动上传同名原图。

## 质量标准

- ✅ 粘贴后不依赖任何外部 CSS/JS/字体，换设备打开排版一致
- ✅ 正文没有残留 `$`、`class`、`<a>`、Mermaid 源码
- ✅ 摘要是一句完整的话，不是截断的半句
- ✅ 封面只有一个视觉隐喻、最多一个词，两种裁切都不切掉主体
- ❌ 不要为了"好看"往封面加标题、副标题、图标堆砌
- ❌ 不要在提示词或文章里加入原文没有的数据
