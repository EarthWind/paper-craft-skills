"""Step 2 of the host-model workflow: register the image the host produced.

Copies the image (from Codex image generation, a host image tool, or a
code-render PNG) into the run directory as generated_image.<ext>, runs the
machine check, updates metadata.json and rebuilds figure_manifest.json.

If the host could not produce an image, call with --blocked-reason instead;
the run then records a blocker and never contains a fabricated image.
"""

import argparse
import shutil
from pathlib import Path

from build_figure_manifest import build_manifest
from check_generated_image import build_check, detect_image
from host_image_common import IMAGE_NAMES, file_digest, now_iso, read_json, write_json

SUFFIX_BY_TYPE = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}


def update_metadata(run_dir, status, backend=None, tool=None, image_path=None, check=None, blocker=None, render_source=None):
    metadata = read_json(run_dir / "metadata.json") or {}
    if "_read_error" in metadata:
        metadata = {}
    metadata.update(
        {
            "schema": "sci_run_metadata_v2",
            "run_id": run_dir.name,
            "updated_at": now_iso(),
            "status": status,
            "provider": "host-agent",
            "iterations_completed": 1 if status == "completed" else 0,
            "machine_check": check,
            "blocker": blocker,
            "error": blocker.get("reason") if blocker else None,
            "errors": [blocker] if blocker else [],
            "last_image_sha256": file_digest(image_path) if image_path else None,
            "output_paths": {
                "run_dir": str(run_dir),
                "image_prompt": str(run_dir / "image_prompt.txt") if (run_dir / "image_prompt.txt").exists() else None,
                "render_source": str(render_source) if render_source else None,
                "image": str(image_path) if image_path else None,
                "check": str(run_dir / "check.json") if (run_dir / "check.json").exists() else None,
                "blocker": str(run_dir / "blocker.json") if (run_dir / "blocker.json").exists() else None,
                "figure_manifest": str(run_dir / "figure_manifest.json"),
            },
        }
    )
    if backend:
        metadata["backend"] = backend
    if tool:
        metadata["generation_tool"] = tool
    write_json(run_dir / "metadata.json", metadata)
    return metadata


def block(run_dir, reason, details=None, backend=None, tool=None, check=None):
    blocker = {"blocked": True, "reason": reason, "details": details or {}}
    write_json(run_dir / "blocker.json", blocker)
    update_metadata(run_dir, "blocked", backend=backend, tool=tool, check=check, blocker=blocker)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir))
    print("BLOCKED: " + reason)
    print("Run directory: " + str(run_dir))
    return 2


def register(run_dir, image, backend=None, tool=None, render_source=None):
    run_dir = Path(run_dir).resolve()
    if not (run_dir / "run_input.json").exists():
        raise SystemExit(f"not a prepared run directory (missing run_input.json): {run_dir}")

    copied_source = None
    if render_source:
        source = Path(render_source)
        if source.exists():
            copied_source = run_dir / ("render_source" + source.suffix.lower())
            if source.resolve() != copied_source.resolve():
                shutil.copyfile(source, copied_source)

    image = Path(image) if image else None
    if not image or not image.exists():
        return block(run_dir, "host did not provide an image file", {"image": str(image) if image else None}, backend, tool)

    data = image.read_bytes()
    image_type = detect_image(data)[0]
    suffix = SUFFIX_BY_TYPE.get(image_type)
    if not suffix:
        check = build_check(image)
        write_json(run_dir / "check.json", check)
        return block(run_dir, "image type is not PNG, JPEG, or WebP", {"image": str(image)}, backend, tool, check)

    for name in IMAGE_NAMES:
        stale = run_dir / name
        if stale.exists() and stale.resolve() != image.resolve():
            stale.unlink()
    target = run_dir / ("generated_image" + suffix)
    if image.resolve() != target.resolve():
        target.write_bytes(data)
        if image.parent.resolve() == run_dir and image.name in IMAGE_NAMES:
            image.unlink()  # mislabeled extension inside the run dir
    stale_blocker = run_dir / "blocker.json"
    if stale_blocker.exists():
        stale_blocker.unlink()

    check = build_check(target)
    check["image_saved"] = check["image_exists"]
    check["source_image"] = str(image)
    write_json(run_dir / "check.json", check)
    if not check["non_empty"]:
        return block(run_dir, "image file is empty", {"image": str(image)}, backend, tool, check)

    update_metadata(run_dir, "completed", backend, tool, target, check, None, copied_source)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir))
    print(f"OK: registered {target.name} ({check.get('width')} x {check.get('height')}, {check.get('mime_type')})")
    print("Machine check: " + str((check.get("machine_check") or {}).get("status")))
    print("Run directory: " + str(run_dir))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Register a host-produced image into a prepared run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--image", help="Image file produced by the host (PNG/JPEG/WebP).")
    parser.add_argument("--backend", choices=["host-image-tool", "code-render"], help="Backend actually used.")
    parser.add_argument("--tool", help="Free text, e.g. 'codex image generation' or 'svg + rsvg-convert'.")
    parser.add_argument("--render-source", help="Figure source file for code-render runs (svg/html/py/dot/mmd); copied into the run.")
    parser.add_argument("--blocked-reason", help="Record a blocker instead of an image (e.g. host has no image tool).")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if args.blocked_reason:
        return block(run_dir, args.blocked_reason, backend=args.backend, tool=args.tool)
    return register(run_dir, args.image, args.backend, args.tool, args.render_source)


if __name__ == "__main__":
    raise SystemExit(main())
