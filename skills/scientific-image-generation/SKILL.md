---
name: "scientific-image-generation"
description: "用宿主模型自己的出图能力（Codex 内置图像生成；Claude Code 写 SVG/HTML/Python 绘图源码再渲染成 PNG）生成科研图，也可选用 Nano Banana API（Gemini API / Vertex AI / OpenRouter）出图，并把论文段落、图题、实验流程或机制描述整理成作图简报、run 目录、图片、机器检查和看图审查记录。默认无需任何外部生图 token。适用于论文配图、科研流程图、机制通路图、实验示意图和学术汇报图。"
---

# Scientific Image Generation（Claude / Codex 宿主出图）

默认出图不调用外部接口、不需要任何 token：图片由当前宿主 agent 自己产出，脚本只负责作图简报、run 目录、机器检查、审查记录和 manifest。唯一的例外是可选的 `api-image` 后端（Nano Banana，经 Gemini API、Vertex AI 或 OpenRouter 调用），只有用户选它时才需要对应凭据。

## 适用场景

用户要“画一张科研图”“生成论文配图”“把这段方法做成流程图”“按这张图继续改”时使用本 skill。

不要把它用于 PPT 排版或普通网页插图。

## 出图后端

| 后端 | 什么时候用 | 怎么出图 |
|---|---|---|
| `host-image-tool` | Codex（内置图像生成可用时），或宿主暴露了其他生图工具 | 把 `image_prompt.txt` 交给宿主的生图工具；从工具输出里拿到图片文件路径 |
| `code-render` | Claude Code 默认（Claude 不直接输出位图）；Codex 没有生图工具时的回退 | 宿主模型按 `image_prompt.txt` 写 `figure.svg`（首选）/ `figure.html` / `figure.py`（matplotlib，保存到 `$FIGURE_OUT`）/ `figure.dot` / `figure.mmd`，再用 `render_figure_source.py` 渲染成 PNG |
| `api-image` | 用户要求用 Nano Banana，或需要高质量写实图、在原图上局部修改，且有 Gemini API / Vertex AI / OpenRouter 任一凭据 | `generate_image_api.py --run-dir <run>` 调用对应接口，自动登记图片 |
| `auto` | 不确定时 | 有生图工具走 `host-image-tool`，否则走 `code-render`；不会自动选 `api-image` |

选择要点：

- 写实插画、细胞/分子示意、有质感的封面图：优先 `host-image-tool`。
- 流程图、方法框架图、对比图、标签必须逐字正确的图：优先 `code-render`；文字由代码写死，不会出现伪英文和错字，且源码可继续编辑。
- `code-render` 写 SVG 时：设置 `viewBox` 与目标尺寸一致、白底、中文用系统无衬线字体（`font-family="Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif"`）、标签逐字取自 `required_labels`、箭头用 `marker`，每条连线单独一个 `path`。
- 写实插画、细胞/分子示意、封面图、在上一版图上局部修改：`api-image`（Nano Banana）效果通常最好；标签仍要靠看图审查逐字核对。
- 都不可用时（没有生图工具，也没有任何渲染器）：用 `register_generated_image.py --blocked-reason` 记录 blocker，不伪造图片。

## Nano Banana（api-image 后端）

`prepare_image_run.py --backend api-image --image-provider <gemini|vertex|openrouter>` 选择调用方式，`generate_image_api.py --run-dir <run>` 出图并自动登记（`--provider` 可临时覆盖 run 里记录的 provider）。凭据只从环境变量（或 gcloud）读取，不写入任何文件。

