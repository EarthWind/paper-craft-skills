import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(script, *args, check=True):
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed


def run_dir_from(stdout):
    for line in stdout.splitlines():
        if line.startswith("Run directory:"):
            return Path(line.split(":", 1)[1].strip())
    raise AssertionError("no run directory in output:\n" + stdout)


def minimal_png(width=1024, height=576):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"


def review_json(ready, action, missing=None):
    return {
        "overall_ready_to_ship": ready,
        "quality_review_axes": [
            {"name": name, "status": "PASS" if ready else "FAIL", "score": 8 if ready else 3, "rationale": "ok"}
            for name in ["content_accuracy", "layout_quality", "text_readability", "aesthetic_quality", "artifact_severity"]
        ],
        "observed_labels": ["输入", "推理"],
        "missing_required_labels": missing or [],
        "forbidden_labels_seen": [],
        "issues": [] if ready else ["缺少验证步骤"],
        "critic_suggestions": "No changes needed." if ready else "补上验证卡片。",
        "revised_description": "No changes needed." if ready else "横向三步流程：输入、推理、验证。",
        "recommended_next_action": action,
        "next_edit_prompt": "" if ready else "补上验证卡片",
    }


def prepare(tmp_path):
    out = run(
        "prepare_image_run.py",
        "--prompt", "画一个横向三步科研流程图：输入、推理、验证",
        "--required-labels", "输入", "推理", "验证",
        "--aspect-ratio", "16:9",
        "--backend", "code-render",
        "--output-dir", tmp_path / "runs",
    )
    return run_dir_from(out.stdout)


def test_prepare_needs_no_token_and_waits_for_image(tmp_path):
    run_dir = prepare(tmp_path)
    run_input = json.loads((run_dir / "run_input.json").read_text(encoding="utf-8"))
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "figure_manifest.json").read_text(encoding="utf-8"))
    prompt = (run_dir / "image_prompt.txt").read_text(encoding="utf-8")

    assert run_input["provider"] == "host-agent"
    assert run_input["backend"] == "code-render"
    assert (run_input["target_width"], run_input["target_height"]) == (1024, 576)
    assert "输入、推理、验证" in prompt
    assert metadata["status"] == "awaiting_image"
    assert manifest["artifacts"]["image_prompt"] == str(run_dir / "image_prompt.txt")
    assert not [key for key in run_input if "token" in key]
    status = json.loads(run("run_scientific_image_workflow.py", "status", "--run-dir", run_dir).stdout)
    assert status["state"] == "awaiting_image"


def test_register_review_repair_finalize(tmp_path):
    run_dir = prepare(tmp_path)
    image = tmp_path / "host_output.png"
    image.write_bytes(minimal_png())
    source = tmp_path / "figure.svg"
    source.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

    run("register_generated_image.py", "--run-dir", run_dir, "--image", image, "--backend", "code-render", "--tool", "svg+test", "--render-source", source)
    check = json.loads((run_dir / "check.json").read_text(encoding="utf-8"))
    assert check["width"] == 1024 and check["height"] == 576
    assert (run_dir / "generated_image.png").exists()
    assert (run_dir / "render_source.svg").exists()

    review = tmp_path / "review.json"
    review.write_text(json.dumps(review_json(False, "edit", ["验证"]), ensure_ascii=False), encoding="utf-8")
    run("host_semantic_review.py", "record", "--run-dir", run_dir, "--review-json", review, "--reviewer", "test-model")
    saved = json.loads((run_dir / "semantic_review.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "host-agent" and saved["status"] == "completed"

    repair = run("run_scientific_image_workflow.py", "repair", "--run-dir", run_dir)
    repair_dir = run_dir_from(repair.stdout)
    assert repair_dir != run_dir
    assert (run_dir / "auto_repair_prompt.json").exists()
    repair_input = json.loads((repair_dir / "run_input.json").read_text(encoding="utf-8"))
    assert repair_input["auto_repair_round"] == 1
    assert repair_input["source_run"] == str(run_dir)
    assert "验证" in (repair_dir / "image_prompt.txt").read_text(encoding="utf-8")

    run("register_generated_image.py", "--run-dir", repair_dir, "--image", image)
    review.write_text(json.dumps(review_json(False, "edit", ["验证"]), ensure_ascii=False), encoding="utf-8")
    run("host_semantic_review.py", "record", "--run-dir", repair_dir, "--review-json", review)
    second = run("run_scientific_image_workflow.py", "repair", "--run-dir", repair_dir)
    assert "NO_REPAIR" in second.stdout  # one auto-repair round only

    run("run_scientific_image_workflow.py", "finalize", "--run-dir", repair_dir, "--initial-run-dir", run_dir)
    status = json.loads((repair_dir / "workflow_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["auto_repair"]["attempted"] is True
    assert (repair_dir / "manual_review.md").exists()
    assert (run_dir / "manual_review.md").exists()


def test_invalid_review_is_rejected(tmp_path):
    run_dir = prepare(tmp_path)
    image = tmp_path / "host_output.png"
    image.write_bytes(minimal_png())
    run("register_generated_image.py", "--run-dir", run_dir, "--image", image)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"overall_ready_to_ship": "yes"}), encoding="utf-8")
    completed = run("host_semantic_review.py", "record", "--run-dir", run_dir, "--review-json", review, check=False)
    assert completed.returncode == 2
    assert "INVALID" in completed.stdout
    assert not (run_dir / "semantic_review.json").exists()


def test_blocked_reason_never_fakes_image(tmp_path):
    run_dir = prepare(tmp_path)
    completed = run("register_generated_image.py", "--run-dir", run_dir, "--blocked-reason", "host has no image tool and no renderer", check=False)
    assert completed.returncode == 2
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "blocked"
    assert not list(run_dir.glob("generated_image.*"))


def test_render_svg_source(tmp_path):
    source = tmp_path / "figure.svg"
    source.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='180'><rect x='10' y='10' width='100' height='60' fill='#0F766E'/></svg>",
        encoding="utf-8",
    )
    out = tmp_path / "figure.png"
    completed = run("render_figure_source.py", "--source", source, "--out", out, "--width", 320, "--height", 180, check=False)
    if completed.returncode == 2:
        assert "BLOCKED" in completed.stdout  # no converter on this machine
    else:
        assert out.read_bytes().startswith(b"\x89PNG")
