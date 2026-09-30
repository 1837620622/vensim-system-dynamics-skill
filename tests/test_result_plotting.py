from pathlib import Path
from types import SimpleNamespace
import json
import math
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "skills/vensim-skill/scripts"
sys.path.insert(0, str(TOOLS))

from experiments import experiment_runs  # noqa: E402
from result_plotting import (  # noqa: E402
    band_figure, classic_figure, export_figures, font_settings, load_plot_config, read_result_csv, validate_results,
)


def trajectories():
    return [(f"policy {i}", SimpleNamespace(times=[0, 1, 2, 3], series={"Stock": [10, 12 + i, 15 + i, 18 + i]},
            metadata={"time_unit": "Month"})) for i in range(5)]


def test_perturb_uses_original_constant_and_one_parameter_at_a_time():
    runs = experiment_runs({"mode": "perturb", "parameters": ["Rate", "Cost"], "changes": [-0.2, -0.1, 0.2, 0.1]}, {"Rate": 10, "Cost": 20})
    assert len(runs) == 9
    assert [run["params"]["Rate"] for run in runs[:4]] == [8, 9, 12, 11]
    assert all(len(run["params"]) == 1 for run in runs[:-1])
    assert runs[-1] == {"name": "current", "params": {}}
    with pytest.raises(ValueError, match="零基准"):
        experiment_runs({"mode": "perturb", "parameters": ["Rate"], "changes": [0.3]}, {"Rate": 0})
    with pytest.raises(ValueError, match="数值常量"):
        experiment_runs({"mode": "perturb", "parameters": ["Feedback"], "changes": [0.3]}, {"Rate": 10})


def test_csv_long_and_wide_preserve_exact_data(tmp_path):
    wide = tmp_path / "wide.csv"
    wide.write_text("Time,Stock\n0,100\n1,101.5\n", encoding="utf-8-sig")
    result, variables = read_result_csv(wide, time_unit="Year")
    assert variables == ["Stock"]
    assert result[0][1].series["Stock"] == [100, 101.5]
    long = tmp_path / "long.csv"
    long.write_text("Scenario,Time,Variable,Value\nbase,0,库存,1\nbase,1,库存,2\n", encoding="gb18030")
    result, variables = read_result_csv(long, encoding="gb18030")
    assert variables == ["库存"]
    assert result[0][1].times == [0, 1]
    assert result[0][1].series["库存"] == [1, 2]
    assert result[0][1].metadata["source_csv_sha256"]


@pytest.mark.parametrize("csv", ["Time,Stock\n0,1\n0,2\n", "Time,Stock\n0,1\n1,nan\n",
                                      "Time,Stock,Stock\n0,1,2\n", "Time,Stock\n0,1,2\n"])
def test_invalid_csv_is_not_silently_repaired(tmp_path, csv):
    source = tmp_path / "bad.csv"
    source.write_text(csv)
    with pytest.raises(ValueError):
        read_result_csv(source)


def test_diagnostic_results_cannot_be_published_as_figures():
    results = trajectories()
    results[0][1].metadata["status"] = "diagnostic_only"
    with pytest.raises(ValueError, match="诊断"):
        validate_results(results, ["Stock"])


def test_style_configuration_is_overridable_and_checked(tmp_path):
    path = tmp_path / "plot.json"
    path.write_text(json.dumps({"width_inches": 9, "number_lines": False, "colors": ["#000000"]}))
    config = load_plot_config(path)
    assert config["width_inches"] == 9
    assert not config["number_lines"]
    path.write_text('{"width_inches": -2}')
    with pytest.raises(ValueError, match="width_inches"):
        load_plot_config(path)
    path.write_text('{"unknown_option": true}')
    with pytest.raises(ValueError, match="未知"):
        load_plot_config(path)


def test_font_checks_actual_glyphs_instead_of_only_font_name():
    pytest.importorskip("matplotlib")
    assert font_settings("Stock Time 0123456789")["font.family"]
    with pytest.raises(RuntimeError, match="字形"):
        font_settings("unassigned \U0010ffff")
    with pytest.raises(ValueError, match="未安装"):
        font_settings("Stock", {"font_family": ["Missing Font For Vensim Regression"]})


def test_classic_curve_data_legends_and_no_title():
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    results = trajectories()
    fig = classic_figure(results, "Stock", "Time (Month)")
    axis, legend = fig.axes
    assert axis.get_title() == ""
    assert fig._suptitle is None
    assert len(axis.lines) == 5
    for i, line in enumerate(axis.lines):
        assert list(line.get_ydata()) == results[i][1].series["Stock"]
        assert line.get_marker() == f"${i + 1}$"
    assert [line.get_color() for line in legend.lines] == ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"]
    assert len(legend.texts) == 30
    assert all(spine.get_visible() for spine in axis.spines.values())
    plt.close(fig)