| provider | 凭据 | 接口 | 默认模型 |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY`（Google AI Studio；`GOOGLE_API_KEY` 也可） | `POST {GEMINI_API_BASE}/models/{model}:generateContent`，默认 `https://generativelanguage.googleapis.com/v1` | `gemini-nano-banana-2.1` |
| `vertex`（OAuth） | `GOOGLE_CLOUD_PROJECT`，可选 `GOOGLE_CLOUD_LOCATION`（默认 `global`），`VERTEX_ACCESS_TOKEN` 或已登录的 `gcloud`（自动取 `gcloud auth print-access-token`） | `https://aiplatform.googleapis.com/v1/projects/{p}/locations/global/publishers/google/models/{model}:generateContent`；非 global 区域用 `{loc}-aiplatform.googleapis.com` | `gemini-nano-banana-2.1` |
| `vertex`（express） | `VERTEX_API_KEY`，无需项目 | `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent` | 同上，但 express 模式目前不提供 `gemini-nano-banana-2.1`，需 `--model gemini-3-pro-image` 或 `gemini-3.1-flash-image` |
| `openrouter` | `OPENROUTER_API_KEY` | `POST {OPENROUTER_API_BASE}/images`，默认 `https://openrouter.ai/api/v1`（OpenRouter 独立的 Image API，不是 chat completions） | `google/gemini-nano-banana-2.1` |

```bash
# Gemini API
export GEMINI_API_KEY=...
python scripts/prepare_image_run.py --prompt "..." --aspect-ratio 16:9 --image-size 2K --backend api-image
python scripts/generate_image_api.py --run-dir <run>

# Vertex AI（gcloud 已登录）
export GOOGLE_CLOUD_PROJECT=my-project
python scripts/prepare_image_run.py --prompt "..." --backend api-image --image-provider vertex
python scripts/generate_image_api.py --run-dir <run>

# OpenRouter
export OPENROUTER_API_KEY=...
python scripts/prepare_image_run.py --prompt "..." --backend api-image --image-provider openrouter
python scripts/generate_image_api.py --run-dir <run> [--model gemini-3-pro-image]   # 无 / 时自动补 google/
```

- 模型：`--model` 优先，其次环境变量 `GEMINI_IMAGE_MODEL`（gemini / vertex）或 `OPENROUTER_IMAGE_MODEL`（openrouter）。文档里的同系列模型：`gemini-3.1-flash-image`（Nano Banana 2）、`gemini-3.1-flash-lite-image`（2 Lite）、`gemini-3-pro-image`（Pro，适合 4K 精细图）、`gemini-2.5-flash-image`（旧版）。模型 ID 更新很快，报 404 时先查各平台的模型列表（OpenRouter 用 `GET /api/v1/images/models`）。
- 画幅和尺寸来自 `run_input.json`：Gemini/Vertex 发 `imageConfig.aspectRatio` / `imageSize`，OpenRouter 发 `aspect_ratio` / `resolution`；尺寸为 1K/2K/4K，不同模型支持范围不同，被拒时降到 1K 或换模型。
- 参考图：edit run 的 `reference_image.*` 随请求发送（Gemini/Vertex 用 `inline_data`，OpenRouter 用 `input_references` 的 data URL）；`--no-reference` 可不发。
- 证据：`request.json`（凭据和图片 base64 都被替换，只记参考图文件名和 SHA-256）、`response_summary.json`（结束原因、安全反馈、用量/费用、模型附带文字），`metadata.json` 和 manifest 记录 provider、模型、鉴权方式和用量。OpenRouter 返回 URL 时会立即下载（链接 24 小时过期）。
- 失败一律写 `blocker.json`：缺凭据、HTTP 报错（如 OpenRouter 402 余额不足）、被安全策略拦截、返回中没有图片。修好原因后可在同一 run 目录重跑，成功时会清除旧 blocker。

数据外发：选用这个后端时，`image_prompt.txt` 的全文和参考图会发送给 Google（gemini / vertex）或 OpenRouter 及其上游模型提供方（openrouter）。`image_prompt.txt` 不包含 `source_context` 原文段落，但用户写进 prompt 的内容会原样发出。未发表的论文内容，用前先向用户确认。

## 用户可见进度输出

