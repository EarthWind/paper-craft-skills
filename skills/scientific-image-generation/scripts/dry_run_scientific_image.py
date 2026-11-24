"""Preview what the host model will be asked to draw, without creating a run."""

import argparse
import json
from pathlib import Path

from host_image_common import (
    BACKEND_NOTES,
    add_figure_arguments,
    build_figure_spec,
    build_host_prompt,
    file_digest,
    infer_mime,
    merge_input_json,
    target_pixels,
)


def main():
    parser = argparse.ArgumentParser(description="Dry-run: build the host-model image prompt and figure spec only.")
    add_figure_arguments(parser)
    args = parser.parse_args()
    merge_input_json(args)
    if not args.prompt:
        parser.error("--prompt is required unless --input-json provides it")

    width, height = target_pixels(args.aspect_ratio, args.image_size)
    output = {
        "provider": "host-agent",
        "backend": args.backend,
        "backend_note": BACKEND_NOTES[args.backend],
        "target_size": {"aspect_ratio": args.aspect_ratio, "width": width, "height": height},
        "prompt": args.prompt.strip(),
        "host_prompt": build_host_prompt(args),
        "safety": "dry-run only; no run directory was created and no image was produced",
        "run_input_preview": {
            "source_context": args.source_context,
            "caption": args.caption,
            "communicative_intent": args.intent or args.caption,
            "figure_kind": args.figure_kind,
            "required_labels": args.required_labels,
            "forbidden_labels": args.forbidden_labels,
            "style_brief": args.style_brief,
            "feedback": args.feedback,
        },
        "figure_spec_preview": build_figure_spec(args),
    }
    edit_metadata = {}
    if args.source_run:
        source_run = Path(args.source_run)
        edit_metadata["source_run"] = str(source_run)
        edit_metadata["source_run_exists"] = source_run.exists()
    if args.reference_image:
        reference_path = Path(args.reference_image)
        edit_metadata["reference_image_path"] = str(reference_path)
        edit_metadata["reference_image_exists"] = reference_path.exists()
        if reference_path.exists():
            edit_metadata["reference_image_mime_type"] = infer_mime(reference_path)
            edit_metadata["reference_image_size_bytes"] = reference_path.stat().st_size
            edit_metadata["reference_image_sha256"] = file_digest(reference_path)
    if edit_metadata:
        output["edit_metadata"] = edit_metadata
        output["run_directory_rule"] = (
            "save edits in a new scientific_image_skill_runs/<session_slug>/edit_YYYYMMDD_HHMMSS "
            "directory and write source_run.txt; never overwrite the source run"
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
