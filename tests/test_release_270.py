"""2.7.0：已有模型补极性、退出码、--version、vensim-script 输入清洗，以及 2.6.x 审计补丁的回归。"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/vensim-skill"
REFERENCE = SKILL / "assets/reference_models"
sys.path.insert(0, str(SKILL / "scripts"))

import skill_cli  # noqa: E402
from feedback_audit import audit_feedback, check_loops, infer_polarity  # noqa: E402
from mdl_document import MdlDocument, split_sketch_fields  # noqa: E402
from vensim_engine import command_check, parse_equations  # noqa: E402

# 符号随时间翻转：库存 → 流出 和 开关 → 流出 都推不出确定符号
FLIPPING = {
    "name": "flip",
    "time": {"initial": 0, "final": 8, "step": 0.25, "saveper": 0.25, "unit": "Month"},
    "variables": [
        {"name": "库存", "kind": "stock", "initial": "100", "unit": "件"},
        {
            "name": "流出",
            "kind": "flow",
            "from": "库存",
            "to": None,
            "equation": "库存 * 开关 / 10",
            "unit": "件/Month",
        },
        {
            "name": "开关",
            "kind": "aux",
            "equation": "IF THEN ELSE(MODULO(Time, 2) < 1, 1, -1)",
            "unit": "Dmnl",
        },
    ],
    "feedback_loops": [],
}


def run_cli(*args):
    return skill_cli.main([str(arg) for arg in args])


# ---------------------------------------------------------------------------
# P0：已有 MDL 补极性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["teacup", "overshoot"])
def test_apply_polarity_makes_reference_models_pass_check_strict(tmp_path, capsys, name):
    source = REFERENCE / f"{name}.mdl"
    assert command_check(source, strict=True) == 1
    capsys.readouterr()
    output = tmp_path / f"{name}_signed.mdl"
    assert run_cli("fix", source, "--apply-polarity", "--output", output) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["fixes"] and report["unsigned_remaining"] == []
    # 只改草图极性字段，方程区逐字节不变
    assert MdlDocument.read(output).equation_bytes == MdlDocument.read(source).equation_bytes
    assert command_check(output, strict=True) == 0


def test_apply_polarity_signs_follow_the_equations(tmp_path, capsys):
    output = tmp_path / "teacup_signed.mdl"
    assert run_cli("fix", REFERENCE / "teacup.mdl", "--apply-polarity", "--output", output) == 0
    capsys.readouterr()
    links = {
        (row["source"], row["target"]): row["displayed"]
        for row in audit_feedback(MdlDocument.read(output).semantic_text)["links"]
        if row["kind"] != "flow"
    }
    # Heat Loss to Room = (Teacup Temperature - Room Temperature) / Characteristic Time
    assert links[("Teacup Temperature", "Heat Loss to Room")] == "+"
    assert links[("Room Temperature", "Heat Loss to Room")] == "-"
    assert links[("Characteristic Time", "Heat Loss to Room")] == "-"


def test_feedback_suggests_signs_for_unsigned_arrows():
    report = audit_feedback(MdlDocument.read(REFERENCE / "teacup.mdl").semantic_text)
    unsigned = [row for row in report["links"] if not row["displayed"] and row["kind"] != "flow"]
    assert unsigned
    assert all(row["suggested"] in {"+", "-"} for row in unsigned)
    assert {row["suggested_source"] for row in unsigned} <= {"equation", "numeric"}


def test_apply_polarity_leaves_ambiguous_arrows_and_reports_them(tmp_path, capsys):
    from model_builder import _model_text

    spec = dict(FLIPPING, sketch={"polarity": "dynamic"})
    text, _ = _model_text(spec)
    source = tmp_path / "flip.mdl"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "flip_signed.mdl"
    assert run_cli("fix", source, "--apply-polarity", "--output", output) == 0
    report = json.loads(capsys.readouterr().out)
    remaining = {(row["source"], row["target"]) for row in report["unsigned_remaining"]}
    assert ("库存", "流出") in remaining
    assert command_check(output, strict=True) == 1


def test_fix_without_options_names_apply_polarity(tmp_path):
    with pytest.raises(ValueError, match="--apply-polarity"):
        from vensim_engine import command_fix

        command_fix(REFERENCE / "teacup.mdl", tmp_path / "x.mdl")


# ---------------------------------------------------------------------------
# 退出码
# ---------------------------------------------------------------------------


def test_build_and_cld_exit_nonzero_on_unsigned_arrows(tmp_path, capsys):
    spec_path = tmp_path / "flip.json"
    spec_path.write_text(json.dumps(FLIPPING, ensure_ascii=False), encoding="utf-8")
    assert run_cli("build", spec_path, "--output", tmp_path / "a.mdl") == 1
    assert "FAIL" in capsys.readouterr().out
    assert run_cli("build", spec_path, "--output", tmp_path / "b.mdl", "--allow-unsigned") == 0
    assert run_cli("cld", spec_path, "--output", tmp_path / "c.mdl", "--no-overall") == 1
    assert (
        run_cli(
            "cld", spec_path, "--output", tmp_path / "d.mdl", "--no-overall", "--allow-unsigned"
        )
        == 0
    )


# ---------------------------------------------------------------------------
# CLI 帮助与版本
# ---------------------------------------------------------------------------


def test_version_flag_matches_pyproject(capsys):
    assert run_cli("--version") == 0
    out = capsys.readouterr().out.strip()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert out == f"vensim-system-dynamics-skill {skill_cli.__version__}"
    assert f'version = "{skill_cli.__version__}"' in pyproject


def test_help_lists_auto_layout_and_apply_polarity(capsys):
    assert run_cli("--help") == 0
    out = capsys.readouterr().out
    assert "auto|natural|refine|preserve" in out
    assert "--apply-polarity" in out
    assert "VENSIM_SKILL_LAYOUT_SECONDS" in out
    assert "非商用" not in out


def test_subcommand_usage_names_the_command(capsys):
    for command in ("build", "cld", "feedback", "vensim-script"):
        assert run_cli(command, "--help") == 0
        assert f"usage: skill.sh {command}" in capsys.readouterr().out
    assert run_cli("calibrate", "--help") == 0
    calibrate = capsys.readouterr().out
    assert run_cli("optimize", "--help") == 0
    optimize = capsys.readouterr().out
    assert "usage: skill.sh calibrate" in calibrate and "usage: skill.sh optimize" in optimize
    assert calibrate != optimize


def test_layout_requirements_file_exists():
    text = (SKILL / "requirements/layout.txt").read_text(encoding="utf-8")
    assert "networkx" in text


# ---------------------------------------------------------------------------
# vensim-script 输入清洗
# ---------------------------------------------------------------------------


def _scenario_spec(name):
    return {
        "mode": "scenarios",
        "scenarios": [
            {"name": "基准", "params": {}},
            {"name": name, "params": {"Room Temperature": 60}},
        ],
    }


@pytest.mark.parametrize("bad", ["A\n:VAR x", "B\rMENU>EXIT", "C\x00", "D\x1b"])
def test_vensim_script_rejects_control_characters_in_scenario_names(tmp_path, bad):
    from vensim_dss import build_files

    with pytest.raises(ValueError, match="控制字符"):
        build_files(REFERENCE / "teacup.mdl", _scenario_spec(bad), ["Teacup Temperature"])


def test_vensim_script_rejects_control_characters_in_title_and_path(tmp_path):
    from vensim_dss import build_files

    with pytest.raises(ValueError, match="控制字符"):
        build_files(
            REFERENCE / "teacup.mdl", _scenario_spec("ok"), ["Teacup Temperature"], "T\n:VAR"
        )
    if sys.platform != "win32":
        folder = tmp_path / "evil\nSPECIAL>X"
        folder.mkdir()
        model = folder / "teacup.mdl"
        model.write_bytes((REFERENCE / "teacup.mdl").read_bytes())
        with pytest.raises(ValueError, match="控制字符"):
            build_files(model, _scenario_spec("ok"), ["Teacup Temperature"], base_dir=tmp_path)


def test_vensim_script_normal_names_still_work():
    from vensim_dss import build_files

    files = build_files(REFERENCE / "teacup.mdl", _scenario_spec("高温"), ["Teacup Temperature"])
    assert ":LABEL 高温" in files["graphs.vgd"]
    assert all("\x00" not in text for text in files.values())


# ---------------------------------------------------------------------------
# 发布物：包装层、CI、许可口径
# ---------------------------------------------------------------------------


def test_windows_wrapper_ignores_current_directory_executables():
    text = (SKILL / "skill.cmd").read_text(encoding="utf-8")
    assert text.index("NoDefaultCurrentDirectoryInExePath=1") < text.index("where %%c")


def test_ci_jobs_have_timeouts_and_do_not_persist_credentials():
    text = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    assert text.count("timeout-minutes:") == 2
    assert text.count("persist-credentials: false") == text.count("actions/checkout@") == 2


def test_license_wording_is_source_available():
    for path in (
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "README.en.md",
        ROOT / "pyproject.toml",
    ):
        text = path.read_text(encoding="utf-8")
        assert "NonCommercial" not in text and "non--commercial" not in text
        assert "非商用免费" not in text and "微信" not in text and "WeChat" not in text
    assert "LicenseRef-Vensim-Skill-SourceAvailable" in (ROOT / "LICENSE").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 2.6.x 审计补丁的回归（5b13d79 当时没有带测试）
# ---------------------------------------------------------------------------


def _write(tmp_path, body, name="m.mdl"):
    path = tmp_path / name
    path.write_text(
        "{UTF-8}\n"
        + body
        + "INITIAL TIME = 0\n\t~ Month\n\t~\n\t|\nFINAL TIME = 4\n\t~ Month\n\t~\n\t|\n"
        "TIME STEP = 1\n\t~ Month\n\t~\n\t|\nSAVEPER = TIME STEP\n\t~ Month\n\t~\n\t|\n",
        encoding="utf-8",
    )
    return path


def test_check_reports_undefined_name_in_untaken_branch(tmp_path, capsys):
    path = _write(tmp_path, "a = IF THEN ELSE(Time > 1, foo, 0)\n\t~ Dmnl\n\t~\n\t|\n")
    assert command_check(path) == 1
    assert "引用未定义变量 'foo'" in capsys.readouterr().out


def test_pysd_translation_drops_comments_and_refuses_triple_quotes():
    from simulation_runner import _strip_docs_for_pysd

    text = 'a = 1\n\t~ Dmnl\n\t~ __import__("os").system("id")\n\t|\n'
    cleaned = _strip_docs_for_pysd(text)
    assert "__import__" not in cleaned and "a = 1" in cleaned
    with pytest.raises(ValueError, match="三引号"):
        _strip_docs_for_pysd('a = 1\n\t~ """x\n\t~ c\n\t|\n')