生成或改图时要主动输出有效状态，减少用户等待。进度绑定真实工作流节点，中文请求用中文；每条前缀当前本地时间或已耗时，但正文要保持科研图执行口径：有参数、有判断、有质量标准，不写成后台日志，也不写成陪聊安慰。只增加用户可见进度说明，不改变 run 目录规范或检查脚本。

进度文字要专业、短、具体。不要堆内部文件名，也不要把话说得太口语。优先输出用户真正关心的技术信息：图型、画幅、标签约束、参考图角色、生成状态、尺寸/格式、语义复查维度、是否可交付、下一轮修订策略。不必每轮全发，按真实发生的节点输出。

中文进度样式：

```text
[21:24 | 任务识别]
我先确认这张图的类型、画幅和必须保留的科学信息，避免生成时跑偏或自行扩写无关内容。

[21:25 | 作图简报]
图型、画幅、必须标签和禁止项已经定好；如果使用参考图，只参考指定部分，不改写原始科学含义。

[21:26 | 出图]
这张是带精确标签的四步流程图，走代码绘制：先写 SVG，再渲染成 1600 x 900 PNG。

[21:27 | 图片生成]
图片已生成，尺寸和格式可读。接下来逐项看标签、伪英文、水印和版式层次。

[21:32 | 机器检查]
文件本身是有效图片，尺寸和格式没问题；但语义和标签还要复查，不能只看机器检查就交付。

[21:34 | 复查结论]
主体结构基本成立，关键标签也在；但仍发现一处文字或语义问题，这版适合做草稿，正式版建议再改一轮。

[21:35 | 交付索引]
本轮生成、检查和复查结论已经整理好；交付时会说明这张图能用到什么程度，以及下一轮该怎么改。
```

优先在这些节点输出：任务识别、作图约束确认、选择后端、出图、机器检查完成、复查开始、复查完成、manifest 更新、blocker、最终交付。每条包含一个真实中间信息即可，例如后端、图片尺寸、必须标签数量、复查发现、是否建议二次修改。文件名可以出现，但不要每条都堆文件名；只在用户需要追溯或出现 blocker 时明确证据文件。遇到 blocker 时说明证据文件和原因（如宿主没有生图工具、渲染器不可用、参考图不存在），明确不会伪造图片。

## 默认交付深度

默认交付不是只出一张图。除非用户明确说"只 dry-run"或"只出草图"，出图后必须完成复查闭环。所有脚本在 `scripts/` 下，从任意目录用绝对路径调用即可；run 目录默认建在当前工作目录的 `scientific_image_skill_runs/` 下。

1. **准备**：`prepare_image_run.py` 写 `figure_spec.json`、`run_input.json`、`image_prompt.txt`，状态为 `awaiting_image`，并打印目标像素尺寸。
2. **出图**：按 `image_prompt.txt` 出图。
   - `host-image-tool`：调用宿主生图工具，拿到图片文件路径。
   - `code-render`：在 run 目录写 `figure.svg` 等源码，运行 `render_figure_source.py --source <run>/figure.svg --out <run>/render.png --width W --height H`。
   - `api-image`：运行 `generate_image_api.py --run-dir <run>`，它会直接完成登记，跳过第 3 步。
