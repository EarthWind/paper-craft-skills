"""Step 3 of the host-model workflow: semantic review by the host model itself.

Claude and Codex can both look at an image, so the Critic review no longer
needs an external VLM key.

  prompt  print the Critic instructions + the image path the host must open
  record  validate the host's JSON verdict and write semantic_review.json

The host must actually open the image (Read tool in Claude Code, image view
in Codex) before writing the verdict; never review from the prompt alone.
"""

import argparse
import json
import sys
from pathlib import Path

from host_image_common import find_image, now_iso, read_json, write_json
from semantic_review_dashscope import build_review_prompt, extract_json_text

AXES = ["content_accuracy", "layout_quality", "text_readability", "aesthetic_quality", "artifact_severity"]
ACTIONS = {"deliver", "edit", "regenerate", "manual_review"}
AXIS_STATUS = {"PASS", "FAIL", "UNCERTAIN"}


def load_context(run_dir):
    return (
        read_json(run_dir / "figure_spec.json"),
        read_json(run_dir / "check.json"),
        read_json(run_dir / "figure_manifest.json"),
        read_json(run_dir / "run_input.json"),
    )


def validate(review):
    errors = []
    if not isinstance(review.get("overall_ready_to_ship"), bool):
        errors.append("overall_ready_to_ship must be true or false")
    action = review.get("recommended_next_action")
    if action not in ACTIONS:
        errors.append(f"recommended_next_action must be one of {sorted(ACTIONS)}")
    axes = review.get("quality_review_axes")
    if not isinstance(axes, list):
        errors.append("quality_review_axes must be a list")
    else:
        names = {axis.get("name") for axis in axes if isinstance(axis, dict)}
        missing = [name for name in AXES if name not in names]
        if missing:
            errors.append("quality_review_axes missing: " + ", ".join(missing))
        for axis in axes:
            if isinstance(axis, dict) and str(axis.get("status", "")).upper() not in AXIS_STATUS:
                errors.append(f"axis {axis.get('name')} status must be PASS/FAIL/UNCERTAIN")
    for key in ["observed_labels", "missing_required_labels", "forbidden_labels_seen", "issues"]:
        if key in review and not isinstance(review[key], list):
            errors.append(f"{key} must be a list")
    for key in ["critic_suggestions", "revised_description"]:
        if not isinstance(review.get(key), str) or not review.get(key).strip():
            errors.append(f"{key} must be a non-empty string (use 'No changes needed.' when nothing changes)")
    return errors


def cmd_prompt(args):
    run_dir = Path(args.run_dir).resolve()
    image = find_image(run_dir)
    if not image:
        print("BLOCKED: no generated_image.* in run directory; register an image first")
        return 2
    figure_spec, check, manifest, run_input = load_context(run_dir)
    print("IMAGE TO OPEN AND INSPECT: " + str(image))
    print()
    print(build_review_prompt(figure_spec, check, manifest, run_input))
    print()
    print(f"Save the JSON verdict and run: host_semantic_review.py record --run-dir {run_dir} --review-json <file> --reviewer <model>")
    return 0


def cmd_record(args):
    run_dir = Path(args.run_dir).resolve()
    image = find_image(run_dir)
    out = run_dir / "semantic_review.json"
    raw = sys.stdin.read() if args.review_json == "-" else Path(args.review_json).read_text(encoding="utf-8")
    base = {
        "schema": "giiisp_semantic_review_v1",
        "created_at": now_iso(),
        "provider": "host-agent",
        "model": args.reviewer,
        "image_path": str(image) if image else None,
        "image_inspected_by_host": True,
    }
    if not image:
        review = dict(base, status="blocked", overall_ready_to_ship=None, blocker={"reason": "image file is missing"})
        write_json(out, review)
        print("BLOCKED: image file is missing")
        return 2
    try:
        parsed = extract_json_text(raw)
    except Exception as exc:
        print("INVALID: review is not parseable JSON: " + str(exc))
        return 2
    if not isinstance(parsed, dict):
        print("INVALID: review JSON must be an object")
        return 2
    errors = validate(parsed)
    if errors:
        print("INVALID: fix the review JSON and record again:")
        for line in errors:
            print("  - " + line)
        return 2
    review = dict(base)
    review.update(parsed)
    review["schema"] = "giiisp_semantic_review_v1"
    review["provider"] = "host-agent"
    review["status"] = "completed"
    write_json(out, review)
    print(
        "OK: semantic review recorded; ready_to_ship="
        + str(review["overall_ready_to_ship"])
        + ", next_action="
        + str(review["recommended_next_action"])
    )
    print("Run directory: " + str(run_dir))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Host-model (Claude/Codex) semantic review for a generated figure.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prompt = sub.add_parser("prompt", help="Print the Critic prompt and image path.")
    p_prompt.add_argument("--run-dir", required=True)
    p_record = sub.add_parser("record", help="Validate and save the host's JSON verdict.")
    p_record.add_argument("--run-dir", required=True)
    p_record.add_argument("--review-json", required=True, help="JSON file path, or - for stdin.")
    p_record.add_argument("--reviewer", default="host-model", help="Reviewer model name, e.g. claude-opus-5-5 or the Codex model.")
    args = parser.parse_args()
    return cmd_prompt(args) if args.command == "prompt" else cmd_record(args)


if __name__ == "__main__":
    raise SystemExit(main())