def test_smoothi_output_is_not_treated_as_its_input():
    class Var:
        rhs = "x"

    class Const:
        def __init__(self, value):
            self.rhs = value

    equations = {"x": Var(), "c": Const("5"), "m": Const("-10")}
    # SMOOTHI 的输出不等于输入值，不能当常数 5 去乘 x
    assert infer_polarity("SMOOTHI(c, 3, 1) * x", "x", equations) == "unknown"
    assert infer_polarity("SMOOTHI(x, 3, 1)", "x", equations) == "+"


def test_cld_accepts_s_and_o_polarity_aliases():
    from cld_builder import cld_links

    spec = {
        "name": "so",
        "time": {"initial": 0, "final": 4, "step": 1, "saveper": 1, "unit": "Month"},
        "variables": [
            {"name": "压力", "kind": "aux", "equation": "1 + 人口 / 100", "unit": "Dmnl"},
            {
                "name": "开关",
                "kind": "aux",
                "equation": "IF THEN ELSE(MODULO(Time, 2) < 1, 1, -1)",
                "unit": "Dmnl",
            },
            {"name": "人口", "kind": "stock", "initial": "10", "unit": "人"},
            {
                "name": "出生率",
                "kind": "flow",
                "from": None,
                "to": "人口",
                "equation": "压力 * 开关",
                "unit": "人/Month",
            },
        ],
        "links": [{"from": "压力", "to": "出生率", "polarity": "S"}],
        "feedback_loops": [],
    }
    links, _, _, notes = cld_links(spec)
    signs = {(a, b): sign for a, b, sign, _ in links}
    assert signs[("压力", "出生率")] == "+"
    assert ["压力", "出生率"] not in notes["unsigned_links"]


