# 验收说明

## 当前定位

这个 skill 面向由宿主 agent 自己出图的科研配图流程：Codex 使用内置图像生成，Claude Code 写 SVG/HTML/Python 绘图源码后渲染成 PNG。它参考 PaperBanana 的 run、continue、manifest、package 思路，不调用任何外部生图接口，也不需要 token。

它负责：

- 把论文段落、图题或实验流程整理成作图简报（`figure_spec.json`）和宿主出图指令（`image_prompt.txt`）。
- 登记宿主产出的图片，做机器检查。
- 记录宿主模型的看图语义审查，最多自动修订一轮。
- 按上一版继续改图时保留父 run、父图、反馈和修改约束。
- 把单图或多图汇总成可交付的 `figure_package.json`。

## 必须通过

```bash
uv run --no-project --with pytest python -m pytest -q -p no:cacheprovider tests
python scripts/dry_run_scientific_image.py --prompt "画一个四步科研流程图：问题定义、数据整理、模型生成、结果检查"
python scripts/prepare_image_run.py --prompt "画一个四步科研流程图：提出问题、检索论文、整理证据、输出结论" --backend code-render
python scripts/render_figure_source.py --source <run>/figure.svg --out <run>/render.png --width 1024 --height 1024
python scripts/register_generated_image.py --run-dir <run> --image <run>/render.png --backend code-render --render-source <run>/figure.svg
python scripts/host_semantic_review.py prompt --run-dir <run>
python scripts/host_semantic_review.py record --run-dir <run> --review-json review.json --reviewer <模型名>
python scripts/run_scientific_image_workflow.py finalize --run-dir <run>
```

预期：

- 任何步骤都不读取生图 token，不发网络请求。
- `prepare_image_run.py` 写 `figure_spec.json`、`run_input.json`、`image_prompt.txt`、`metadata.json`（`awaiting_image`）、`figure_manifest.json`。
- 宿主无法出图时用 `--blocked-reason` 写 `blocker.json`，不会出现伪造图片。
- `check.json` 只由 `register_generated_image.py` 生成，包含 `machine_check` 和 `quality_review_axes`。
- `host_semantic_review.py record` 拒绝格式不合格的审查；合格时写 `provider=host-agent` 的 `semantic_review.json`。
- `repair` 最多生成一轮 edit run；第二次返回 `NO_REPAIR`。
- edit run 的 `continue_from` 记录父 run、父图 SHA-256、反馈、参考图角色、允许和禁止修改项。
- `figure_manifest.json` 和 `figure_package.json` 能追踪后端、`image_prompt.txt`、`render_source.*`、`semantic_review.json` 和 `overall_ready_to_ship`。
- variant selector 只按 manifest 中的可审计字段选择候选。

## 可交付文件

- `SKILL.md`
- `agents/openai.yaml`
- `scripts/host_image_common.py`
- `scripts/dry_run_scientific_image.py`
- `scripts/prepare_image_run.py`
- `scripts/render_figure_source.py`
- `scripts/register_generated_image.py`
- `scripts/host_semantic_review.py`
- `scripts/run_scientific_image_workflow.py`
- `scripts/check_generated_image.py`
- `scripts/build_figure_manifest.py`
- `scripts/build_figure_package.py`
- `scripts/select_figure_variant.py`
- `scripts/semantic_review_dashscope.py`（可选第二意见，非默认）
- `templates/check_report_template.md`

## 出图边界

- 图片只能来自宿主生图工具的真实输出，或宿主写的绘图源码的真实渲染结果。
- 审查必须基于宿主真实打开图片后的观察。