def test_export_dpi_vector_and_provenance(tmp_path):
    pytest.importorskip("matplotlib")
    from PIL import Image
    output = tmp_path / "results.png"
    report = export_figures(trajectories(), ["Stock"], output, formats="pdf,svg")
    assert len(report["figures"]) == 3
    with Image.open(output) as image:
        assert image.size == (4320, 3390)
        assert image.info["dpi"][0] == pytest.approx(600, abs=0.02)
    assert output.with_suffix(".pdf").read_bytes().startswith(b"%PDF")
    assert "<svg" in output.with_suffix(".svg").read_text()
    manifest = json.loads(output.with_suffix(".plot.json").read_text())
    assert manifest["title"] == ""
    assert manifest["time_unit"] == "Month"
    assert len(manifest["runs"]) == 5
    assert not manifest["native_verified"]


def test_multi_variable_outputs_cannot_overwrite_any_input(tmp_path):
    pytest.importorskip("matplotlib")
    source = tmp_path / "out_02.svg"
    source.write_text("original")
    results = trajectories()
    for _, result in results:
        result.series["Flow"] = result.series["Stock"]
    with pytest.raises(ValueError):
        export_figures(results, ["Stock", "Flow"], tmp_path / "out.png", formats="svg", inputs=[source])
    assert source.read_text() == "original"
    assert not (tmp_path / "out_01.png").exists()


def test_quantile_band_uses_sample_quantiles_and_rejects_different_time_grid():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    results = trajectories()
    fig = band_figure(results, "Stock", "Time")
    assert list(fig.axes[0].lines[0].get_ydata()) == [10, 14, 17, 20]
    assert len(fig.axes[0].collections) == 2
    plt.close(fig)
    results[1][1].times = [0, 1, 2, 4]
    with pytest.raises(ValueError, match="时间网格"):
        band_figure(results, "Stock", "Time")
    results[1][1].times = [0, 1, 2, math.inf]
    with pytest.raises(ValueError, match="Time"):
        validate_results(results, ["Stock"])


def test_color_styles_match_legend_and_do_not_change_data():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    config = {**load_plot_config(), "line_styles": ["-", "--", "-.", ":"], "show_grid": False}
    results = trajectories()
    fig = classic_figure(results, "Stock", "Time", config=config)
    axis, legend = fig.axes
    assert [line.get_linestyle() for line in axis.lines] == [line.get_linestyle() for line in legend.lines]
    assert len({line.get_linestyle() for line in axis.lines}) == 4
    assert not any(line.get_visible() for line in [*axis.get_xgridlines(), *axis.get_ygridlines()])
    assert len({line.get_color() for line in axis.lines}) == len(results)
    assert axis.get_title() == "" and fig._suptitle is None
    assert list(axis.lines[2].get_ydata()) == results[2][1].series["Stock"]
    plt.close(fig)


@pytest.mark.parametrize("value", [{"show_grid": "false"}, {"line_styles": []}, {"line_styles": ["invalid"]},
                                   {"band_outer_alpha": True}, {"band_inner_alpha": 1.1}])
def test_invalid_line_styles_are_rejected(tmp_path, value):
    path = tmp_path / "plot.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_plot_config(path)


def test_named_scenarios_are_not_silently_replaced_with_quantiles(tmp_path):
    pytest.importorskip("matplotlib")
    results = [(str(i), trajectories()[0][1]) for i in range(13)]
    with pytest.raises(ValueError, match="政策情景"):
        export_figures(results, ["Stock"], tmp_path / "unsafe.svg")
    assert not (tmp_path / "unsafe.svg").exists()
    assert not (tmp_path / "unsafe.plot.json").exists()
    for _, result in results:
        result.metadata["experiment_mode"] = "monte-carlo"
    export_figures(results, ["Stock"], tmp_path / "samples.svg")
    report = json.loads((tmp_path / "samples.plot.json").read_text())
    assert report["style"] == "band" and not report["sample_bands_are_confidence_intervals"]


def test_single_curve_and_quantile_bands_are_colored_by_default():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgb
    for fig in (classic_figure(trajectories()[:1], "Stock", "Time"), band_figure(trajectories(), "Stock", "Time")):
        axis = fig.axes[0]
        assert len(set(to_rgb(axis.lines[0].get_color()))) > 1
        for band in axis.collections:
            assert len(set(band.get_facecolor()[0][:3])) > 1
        plt.close(fig)


def test_coincident_curves_keep_data_but_do_not_share_number_positions():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    results = [(str(i), SimpleNamespace(times=list(range(60)), series={"Stock": [1]*60})) for i in range(6)]
    fig = classic_figure(results, "Stock", "Time")
    indices = [index for line in fig.axes[0].lines for index in line.get_markevery()]
    assert len(indices) == len(set(indices)) and indices
    assert all(list(line.get_ydata()) == [1]*60 for line in fig.axes[0].lines)
    plt.close(fig)
