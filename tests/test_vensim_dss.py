"""vensim-script：Vensim DSS 原生结果图的 .cin/.vgd/.cmd。"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "vensim-skill"
sys.path.insert(0, str(SKILL / "scripts"))

from vensim_dss import command_vensim_script  # noqa: E402

MODEL = SKILL / "assets" / "examples" / "inventory_zh.mdl"
SPEC = SKILL / "assets" / "templates" / "inventory_scenarios.json"


def test_writes_change_files_graphs_and_command_script(tmp_path):
    out = tmp_path / "dss"
    assert command_vensim_script(MODEL, SPEC, [], out) == 0
    cmd = (out / "run_all.cmd").read_text(encoding="utf-8").splitlines()
    assert cmd[0].startswith("SPECIAL>LOADMODEL|")
    assert cmd.count("MENU>RUN|O") == 3  # 空参数情景即基准，不重复跑
    assert "SIMULATE>READCIN|scenario_02.cin" in cmd
    assert cmd[-1] == "MENU>VGD|graphs.vgd"
    assert (out / "scenario_02.cin").read_text(encoding="utf-8").strip() == "调整时间 = 2"
    vgd = (out / "graphs.vgd").read_text(encoding="utf-8")
    assert vgd.count(":GRAPH") == 2 and vgd.count(":DATASET") == 6
    assert (out / "run_all.cmd").read_bytes().count(b"\r\n") == len(cmd)


def test_rejects_unknown_parameter_and_existing_output(tmp_path):
    spec = tmp_path / "bad.json"
    spec.write_text(
        json.dumps(
            {
                "mode": "scenarios",
                "variables": ["库存"],
                "scenarios": [{"name": "a", "params": {"不存在": 1}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="数值常量"):
        command_vensim_script(MODEL, spec, [], tmp_path / "x")
    out = tmp_path / "dss"
    command_vensim_script(MODEL, SPEC, [], out)
    with pytest.raises(ValueError, match="已存在"):
        command_vensim_script(MODEL, SPEC, [], out)


def test_falls_back_to_absolute_model_path_across_drives(tmp_path, monkeypatch):
    import vensim_dss

    def _cross_drive(*_args, **_kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(vensim_dss.os.path, "relpath", _cross_drive)
    out = tmp_path / "dss"
    assert command_vensim_script(MODEL, SPEC, [], out) == 0
    first = (out / "run_all.cmd").read_text(encoding="utf-8").splitlines()[0]
    assert first == f"SPECIAL>LOADMODEL|{Path(MODEL).resolve()}"