3. **登记**：`register_generated_image.py --run-dir <run> --image <图片> --backend <后端> --tool "<具体工具>" [--render-source <源码>]`，写 `generated_image.*`、`check.json`，更新 metadata 和 manifest。
4. **看图审查**：`host_semantic_review.py prompt --run-dir <run>` 打印 Critic 指令和图片路径。宿主必须真的打开图片（Claude Code 用 Read 读图，Codex 用图片查看），按指令输出 JSON，再用 `host_semantic_review.py record --run-dir <run> --review-json <file> --reviewer <模型名>` 落成 `semantic_review.json`。格式不合格会被拒绝，按提示修正后重录。不要不看图就写审查。
5. **一次自动修订**：`run_scientific_image_workflow.py repair --run-dir <run>`。如果审查结论是 `edit`/`regenerate`、缺必须标签、出现禁止项，或 `content_accuracy`/`text_readability`/`artifact_severity` 判为 FAIL，它会写 `auto_repair_prompt.json`，并新建一个 `edit_*` run（`image_prompt.txt` 以 `revised_description` 为核心）。对新 run 重复步骤 2-4。最多自动修订一轮，第二次调用会返回 `NO_REPAIR`。
6. **收尾**：`run_scientific_image_workflow.py finalize --run-dir <最终 run> [--initial-run-dir <首版 run>]`，写 `manual_review.md` 代理复查摘要、重建 `figure_manifest.json`、写 `workflow_status.json`。
7. 最终回复必须说明：图片路径、后端、检查结果、复查结论、是否达到交付标准、下一轮修改 prompt。

不确定下一步时运行 `run_scientific_image_workflow.py status --run-dir <run>`，它会告诉你当前在等哪一步。

`api-image` 的修订：审查结论为 `edit` 时，`repair` 会自动把上一版图作为参考图（`reference_role=edit_image`）放进新 run，让 Nano Banana 在原图上改；结论为 `regenerate` 时整图重画。

`code-render` 的额外好处：修订时直接改源码再渲染，比整图重生更稳；改动要写进新的 edit run，不覆盖上一轮。

## 工作流

1. 先写作图简报：图的用途、核心信息、必须出现的标签、画幅比例、风格。
2. 再整理生成提示词：中文标签优先保留中文，科研术语按用户材料原样保留。
3. 把作图简报落成 `figure_spec.json`：必须标签、布局、风格、参考图角色、允许/禁止修改项。
4. 选择后端并按 `image_prompt.txt` 出图。
5. 登记图片，保存图片、源码（如有）和检查记录。
6. 生成图片后做机器检查和宿主模型看图语义审查；若有基础问题，自动输出 Critic 建议和 revised description，再用 revised description 重新文生图一次。
7. 对最终版写代理复查摘要，并给出是否需要用户继续判断或修改。
8. 根据用户反馈继续改图，不覆盖上一版；这属于人工接管后的下一轮，不进入无限自动迭代。
9. 生成或修改后重建 `figure_manifest.json`，把任务、图片、检查、复查、自动修订状态和人工判断串起来。

## 参考 PaperBanana 的地方

这个 skill 不照搬 PaperBanana 的模型调用栈，但照搬它的工作流结构：

| PaperBanana 做法 | 本 skill 对应实现 |
|---|---|
| `generate_diagram(source_context, caption, ...)` | `prompt` + 作图简报，生成单张科研图 |
| `continue_run(run_id, feedback, ...)` | `--run-kind edit`、`--source-run`、`--reference-image`，每次修改新建 run |
| `metadata.json` / `run_input.json` | 每轮生成或 blocker 都写 `run_input.json`、`metadata.json` 和 `figure_manifest.json` |
| `batch_manifest.yaml` | 多图交付用 `build_figure_package.py` 汇总每张图的 manifest，不用口头一次性塞多张图 |
| `batch_report.json` / checkpoint | 图包目录写 `package_plan.json`、`package_checkpoint.json`、`figure_package.json` |
| `evaluate_diagram` | 当前用 `check.json` + `semantic_review.json` + `manual_review.md`；有参考图时再补结构化对比 |
| 多候选择优 | 多个 run 完成后用 `select_figure_variant.py` 按完成状态、机器检查和人工质量轴选择候选 |

对齐源项目的关键链路是 Critic 看图后修改描述，再由 Visualizer 基于 revised description 重新文生图；本 skill 不默认接入 PaperBanana 的参考图库、Retriever、多候选并发筛选或三轮以上 Critic 循环。核心原则：每张图都必须有可追溯 run，不只保存最终图片。

Critic 输出字段要对齐 PaperBanana：

