import json
import subprocess
import sys
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_cover.py"


def run(*args):
    done = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")
    return done.returncode, json.loads(done.stdout) if done.stdout.strip().startswith("{") else done


def draw(path, box, size=(1536, 1024)):
    im = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(im).rectangle(box, fill=(31, 78, 158))
    im.save(path)


def test_crops_both_sizes_around_offcentre_content(tmp_path):
    src = tmp_path / "gen.png"
    draw(src, (900, 300, 1300, 600))  # subject sits right of centre
    code, report = run(src, tmp_path / "out")
    assert code == 0 and report["warnings"] == []
    assert report["content_bbox"] == [900, 300, 1301, 601]
    banner, square = (Image.open(o["path"]) for o in report["outputs"])
    assert banner.size == (900, 383) and square.size == (500, 500)
    assert all(o["content_fits"] for o in report["outputs"])
    # the square crop is centred on the subject, not on the image
    left, top, right, bottom = report["outputs"][1]["crop_box"]
    assert abs((left + right) / 2 - 1100.5) <= 1 and abs((top + bottom) / 2 - 450.5) <= 1


def test_warns_when_subject_cannot_fit_square(tmp_path):
    src = tmp_path / "wide.png"
    draw(src, (20, 480, 1516, 540))  # very wide, thin subject
    code, report = run(src, tmp_path / "out")
    assert code == 1
    assert any("1:1" not in w and "-1x1" in w for w in report["warnings"])


def test_blank_image_fails(tmp_path):
    src = tmp_path / "blank.png"
    Image.new("RGB", (300, 200), (255, 255, 255)).save(src)
    code, _ = run(src, tmp_path / "out")
    assert code != 0
