import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills/vensim-skill/scripts"
sys.path.insert(0, str(TOOLS))

from mdl_document import atomic_write, preflight_outputs  # noqa: E402
from model_builder import command_build  # noqa: E402
from simulation_runner import run_model  # noqa: E402
from vensim_engine import command_simulate  # noqa: E402
from vensim_autolayout import command_layout  # noqa: E402
from result_plotting import export_figures  # noqa: E402


def test_atomic_write_preserves_existing_and_dangling_symlink(tmp_path):
    output = tmp_path / "result.csv"
    atomic_write(output, b"original")
    with pytest.raises(FileExistsError):
        atomic_write(output, b"replacement")
    assert output.read_bytes() == b"original"
    assert not list(tmp_path.glob(".vensim-*"))
    link = tmp_path / "dangling.csv"
    try:
        link.symlink_to(tmp_path / "missing.csv")
    except OSError:
        pytest.skip("系统未允许创建符号链接")
    with pytest.raises(FileExistsError):
        atomic_write(link, b"unsafe")
    assert link.is_symlink()
    assert not (tmp_path / "missing.csv").exists()


def test_preflight_catches_colliding_outputs_and_input_alias(tmp_path):
    source = tmp_path / "source.mdl"
    source.write_bytes(b"keep")
    with pytest.raises(ValueError, match="覆盖输入"):
        preflight_outputs([source], [source])
    with pytest.raises(ValueError, match="覆盖输入"):
        preflight_outputs([tmp_path / "x.csv", tmp_path / "x.csv"])


def test_builder_preflights_sidecar_before_writing_model(tmp_path):
    spec = ROOT / "skills/vensim-skill/assets/templates/inventory_zh.json"
    report = tmp_path / "test.mdl.build_report.json"
    report.write_text(json.dumps({"user_data": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        command_build(spec, tmp_path / "test.mdl")
    assert not (tmp_path / "test.mdl").exists()
    assert json.loads(report.read_text())["user_data"]


@pytest.mark.parametrize("action,suffix", [("simulate", ".csv.run.json"), ("layout", ".mdl.layout_report.json"), ("plot", ".plot.json")])
def test_other_commands_preserve_sidecars_without_partial_primary(tmp_path, action, suffix):
    source = ROOT / "skills/vensim-skill/assets/examples/inventory_zh.mdl"
    report = tmp_path / ("protected" + suffix)
    report.write_bytes(b"user supplied sidecar")
    output = tmp_path / ("protected" + {"simulate": ".csv", "layout": ".mdl", "plot": ".png"}[action])
    with pytest.raises(ValueError, match="已存在"):
        if action == "simulate":
            command_simulate(source, output, ["库存"])
        elif action == "layout":
            command_layout(source, output)
        else:
            export_figures([("baseline", run_model(source, ["库存"]))], ["库存"], output)
    assert report.read_bytes() == b"user supplied sidecar"
    assert not output.exists()


def test_internal_aliases_cannot_corrupt_real_variable_names(tmp_path):
    model = tmp_path / "aliases.mdl"
    model.write_text("中文=2~Dmnl~|\n_v0=3~Dmnl~|\nmath=4~Dmnl~|\n_sd_exp=5~Dmnl~|\n"
                     "结果=中文+_v0+math+_sd_exp+EXP(0)~Dmnl~|\n"
                     "INITIAL TIME=0~Hour~|\nFINAL TIME=1~Hour~|\nTIME STEP=1~Hour~|\nSAVEPER=1~Hour~|\n", encoding="utf-8")
    result = run_model(model, ["结果"])
    assert result.series["结果"] == [15, 15]
    with pytest.raises(ValueError, match="不重复"):
        run_model(model, ["结果", "结果"])
    with pytest.raises(ValueError, match="有限"):
        run_model(model, ["结果"], params={"中文": True})