def test_loop_through_delay_fixed_is_not_verified_without_link_sign(tmp_path):
    path = _write(
        tmp_path,
        "库存 = INTEG(入 - 出, 10)\n\t~ 件\n\t~\n\t|\n出 = 库存 / 2\n\t~ 件/Month\n\t~\n\t|\n"
        "入 = DELAY FIXED(出, 2, 0)\n\t~ 件/Month\n\t~\n\t|\n",
    )
    equations = parse_equations(path.read_text(encoding="utf-8"), expand=False)
    rows = check_loops(equations, [{"name": "L", "variables": ["库存", "出", "入"]}])
    # DELAY FIXED 的输出对输入正向，回路 库存 → 出 → 入 → 库存 三个 + 号，为 R
    assert rows[0]["link_signs"] == ["+", "+", "+"]
    assert rows[0]["expected"] == "R"


def test_split_sketch_fields_respects_quotes_and_is_linear():
    assert split_sketch_fields('10,1,"a, b",3') == ["10", "1", '"a, b"', "3"]
    body = ",".join(['"x"'] * 20000)
    assert len(split_sketch_fields(body)) == 20000


def test_layout_deadline_env_stops_annealing(monkeypatch):
    import natural_layout

    monkeypatch.setattr(natural_layout, "_LAYOUT_DEADLINE", [0.0])
    assert natural_layout._deadline_passed()
    monkeypatch.setattr(natural_layout, "_LAYOUT_DEADLINE", [None])
    assert not natural_layout._deadline_passed()