- `critic_suggestions`：具体审查意见；如果无需修改，写 `No changes needed.`。
- `revised_description`：合并修正后的完整详细描述；如果无需修改，写 `No changes needed.`。

Visualizer 重生 prompt 要以 revised description 为核心：

```text
Render an image based on the following detailed description: {revised_description}
Note that do not include figure titles in the image. Diagram:
```

本 skill 可以在这个 prompt 后追加中文标签、禁止项、画幅要求和“一轮自动修订”边界，但不能跳过 `critic_suggestions` / `revised_description` 这组中间产物。

## Crafter 启发但不照搬的地方

Crafter 的有效启发是“科研图是结构化语义组件组合，不只是更长 prompt”。本 skill 只吸收轻量契约，不引入 OpenRouter、多 agent、SAM3 或 raster-to-SVG 依赖：

- 用 `figure_spec.json` 作为单张图的结构化事实源，避免多轮 prompt 追加后互相矛盾。
- 用 `reference_role` 明确参考图用途，避免“保留结构”“借用元素”“润色草图”“编辑当前图”混在一起。
- 用固定质量轴复核生成结果：`content_accuracy`、`layout_quality`、`text_readability`、`aesthetic_quality`、`artifact_severity`。
- 默认完整流程由宿主模型自己看图做语义复核，结果写 `semantic_review.json`，不替代本地机器检查和代理复查摘要。
- 继续坚持本 skill 的核心优势：不依赖外部 token、每轮 run 可审计、不伪造图片和审查。

## Figure spec 契约

`figure_spec.json` 是每轮 run 的结构化作图简报，字段包括：

- `figure_kind`：`workflow`、`mechanism schematic`、`method diagram`、`comparison figure` 等。
- `caption` / `communicative_intent`：图题和要传达的科学信息。
- `required_labels` / `forbidden_labels`：必须出现和必须避免的标签。
- `layout_brief`：如“横向四步流程、每步一个卡片、箭头单向连接、留白充足”。
- `style_brief`：如“白底、蓝绿色学术配色、扁平风格、无广告感”。
- `reference_role`：空值或 `preserve_structure`、`use_elements`、`refine_sketch`、`edit_image`。
- `preserve_constraints`、`allowed_changes`、`disallowed_changes`：续改时的保留项、允许变化项和禁止变化项。

可从 JSON 直接构造 dry-run 或真实 run：

```powershell
python scripts/dry_run_scientific_image.py --input-json params.json
python scripts/prepare_image_run.py --input-json params.json
```

`params.json` 可以直接放上述字段，也可以放在 `figure_spec` 子对象里。命令行参数优先级高于 JSON 默认值。

## 提示词质量改进建议

生图模型在信息过密时容易出现小字拥挤、英文拼写错误和装饰元素偏多；收窄提示词后结构更干净，但图内语义细节会减少。代码绘制没有错字问题，但容易排版拥挤、箭头穿过卡片。因此提示词应优先控制信息层级：

- 主标签控制在 3-6 个短词或短句，逐字列出必须出现的标签。
- 不要要求模型生成密集说明文字；长解释放到图注、PPT 文本或后期排版层。
- 如果必须有英文术语，逐字给出并说明“不要改写、不要拼写变体”；不确定时优先中文。
- 明确版式约束，如“横向四步流程、每步一个卡片、箭头单向连接、留白充足”。
- 明确禁止项，如“小号正文、伪论文截图、随机英文、额外步骤、广告风格、水印”。
- 二次修改时只列允许变化的局部，避免重新生成整张图导致结构漂移。

检查报告必须写出“提示词质量改进建议”：指出本轮是标签问题、结构问题、语义缺失、风格偏差还是文字过密，并给出下一轮可直接复用的修改 prompt。

## 二次修改入口

