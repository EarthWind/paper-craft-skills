"""Shared helpers for host-model image generation (Claude Code / Codex).

By default no network access and no API tokens: the image is produced by the
host agent (Codex built-in image generation, or Claude rendering SVG/HTML/Python
figure code). The optional api-image backend calls an image model API
(Gemini "Nano Banana" via Gemini API, Vertex AI or OpenRouter) through
generate_image_api.py; only that script reads credentials, from the environment.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

DEFAULT_NEGATIVE_PROMPT = "水印，模糊文字，错乱标签，低清晰度，广告风格"
BACKENDS = ["auto", "host-image-tool", "code-render", "api-image"]
IMAGE_PROVIDERS = ["gemini", "vertex", "openrouter"]
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-nano-banana-2.1"
BACKEND_NOTES = {
    "host-image-tool": "宿主自带的生图工具（Codex 内置图像生成，或宿主暴露的其他生图工具）直接出位图",
    "code-render": "宿主模型写 SVG/HTML/Python/Graphviz/Mermaid 绘图源码，再用 render_figure_source.py 渲染成 PNG（Claude Code 默认）",
    "api-image": "脚本直接调用 Nano Banana 生图 API（Gemini API / Vertex AI / OpenRouter，需要对应凭据），用 generate_image_api.py 出图",
    "auto": "有生图工具就用 host-image-tool，否则用 code-render",
}
IMAGE_NAMES = ["generated_image.png", "generated_image.jpg", "generated_image.jpeg", "generated_image.webp"]
REFERENCE_ROLES = ["", "preserve_structure", "use_elements", "refine_sketch", "edit_image"]
ASPECT_RATIOS = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "21:9": (21, 9),
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc)}


def file_digest(path):
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def infer_mime(path):
    suffix = Path(path).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def find_image(run_dir):
    for name in IMAGE_NAMES:
        path = Path(run_dir) / name
        if path.exists():
            return path
    return None


def target_pixels(aspect_ratio, image_size):
    w, h = ASPECT_RATIOS.get(str(aspect_ratio), (1, 1))
    long_side = {"1K": 1024, "2K": 2048, "4K": 4096}.get(str(image_size).upper(), 1024)
    if w >= h:
        return long_side, round(long_side * h / w)
    return round(long_side * w / h), long_side


def add_figure_arguments(parser):
    parser.add_argument("--prompt")
    parser.add_argument("--negative-prompt", default=DEFAULT_NEGATIVE_PROMPT)
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--image-size", default="1K", help="1K / 2K / 4K, long side of the target image.")
    parser.add_argument("--backend", default="auto", choices=BACKENDS, help="How the image will be produced.")
    parser.add_argument("--image-provider", default="gemini", choices=IMAGE_PROVIDERS, help="Image API provider for the api-image backend.")
    parser.add_argument("--reference-image", help="Local image for an edit/reference run.")
    parser.add_argument("--source-run", help="Prior run directory for edit/reference runs.")
    parser.add_argument("--source-context", help="Original paper paragraph, method description, or user material behind the figure.")
    parser.add_argument("--caption", help="Short caption or figure title.")
    parser.add_argument("--intent", help="Scientific message this figure should communicate.")
    parser.add_argument("--figure-kind", default="workflow", help="workflow, mechanism schematic, method diagram, comparison figure, etc.")
    parser.add_argument("--required-labels", nargs="*", default=[], help="Labels that must appear in the figure.")
    parser.add_argument("--forbidden-labels", nargs="*", default=[], help="Labels or text patterns that must not appear.")
    parser.add_argument("--style-brief", help="Compact visual style brief.")
    parser.add_argument("--feedback", help="User feedback for edit/continue-style runs.")
    parser.add_argument("--reference-role", default="", choices=REFERENCE_ROLES, help="How to interpret the reference image, if present.")
    parser.add_argument("--preserve-constraints", nargs="*", default=[], help="Elements to preserve from the parent run.")
    parser.add_argument("--allowed-changes", nargs="*", default=[], help="Specific changes allowed in an edit run.")
    parser.add_argument("--disallowed-changes", nargs="*", default=[], help="Changes that must not happen in an edit run.")
    parser.add_argument("--layout-brief", help="Structured layout constraint such as horizontal four-step flow or 2x2 grid.")
    parser.add_argument("--input-json", help="Path to JSON containing prompt fields and optional figure_spec.")


def load_input_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    figure_spec = data.get("figure_spec") or {}
    return {
        "prompt": data.get("prompt", ""),
        "negative_prompt": data.get("negative_prompt", data.get("negativePrompt", DEFAULT_NEGATIVE_PROMPT)),
        "aspect_ratio": data.get("aspect_ratio", data.get("aspectRatio", "1:1")),
        "image_size": data.get("image_size", data.get("imageSize", "1K")),
        "backend": data.get("backend", "auto"),
        "image_provider": data.get("image_provider", "gemini"),
        "reference_image": data.get("reference_image"),
        "source_run": data.get("source_run"),
        "source_context": data.get("source_context"),
        "caption": data.get("caption", figure_spec.get("caption")),
        "intent": data.get("intent", data.get("communicative_intent", figure_spec.get("communicative_intent"))),
        "figure_kind": data.get("figure_kind", figure_spec.get("figure_kind", "workflow")),
        "required_labels": data.get("required_labels", figure_spec.get("required_labels", [])),
        "forbidden_labels": data.get("forbidden_labels", figure_spec.get("forbidden_labels", [])),
        "style_brief": data.get("style_brief", figure_spec.get("style_brief")),
        "feedback": data.get("feedback"),
        "reference_role": data.get("reference_role", figure_spec.get("reference_role", "")),
        "preserve_constraints": data.get("preserve_constraints", figure_spec.get("preserve_constraints", [])),
        "allowed_changes": data.get("allowed_changes", figure_spec.get("allowed_changes", [])),
        "disallowed_changes": data.get("disallowed_changes", figure_spec.get("disallowed_changes", [])),
        "layout_brief": data.get("layout_brief", figure_spec.get("layout_brief")),
    }


# argparse defaults that an --input-json value may replace
_ARG_DEFAULTS = {
    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
    "aspect_ratio": "1:1",
    "image_size": "1K",
    "backend": "auto",
    "figure_kind": "workflow",
}


def merge_input_json(args):
    if not getattr(args, "input_json", None):
        return
    auto = load_input_json(args.input_json)
    for name, value in auto.items():
        if not hasattr(args, name):
            continue
        current = getattr(args, name)
        if current in (None, "", []) or (name in _ARG_DEFAULTS and current == _ARG_DEFAULTS[name]):
            setattr(args, name, value)


def build_figure_spec(args):
    return {
        "schema": "sci_figure_spec_v1",
        "figure_kind": args.figure_kind,
        "caption": args.caption,
        "communicative_intent": args.intent or args.caption,
        "required_labels": args.required_labels,
        "forbidden_labels": args.forbidden_labels,
        "layout_brief": args.layout_brief,
        "style_brief": args.style_brief,
        "reference_role": args.reference_role,
        "preserve_constraints": args.preserve_constraints,
        "allowed_changes": args.allowed_changes,
        "disallowed_changes": args.disallowed_changes,
    }


def build_host_prompt(args):
    """The exact instruction the host agent uses to produce the image."""
    width, height = target_pixels(args.aspect_ratio, args.image_size)
    lines = [args.prompt.strip(), ""]
    if args.figure_kind:
        lines.append(f"图类型：{args.figure_kind}")
    if args.caption:
        lines.append(f"图题/主题（不要把图题写进图里）：{args.caption}")
    if args.intent:
        lines.append(f"要传达的科学信息：{args.intent}")
    if args.layout_brief:
        lines.append(f"版式：{args.layout_brief}")
    if args.style_brief:
        lines.append(f"风格：{args.style_brief}")
    if args.required_labels:
        lines.append("必须逐字出现的标签（不要改写、不要拼写变体）：" + "、".join(args.required_labels))
    if args.forbidden_labels:
        lines.append("不得出现：" + "、".join(args.forbidden_labels))
    if args.preserve_constraints:
        lines.append("保留上一版：" + "、".join(args.preserve_constraints))
    if args.allowed_changes:
        lines.append("只允许修改：" + "、".join(args.allowed_changes))
    if args.disallowed_changes:
        lines.append("禁止修改：" + "、".join(args.disallowed_changes))
    if args.reference_image:
        if getattr(args, "backend", None) == "api-image":
            # the image itself travels with the API request; do not leak local paths
            lines.append(f"参考图：见随附图片（角色：{args.reference_role or '未声明'}）")
        else:
            lines.append(f"参考图：{args.reference_image}（角色：{args.reference_role or '未声明'}）")
    if args.negative_prompt:
        lines.append("避免：" + args.negative_prompt.strip())
    lines.append(f"画幅：{args.aspect_ratio}，目标尺寸约 {width} x {height} 像素。")
    lines.append("白底或浅底、学术风格、字号可读、无水印、无伪英文、无额外步骤。")
    return "\n".join(lines).strip() + "\n"
