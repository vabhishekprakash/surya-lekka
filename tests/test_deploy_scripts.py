import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_render_samples_writes_the_layout_the_sample_route_reads(tmp_path):
    pytest.importorskip("pymupdf")
    render = runpy.run_path(str(ROOT / "scripts" / "render_samples.py"))["render_samples"]
    assert render(tmp_path) == {"S1": 2, "S2": 2, "S3": 2}
    for sid in ("S1", "S2", "S3"):
        folder = tmp_path / sid
        pages = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["pages"]
        images = sorted(folder.glob("page-*.jpg"))
        assert [p.name for p in images] == [f"page-{n:02d}.jpg" for n in range(1, pages + 1)]
        assert all(p.read_bytes()[:3] == b"\xff\xd8\xff" and p.stat().st_size <= 3_750_000 for p in images)
        reading = json.loads((folder / "reading.json").read_text(encoding="utf-8"))
        assert reading["sample_id"] == sid and reading["reading"] == "saved"


def test_deploy_script_steps():
    script = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    order = [r"scripts\render_samples.py --out", "sam build --template-file", "sam deploy @deployArgs",
             "describe-stacks --stack-name", r"aws s3 cp .build\samples", 'Write-Host "API URL']
    positions = [script.index(step) for step in order]
    assert positions == sorted(positions)
    assert "--guided" in script and "Test-Path samconfig.toml" in script