用户说“按这张图继续改”“保留主体，只改标签/配色/布局/局部元素”时，走二次修改入口。默认先把用户反馈、上一轮 `semantic_review.json` 和原始 `figure_spec.json` 合成新版 prompt/spec 再出图。`code-render` 的上一轮有 `render_source.*` 时，直接复制源码局部修改后重新渲染。只有用户明确要求按参考图结构修改，且宿主生图工具支持图像输入时，才传 `--reference-image`；该图会被复制到新 run 的 `reference_image.*` 并交给生图工具。

1. 先确认上一轮图片路径或用户提供的参考图路径。
2. 先声明 `reference_role`：`preserve_structure` 保留结构，`use_elements` 借用元素，`refine_sketch` 把草图润色成成品，`edit_image` 编辑当前图；如果不走参考图输入，则记录为文本重生。
3. 把修改要求写进新的 `prompt`，明确“保留上一版的主体结构/构图/画幅”，再列出只允许变动的部分。
4. 用 `prepare_image_run.py --run-kind edit --source-run <上一轮> [--reference-image <图>]` 新建 run，不要覆盖上一轮 run 目录。
5. 出图、登记、审查、收尾，与首版相同。
6. 如果参考图不存在或宿主无法出图，只写 `blocker.json`，不要伪造图片或检查结果。

二次修改 run 目录规范：

- 所有运行目录放在 `scientific_image_skill_runs/<session_slug>/<run_slug>/`。
- 首次生成目录为 `gen_YYYYMMDD_HHMMSS/`（`prepare_image_run.py` 自动创建，可用 `--output-dir` 指定 session 目录）。
- 二次修改必须用新的 `edit_YYYYMMDD_HHMMSS/` 目录，放在当前 session slug 下，或放在 `edit_YYYYMMDD/` session 下。
- 二次修改目录必须写 `source_run.txt`，内容是上一轮 run 目录的绝对路径或相对稳定目录路径。
- 二次修改目录必须保留自己的 `image_prompt.txt`、`render_source.*`（如有）、`generated_image.*`、`check.json`、`semantic_review.json` 和复查报告，不复写上一轮文件。
- 如果只做 dry-run，输出仍要能看出 `reference_image_path`、参考图 SHA-256 和 `source_run`。

二次修改必须保留这些 lineage 字段：

- 父 run：`source_run_id`、`source_run_dir`、`parent_run_input_path`、`parent_metadata_path`、`parent_manifest_path`。
- 父图：`source_image_path`、`source_image_sha256`。
- 用户反馈：`feedback`、`preserve_constraints`、`allowed_changes`、`disallowed_changes`、`new_prompt_delta`。
- 语义继承：`caption`、`communicative_intent`、`figure_kind`、`required_labels`。

dry-run 二次修改示例：

```powershell
python scripts/dry_run_scientific_image.py --prompt "保留上一版四步流程，只把第三步改成模型推断，并统一蓝绿色学术风格" --reference-image "path/to/generated_image.png" --reference-role preserve_structure --allowed-changes 替换第三步标签 统一配色 --disallowed-changes 新增步骤 改变画幅
```

## 结果检查

生成后至少检查：

- 图片文件是否存在。
- 图片文件字节数是否大于 0。
- 图片类型是否为 PNG、JPEG 或 WebP。
- 图片尺寸是否可读取。
- 是否被 blocker 阻断（宿主无法出图、渲染失败、参考图不存在）。
- 是否符合图题和提示词。
- 必须标签是否缺失或错乱。
- 是否有水印、广告感、模糊文字。

检查结果写入同一轮 run 目录。

机器检查字段至少包括：

| 字段 | 说明 |
|---|---|
| `image_exists` | 图片文件是否存在 |
| `file_size_bytes` | 图片字节数，无法读取时为 `null` |
| `image_type` | `png`、`jpeg`、`webp`、`unknown` 或 `missing` |
| `mime_type` | 推断出的 MIME 类型 |
| `width` / `height` | 可读取尺寸；不可读时为 `null` |
| `has_blocker` | 本轮是否存在 blocker |
| `blocker_reason` | blocker 原因 |
| `manual_review_required` | 固定为 `true`，用于提醒仍需人工检查语义和标签 |

