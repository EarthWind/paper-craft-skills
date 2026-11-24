"""Host-model (Claude Code / Codex) scientific figure workflow driver.

The image is produced by the host agent, so this driver is step-based instead
of one blocking call. Typical order:

  prepare_image_run.py          -> run dir + image_prompt.txt
  (host) produce the image      -> Codex image generation, or code-render
  register_generated_image.py   -> generated_image.* + check.json
  host_semantic_review.py       -> semantic_review.json (host looks at the image)
  run_scientific_image_workflow.py repair   -> at most one auto-repair edit run
  run_scientific_image_workflow.py finalize -> manual_review.md, manifest, workflow_status.json

`status` prints which step a run directory is waiting for.
"""

import argparse
import json
from argparse import Namespace
from pathlib import Path

from build_figure_manifest import build_manifest
from host_image_common import DEFAULT_NEGATIVE_PROMPT, find_image, now_iso, read_json, write_json
from prepare_image_run import prepare_run

MAX_AUTO_REPAIR_ROUNDS = 1


def semantic_status(review):
    if not isinstance(review, dict):
        return "missing"
    return review.get("status") or "unknown"


def list_axis_failures(review):
    failures = []
    axes = review.get("quality_review_axes") if isinstance(review, dict) else None
    if isinstance(axes, list):
        for axis in axes:
            if not isinstance(axis, dict):
                continue
            if str(axis.get("status", "")).upper() in {"FAIL", "UNCERTAIN"}:
                failures.append(
                    {
                        "name": axis.get("name"),
                        "status": axis.get("status"),
                        "score": axis.get("score"),
                        "rationale": axis.get("rationale"),
                    }
                )
    return failures


