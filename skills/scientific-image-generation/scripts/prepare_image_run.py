"""Step 1 of the host-model workflow: create an auditable run directory.

Writes figure_spec.json, run_input.json, image_prompt.txt, metadata.json
(status=awaiting_image) and figure_manifest.json. It sends nothing over the
network; the host agent (Codex image generation or Claude code-render)
produces the image afterwards and registers it with register_generated_image.py.
"""

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from build_figure_manifest import build_manifest
from host_image_common import (
    BACKEND_NOTES,
    add_figure_arguments,
    build_figure_spec,
    build_host_prompt,
    file_digest,
    infer_mime,
    merge_input_json,
    now_iso,
    target_pixels,
    text_digest,
    write_json,
)


def make_run_dir(output_dir, run_kind):
    base = Path(output_dir) if output_dir else Path.cwd() / "scientific_image_skill_runs"
    prefix = "edit" if run_kind == "edit" else "gen"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base / f"{prefix}_{stamp}"
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = base / f"{prefix}_{stamp}_{suffix}"
    run_dir.mkdir(parents=True)
    return run_dir


def build_continue_from(args):
    reference_path = Path(args.reference_image) if args.reference_image else None
    source_run = Path(args.source_run) if args.source_run else None
    if not (source_run or reference_path or args.feedback):
        return None
    return {
        "source_run_id": source_run.name if source_run else None,
        "source_run_dir": str(source_run) if source_run else None,
        "source_run_exists": source_run.exists() if source_run else None,
        "parent_run_input_path": str(source_run / "run_input.json") if source_run else None,
        "parent_metadata_path": str(source_run / "metadata.json") if source_run else None,
        "parent_manifest_path": str(source_run / "figure_manifest.json") if source_run else None,
        "source_image_path": str(reference_path) if reference_path else None,
        "source_image_exists": reference_path.exists() if reference_path else None,
        "source_image_sha256": file_digest(reference_path) if reference_path else None,
        "feedback": args.feedback,
        "reference_role": args.reference_role,
        "preserve_constraints": args.preserve_constraints,
        "allowed_changes": args.allowed_changes,
        "disallowed_changes": args.disallowed_changes,
        "new_prompt_delta": args.prompt.strip(),
    }


def build_run_input(run_dir, args, figure_spec, host_prompt):
    reference_path = Path(args.reference_image) if args.reference_image else None
    width, height = target_pixels(args.aspect_ratio, args.image_size)
    return {
        "schema": "sci_run_input_v2",
        "created_at": now_iso(),
        "run_id": run_dir.name,
        "run_kind": args.run_kind,
        "provider": "host-agent",
        "backend": args.backend,
        "backend_note": BACKEND_NOTES[args.backend],
        "source_context": args.source_context,
        "caption": args.caption,
        "communicative_intent": args.intent or args.caption,
        "figure_kind": args.figure_kind,
        "required_labels": args.required_labels,
        "forbidden_labels": args.forbidden_labels,
        "layout_brief": args.layout_brief,
        "style_brief": args.style_brief,
        "reference_role": args.reference_role,
        "figure_spec": figure_spec,
        "prompt": args.prompt.strip(),
        "prompt_hash": text_digest(args.prompt.strip()),
        "host_prompt": host_prompt,
        "host_prompt_path": str(run_dir / "image_prompt.txt"),
        "negative_prompt": args.negative_prompt.strip(),
        "aspect_ratio": args.aspect_ratio,
        "image_size": args.image_size,
        "target_width": width,
        "target_height": height,
        "source_run": args.source_run,
        "feedback": args.feedback,
        "continue_from": build_continue_from(args),
        "reference_image": str(reference_path) if reference_path else None,
        "reference_image_exists": reference_path.exists() if reference_path else None,
        "reference_image_mime_type": infer_mime(reference_path) if reference_path else None,
        "reference_image_sha256": file_digest(reference_path) if reference_path else None,
        "iterations_requested": 1,
    }


def write_metadata(run_dir, run_input, status, blocker=None):
    write_json(
        run_dir / "metadata.json",
        {
            "schema": "sci_run_metadata_v2",
            "created_at": run_input["created_at"],
            "updated_at": now_iso(),
            "run_id": run_dir.name,
            "status": status,
            "provider": "host-agent",
            "backend": run_input["backend"],
            "run_kind": run_input["run_kind"],
            "prompt_hash": run_input["prompt_hash"],
            "source_run": run_input["source_run"],
            "iterations_requested": 1,
            "iterations_completed": 0,
            "blocker": blocker,
            "error": blocker.get("reason") if blocker else None,
            "errors": [blocker] if blocker else [],
        },
    )


def prepare_run(args):
    run_dir = make_run_dir(args.output_dir, args.run_kind)
    if args.source_run:
        (run_dir / "source_run.txt").write_text(str(Path(args.source_run)), encoding="utf-8")
    figure_spec = build_figure_spec(args)
    write_json(run_dir / "figure_spec.json", figure_spec)
    host_prompt = build_host_prompt(args)
    (run_dir / "image_prompt.txt").write_text(host_prompt, encoding="utf-8")
    run_input = build_run_input(run_dir, args, figure_spec, host_prompt)
    write_json(run_dir / "run_input.json", run_input)

    blocker = None
    if args.reference_image:
        reference_path = Path(args.reference_image)
        if reference_path.exists():
            shutil.copyfile(reference_path, run_dir / ("reference_image" + reference_path.suffix.lower()))
        else:
            blocker = {
                "blocked": True,
                "reason": "reference image path does not exist",
                "details": {"reference_image": str(reference_path)},
            }
            write_json(run_dir / "blocker.json", blocker)

    write_metadata(run_dir, run_input, "blocked" if blocker else "awaiting_image", blocker)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir, source_run=args.source_run))
    return run_dir, run_input, blocker


def main():
    parser = argparse.ArgumentParser(description="Prepare a host-model (Claude/Codex) scientific image run directory.")
    add_figure_arguments(parser)
    parser.add_argument("--output-dir", help="Directory that receives timestamped run folders (default ./scientific_image_skill_runs).")
    parser.add_argument("--run-kind", choices=["generate", "edit"], default="generate")
    args = parser.parse_args()
    merge_input_json(args)
    if not args.prompt:
        parser.error("--prompt is required unless --input-json provides it")
    if args.reference_image and args.run_kind != "edit":
        args.run_kind = "edit"

    run_dir, run_input, blocker = prepare_run(args)
    if blocker:
        print("BLOCKED: " + blocker["reason"])
        print("Run directory: " + str(run_dir))
        return 2
    print("PREPARED: run directory is waiting for an image")
    print("Backend: " + args.backend + " — " + BACKEND_NOTES[args.backend])
    print(f"Target size: {run_input['target_width']} x {run_input['target_height']}")
    print("Image prompt: " + str(run_dir / "image_prompt.txt"))
    print("Run directory: " + str(run_dir))
    print("Next: produce the image, then run register_generated_image.py --run-dir <run> --image <file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