`check.json` 还会包含：

- `machine_check`：本地可判定的存在性、格式、尺寸、宽高比和像素问题。
- `quality_review_axes`：人工或 VLM 后续复核用的五个质量轴。分数默认为 `null`，不要伪造模型判断。
- `semantic_review`：使用 `--semantic-check` 时生成的待填写语义复核占位。

## 宿主模型看图审查

生成并通过机器检查后，由宿主模型自己看图审查，不需要外部 VLM key：

```bash
python scripts/host_semantic_review.py prompt --run-dir <run>
# 宿主打开 prompt 打印的图片路径，按 Critic 规则输出 JSON，保存为 review.json
python scripts/host_semantic_review.py record --run-dir <run> --review-json review.json --reviewer <模型名>
```

`semantic_review.json` 必须包含：

- `provider`（固定为 `host-agent`）、`model`（审查模型）、`status`。
- `quality_review_axes`：五个质量轴的 `PASS` / `FAIL` / `UNCERTAIN`、分数和理由。
- `observed_labels`、`missing_required_labels`、`forbidden_labels_seen`、`issues`。
- `critic_suggestions`、`revised_description`：对齐 PaperBanana Critic 输出；无需修改时写 `No changes needed.`。
- `overall_ready_to_ship`、`recommended_next_action`（`deliver|edit|regenerate|manual_review`）、`next_edit_prompt`。

审查要真实：逐个核对必须标签是否逐字出现，检查错字、伪英文、箭头方向、元素重叠和截断。自己生成的图也按同一标准挑错，不因为是自己画的就放宽。

可选：`semantic_review_dashscope.py` 仍保留，可以用 DashScope Qwen 做第二意见（需要 `DASHSCOPE_API_KEY`），默认流程不再依赖它。

人工检查报告建议使用 `templates/check_report_template.md`，字段至少包括：

- run 目录、源 run 目录、图片路径、请求摘要和生成时间。
- 机器检查摘要：存在性、字节数、类型、尺寸、blocker、后端。
- 语义检查：是否符合图题、必须标签是否出现、是否有错字/伪英文/水印/广告感。
- 图像质量：布局、留白、层级、颜色、图标一致性、是否适合论文或汇报。
- 提示词质量改进建议：本轮问题归因、下一轮 prompt、negative prompt、是否改用另一种后端或参考图。

## Dry-run

只想预览宿主会收到的作图指令时运行：

```bash
python scripts/dry_run_scientific_image.py --prompt "画一个四步科研流程图：问题定义、数据整理、模型生成、结果检查"
```

dry-run 只输出 `host_prompt`、目标尺寸和 figure spec，不建 run 目录、不出图。参考图只记录路径、MIME、字节数和 SHA-256，不输出图片内容。

## Figure manifest

生成或二次修改后运行：

```powershell
python scripts/build_figure_manifest.py --run-dir "scientific_image_skill_runs/session_a/gen_YYYYMMDD_HHMMSS"
```

`figure_manifest.json` 至少记录：

- `run_id` 和 `run_dir`
- `figure_id`、`source_run`、`feedback`、`lineage`
- `caption`、`intent`、`figure_kind`、`required_labels`、`style_brief`
- `prompt`、`host_prompt`、negative prompt、画幅、目标尺寸、后端和具体工具
- `image_prompt.txt`、`render_source.*`、`reference_image.*`、图片、`check.json`、`semantic_review.json`、`manual_review.md`、`blocker.json`
- 输出摘要：图片路径、SHA-256、MIME、宽高
- 质量摘要：机器检查、人工检查摘要、是否需要重生成
- 机器检查摘要
- 人工检查摘要