def list_values(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def meaningful_text(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text.lower() == "no changes needed." or text.lower() == "no changes needed":
        return ""
    return text


def should_auto_repair(review):
    if not isinstance(review, dict) or review.get("status") != "completed":
        return False, "semantic review not completed"
    action = str(review.get("recommended_next_action") or "").lower()
    if action in {"edit", "regenerate"}:
        return True, f"recommended_next_action={action}"
    if review.get("overall_ready_to_ship") is False:
        return True, "overall_ready_to_ship=false"
    if list_values(review.get("missing_required_labels")):
        return True, "missing_required_labels"
    if list_values(review.get("forbidden_labels_seen")):
        return True, "forbidden_labels_seen"
    axis_failures = list_axis_failures(review)
    hard_failures = [
        item
        for item in axis_failures
        if str(item.get("name") or "") in {"content_accuracy", "text_readability", "artifact_severity"}
        and str(item.get("status") or "").upper() == "FAIL"
    ]
    if hard_failures:
        return True, "hard quality axis failure"
    return False, "review passed or only needs human judgment"


def build_auto_repair_prompt(run_dir, review):
    run_input = read_json(run_dir / "run_input.json") or {}
    figure_spec = read_json(run_dir / "figure_spec.json") or {}
    original_prompt = run_input.get("prompt") or ""
    caption = figure_spec.get("caption") or run_input.get("caption") or ""
    figure_kind = figure_spec.get("figure_kind") or run_input.get("figure_kind") or ""
    required_labels = list_values(figure_spec.get("required_labels") or run_input.get("required_labels"))
    forbidden_labels = list_values(figure_spec.get("forbidden_labels") or run_input.get("forbidden_labels"))
    missing_labels = list_values(review.get("missing_required_labels"))
    forbidden_seen = list_values(review.get("forbidden_labels_seen"))
    issues = list_values(review.get("issues"))
    axis_failures = list_axis_failures(review)
    critic_suggestions = meaningful_text(review.get("critic_suggestions"))
    revised_description = meaningful_text(review.get("revised_description"))
    next_prompt = meaningful_text(review.get("next_edit_prompt"))

    source_description = revised_description or original_prompt.strip()
    parts = ["Render an image based on the following detailed description:", source_description, ""]
    parts.extend(
        [
            "Note that do not include figure titles in the image. Diagram:",
            "请基于上一轮视觉审查结果重新生成一版科研图，只修正基础问题，不扩展新的科学内容。",
            "保持原始科学含义、图题、画幅和主体结构；不要引入未在需求中出现的新步骤、新变量或随机英文。",
        ]
    )
    if caption:
        parts.append(f"图题/主题：{caption}")
    if figure_kind:
        parts.append(f"图类型：{figure_kind}")
    if required_labels:
        parts.append("必须清晰出现这些标签，逐字保留：" + "、".join(required_labels))
    if forbidden_labels or forbidden_seen:
        combined_forbidden = forbidden_labels + [item for item in forbidden_seen if item not in forbidden_labels]
        parts.append("必须避免这些文字或元素：" + "、".join(combined_forbidden))
    if missing_labels:
        parts.append("上一版缺失的必须标签，需要补上：" + "、".join(missing_labels))
    if issues:
        parts.append("上一版需要修正的问题：" + "；".join(issues))
    if axis_failures:
        concise = []
        for item in axis_failures:
            name = item.get("name")
            status = item.get("status")
            rationale = item.get("rationale")
            concise.append(f"{name}/{status}: {rationale}")
        parts.append("质量轴问题：" + "；".join(concise))
    if critic_suggestions:
        parts.append("Critic suggestions：" + critic_suggestions)
    if next_prompt:
        parts.append("审查给出的修订指令：" + next_prompt)
    parts.extend(
        [
            "输出要求：白底或浅底、学术汇报风格、结构清晰、字号可读、无水印、无广告感、无伪英文、无错乱标签。",
            "这是一次自动基础修订，不要进行多轮发散优化。",
        ]
    )
    return "\n".join(part for part in parts if part is not None).strip()



def write_agent_review_summary(run_dir, overwrite=False):
    report_path = run_dir / "manual_review.md"
    if report_path.exists() and not overwrite:
        return {"written": False, "path": str(report_path), "reason": "manual_review.md 已存在"}

    figure_spec = read_json(run_dir / "figure_spec.json") or {}
    check = read_json(run_dir / "check.json") or {}
    review = read_json(run_dir / "semantic_review.json") or {}
    image = find_image(run_dir)
    review_state = semantic_status(review)
    missing_labels = review.get("missing_required_labels") if isinstance(review, dict) else None
    forbidden_seen = review.get("forbidden_labels_seen") if isinstance(review, dict) else None
    issues = review.get("issues") if isinstance(review, dict) else None
    axis_failures = list_axis_failures(review if isinstance(review, dict) else {})
    ready = review.get("overall_ready_to_ship") if isinstance(review, dict) else None
    next_action = review.get("recommended_next_action") if isinstance(review, dict) else None
    next_prompt = review.get("next_edit_prompt") if isinstance(review, dict) else None
    critic_suggestions = review.get("critic_suggestions") if isinstance(review, dict) else None
    revised_description = review.get("revised_description") if isinstance(review, dict) else None

    if review_state != "completed":
        ready_text = "否：宿主模型语义审查未完成，正式交付前仍需要人工看图确认。"
        next_action = next_action or "manual_review"
        next_prompt = next_prompt or "补充可用的 宿主模型审查，或人工检查图片里的标签、结构和错字后再交付。"
    elif ready is True:
        ready_text = "是：宿主模型审查认为这张图可交付。"
    elif ready is False:
        ready_text = "否：宿主模型审查发现正式交付前应继续修改的问题。"
    else:
        ready_text = "不确定：宿主模型审查没有给出明确交付判断。"

    lines = [
        "# 代理复查摘要",
        "",
        "本摘要基于机器检查和宿主模型（Claude/Codex）看图语义审查生成，用于交付前复查记录；它不替代最终人工看图签核。",
        "",
        "## 运行信息",
        f"- 运行目录：{run_dir}",
        f"- 图片：{image if image else '缺失'}",
        f"- 图类型：{figure_spec.get('figure_kind')}",
        f"- 图题：{figure_spec.get('caption')}",
        f"- 必须标签：{figure_spec.get('required_labels')}",
        f"- 禁止项：{figure_spec.get('forbidden_labels')}",
        "",
        "## 机器检查",
        f"- 图片存在：{check.get('image_exists')}",
        f"- 文件非空：{check.get('non_empty')}",
        f"- 类型：{check.get('image_type')} / {check.get('mime_type')}",
        f"- 尺寸：{check.get('width')} x {check.get('height')}",
        f"- 阻断：{check.get('has_blocker')}",
        "",
        "## 语义审查",
        f"- 状态：{review_state}",
        f"- 是否可交付：{ready}",
        f"- 缺失必须标签：{missing_labels or []}",
        f"- 出现禁止项：{forbidden_seen or []}",
        f"- 问题：{issues or []}",
        f"- 失败或不确定质量轴：{axis_failures or []}",
        f"- Critic 建议：{critic_suggestions}",
        f"- Revised description：{revised_description}",
        "",
        "## 复查结论",
        f"- 交付判断：{ready_text}",
        f"- 建议动作：{next_action}",
        f"- 下一轮修改 prompt：{next_prompt}",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"written": True, "path": str(report_path)}


def next_step(run_dir):
    metadata = read_json(run_dir / "metadata.json") or {}
    if (run_dir / "blocker.json").exists():
        return "blocked", "查看 blocker.json；不要伪造图片"
    if not find_image(run_dir):
        run_input = read_json(run_dir / "run_input.json") or {}
        if run_input.get("backend") == "api-image":
            return "awaiting_image", f"运行 generate_image_api.py --run-dir（provider: {run_input.get('image_provider') or 'gemini'}）"
        return "awaiting_image", "按 image_prompt.txt 出图，然后运行 register_generated_image.py"
    review = read_json(run_dir / "semantic_review.json")
    if semantic_status(review) != "completed":
        return "awaiting_review", "打开图片看图，运行 host_semantic_review.py prompt / record"
    if not (run_dir / "workflow_status.json").exists():
        needed, reason = should_auto_repair(review)
        if needed and repair_round(run_dir) < MAX_AUTO_REPAIR_ROUNDS and not (run_dir / "auto_repair_prompt.json").exists():
            return "repair_suggested", f"审查要求修订（{reason}）：运行 repair，或用 --no-auto-repair 直接 finalize"
        return "ready_to_finalize", "运行 finalize 写复查摘要和 manifest"
    return metadata.get("status") or "finalized", "已完成；可交付或按用户反馈开新一轮 edit"


def repair_round(run_dir):
    run_input = read_json(run_dir / "run_input.json") or {}
    return int(run_input.get("auto_repair_round") or 0)


def cmd_status(args):
    run_dir = Path(args.run_dir).resolve()
    state, hint = next_step(run_dir)
    print(json.dumps({"run_dir": str(run_dir), "state": state, "next": hint}, ensure_ascii=False, indent=2))
    return 0


def cmd_repair(args):
    run_dir = Path(args.run_dir).resolve()
    review = read_json(run_dir / "semantic_review.json")
    needed, reason = should_auto_repair(review)
    if not needed:
        print("NO_REPAIR: " + reason)
        return 0
    if repair_round(run_dir) >= MAX_AUTO_REPAIR_ROUNDS:
        print("NO_REPAIR: 已达到自动修订上限（1 轮）；交给用户判断")
        return 0

    run_input = read_json(run_dir / "run_input.json") or {}
    repair_prompt = build_auto_repair_prompt(run_dir, review)
    write_json(
        run_dir / "auto_repair_prompt.json",
        {
            "schema": "sci_auto_repair_prompt_v1",
            "created_at": now_iso(),
            "source_run_dir": str(run_dir),
            "reason": reason,
            "strategy": "PaperBanana-style Critic revised_description -> Visualizer prompt -> host regeneration",
            "critic_suggestions": review.get("critic_suggestions"),
            "revised_description": review.get("revised_description"),
            "next_edit_prompt": review.get("next_edit_prompt"),
            "visualizer_prompt": repair_prompt,
            "max_auto_repair_rounds": MAX_AUTO_REPAIR_ROUNDS,
        },
    )
    spec = run_input.get("figure_spec") or {}
    metadata = read_json(run_dir / "metadata.json") or {}
    backend = args.backend or metadata.get("backend") or run_input.get("backend") or "auto"
    # Nano Banana edits images well: for a local fix, send the previous image as the reference
    reference_image, reference_role = None, ""
    parent_image = find_image(run_dir)
    if backend == "api-image" and str(review.get("recommended_next_action")).lower() == "edit" and parent_image:
        reference_image, reference_role = str(parent_image), "edit_image"
    edit_args = Namespace(
        prompt=repair_prompt,
        negative_prompt=run_input.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
        aspect_ratio=run_input.get("aspect_ratio") or "1:1",
        image_size=run_input.get("image_size") or "1K",
        backend=backend,
        image_provider=run_input.get("image_provider") or "gemini",
        reference_image=reference_image,
        source_run=str(run_dir),
        source_context=run_input.get("source_context"),
        caption=run_input.get("caption"),
        intent=run_input.get("communicative_intent"),
        figure_kind=run_input.get("figure_kind") or "workflow",
        required_labels=run_input.get("required_labels") or [],
        forbidden_labels=run_input.get("forbidden_labels") or [],
        style_brief=run_input.get("style_brief"),
        feedback="宿主模型看图审查发现基础问题，自动生成一版修订图。",
        reference_role=reference_role,
        preserve_constraints=spec.get("preserve_constraints") or [],
        allowed_changes=spec.get("allowed_changes") or [],
        disallowed_changes=spec.get("disallowed_changes") or [],
        layout_brief=run_input.get("layout_brief"),
        output_dir=str(Path(args.output_dir) if args.output_dir else run_dir.parent),
        run_kind="edit",
    )
    new_dir, new_input, _ = prepare_run(edit_args)
    new_input["auto_repair_round"] = repair_round(run_dir) + 1
    write_json(new_dir / "run_input.json", new_input)
    print("REPAIR_PREPARED: " + reason)
    print("Image prompt: " + str(new_dir / "image_prompt.txt"))
    if reference_image:
        print("Reference image (edit_image): " + reference_image)
    print("Run directory: " + str(new_dir))
    if backend == "api-image":
        print("Next: generate_image_api.py --run-dir <new run>, review it, then finalize with --initial-run-dir " + str(run_dir))
        return 0
    print("Next: produce the revised image, register it, review it, then finalize with --initial-run-dir " + str(run_dir))
    return 0


def build_status(run_dir, initial_run_dir, auto_repair):
    image = find_image(run_dir)
    review = read_json(run_dir / "semantic_review.json")
    state, _ = next_step(run_dir)
    if (run_dir / "blocker.json").exists():
        status = "generation_blocked"
    elif semantic_status(review) != "completed":
        status = "completed_without_semantic_review"
    else:
        status = "completed"
    return {
        "schema": "sci_image_workflow_v3",
        "created_at": now_iso(),
        "provider": "host-agent",
        "run_dir": str(run_dir),
        "initial_run_dir": str(initial_run_dir),
        "final_run_dir": str(run_dir),
        "status": status,
        "state_before_finalize": state,
        "auto_repair": auto_repair,
        "artifacts": {
            "image": str(image) if image else None,
            "image_prompt": str(run_dir / "image_prompt.txt") if (run_dir / "image_prompt.txt").exists() else None,
            "check": str(run_dir / "check.json") if (run_dir / "check.json").exists() else None,
            "semantic_review": str(run_dir / "semantic_review.json") if review else None,
            "manual_review": str(run_dir / "manual_review.md") if (run_dir / "manual_review.md").exists() else None,
            "manifest": str(run_dir / "figure_manifest.json") if (run_dir / "figure_manifest.json").exists() else None,
        },
    }


def cmd_finalize(args):
    run_dir = Path(args.run_dir).resolve()
    initial = Path(args.initial_run_dir).resolve() if args.initial_run_dir else run_dir
    auto_repair = {"attempted": initial != run_dir, "max_rounds": MAX_AUTO_REPAIR_ROUNDS}
    if initial != run_dir:
        auto_repair.update({"source_run_dir": str(initial), "repair_run_dir": str(run_dir)})
        write_agent_review_summary(initial, overwrite=args.overwrite_agent_summary)
        write_json(initial / "figure_manifest.json", build_manifest(initial))
    summary = {"written": False}
    if not args.no_agent_summary:
        summary = write_agent_review_summary(run_dir, overwrite=args.overwrite_agent_summary)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir))
    status = build_status(run_dir, initial, auto_repair)
    write_json(run_dir / "workflow_status.json", status)
    review = read_json(run_dir / "semantic_review.json") or {}
    print("FINALIZED: " + status["status"])
    print("Agent summary: " + ("written" if summary.get("written") else summary.get("reason", "skipped")))
    print("Ready to ship: " + str(review.get("overall_ready_to_ship")))
    print("Next edit prompt: " + str(review.get("next_edit_prompt")))
    print("Run directory: " + str(run_dir))
    return 0 if status["status"] != "generation_blocked" else 2


def main():
    parser = argparse.ArgumentParser(description="Claude/Codex 宿主模型科研绘图流程：状态、一次自动修订、收尾。")
    sub = parser.add_subparsers(dest="command", required=True)
    p_status = sub.add_parser("status", help="Show which step a run directory is waiting for.")
    p_status.add_argument("--run-dir", required=True)
    p_repair = sub.add_parser("repair", help="Prepare at most one auto-repair edit run from the semantic review.")
    p_repair.add_argument("--run-dir", required=True)
    p_repair.add_argument("--backend", choices=["auto", "host-image-tool", "code-render", "api-image"])
    p_repair.add_argument("--output-dir")
    p_final = sub.add_parser("finalize", help="Write manual_review.md, rebuild manifest, write workflow_status.json.")
    p_final.add_argument("--run-dir", required=True, help="Final run (the repair run if one was made).")
    p_final.add_argument("--initial-run-dir", help="First run, when an auto-repair run followed it.")
    p_final.add_argument("--no-agent-summary", action="store_true")
    p_final.add_argument("--overwrite-agent-summary", action="store_true")
    args = parser.parse_args()
    return {"status": cmd_status, "repair": cmd_repair, "finalize": cmd_finalize}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