它的作用类似 PaperBanana 的 run metadata/package report：让后续 continue、批量、复核和交付时知道这张图从哪里来、怎么生成、是否合格。

## Figure package

多张图或一个论文图包完成后，汇总所有 `figure_manifest.json`：

```powershell
python scripts/build_figure_package.py --input "scientific_image_skill_runs/session_a" --package-dir "scientific_image_skill_runs/packages/paper_demo" --title "论文图像生成结果包"
```

图包目录会写：

- `package_plan.json`：锁定本次要交付的图、图题、类型、意图和 manifest 路径。
- `package_checkpoint.json`：记录每个 item 的状态、run id、manifest 和错误，便于后续恢复。
- `figure_package.json`：给用户查看的交付清单，统计完成/阻塞数量和每张图的图片路径、caption、错误。

这对应 PaperBanana 的 `orchestration_plan.json`、`orchestration_checkpoint.json` 和 `figure_package.json` 思路。

## Variant selection

如果同一张图生成了多个候选 run，先为每个 run 生成 `figure_manifest.json`，再运行：

```powershell
python scripts/select_figure_variant.py --input "scientific_image_skill_runs/session_a" --out "scientific_image_skill_runs/session_a/variant_selection.json"
```

选择逻辑只使用可审计字段：完成状态、是否有图片、`machine_check` 是否通过、`semantic_review_summary`、人工填写的 `quality_review_axes` 分数和 blocker。没有人工或模型语义分数时不会伪造语义判断。

## 完整示例

Claude Code（code-render）：

```bash
S=<本技能目录>/scripts
python $S/prepare_image_run.py --prompt "横向四步科研流程图：问题定义、数据整理、模型生成、结果检查" \
  --required-labels 问题定义 数据整理 模型生成 结果检查 --aspect-ratio 16:9 --backend code-render
# 输出 Run directory: <run>，目标尺寸 1024 x 576
# 宿主按 <run>/image_prompt.txt 写 <run>/figure.svg
python $S/render_figure_source.py --source <run>/figure.svg --out <run>/render.png --width 1024 --height 576
python $S/register_generated_image.py --run-dir <run> --image <run>/render.png --backend code-render --tool "svg + headless-chrome" --render-source <run>/figure.svg
python $S/host_semantic_review.py prompt --run-dir <run>
# 宿主读图 -> review.json
python $S/host_semantic_review.py record --run-dir <run> --review-json review.json --reviewer claude
python $S/run_scientific_image_workflow.py repair --run-dir <run>      # 需要时生成一轮修订 run
python $S/run_scientific_image_workflow.py finalize --run-dir <run>
```

Codex（host-image-tool）：前两步相同，`--backend host-image-tool`；出图时把 `image_prompt.txt` 交给 Codex 内置图像生成，拿到图片路径后用 `register_generated_image.py --image <路径> --backend host-image-tool --tool "codex image generation"` 登记，后续审查、修订、收尾不变。

run 目录最终包含：

- `figure_spec.json`、`run_input.json`、`image_prompt.txt`：作图契约和发给宿主的出图指令。
- `render_source.*`：`code-render` 的绘图源码，可直接改了重渲染。
- `reference_image.*`：edit run 的参考图副本。
- `generated_image.*`、`check.json`：登记后的图片和机器检查。
- `semantic_review.json`：宿主模型看图审查。
- `auto_repair_prompt.json`：触发一次自动修订时的 Critic 建议、revised description 和新 prompt。
- `manual_review.md`、`workflow_status.json`、`metadata.json`、`figure_manifest.json`：复查摘要、流程状态和交付索引。
- `request.json`、`response_summary.json`：仅 `api-image` 后端，脱敏请求和响应摘要。
- `blocker.json`：宿主无法出图、渲染失败、API 失败或参考图不存在时写入。

不要手写或伪造图片、`check.json` 或审查结论；`check.json` 只能由 `register_generated_image.py` 生成，审查必须基于真实看图。
