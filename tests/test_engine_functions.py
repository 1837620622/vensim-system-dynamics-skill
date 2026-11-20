"""2.5.0 新增函数：状态函数、平滑/延迟族、确定性数学函数和不支持函数的报错。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills" / "vensim-skill" / "scripts"
sys.path.insert(0, str(TOOLS))

from vensim_engine import get_time_bounds, parse_equations, simulate  # noqa: E402

CONTROLS = """
INITIAL TIME = 0
	~	~	|
FINAL TIME = 10
	~	~	|
TIME STEP = 0.5
	~	~	|
SAVEPER = 1
	~	~	|
"""

FUNCTIONS = (
    """{UTF-8}
x = Time*Time/5 + 1
	~	~	|
held = SAMPLE IF TRUE(MODULO(Time, 3) = 0, x * 10, -1)
	~	~	|
first = INITIAL(x + 5)
	~	~	|
sm = 2 * SMOOTH N(x, 4, 1, 3) + 1
	~	~	|
dn = DELAY N(x, 4, 1, 2)
	~	~	|
tr = TREND(x, 2, 0.1)
	~	~	|
fc = FORECAST(x, 2, 1)
	~	~	|
q = QUANTUM(x, 4) + POWER(2, 3) + LOG(100, 10)
	~	~	|
pt = PULSE TRAIN(1, 1, 4, 9)
	~	~	|
"""
    + CONTROLS
    + "\\\\\\---/// Sketch information - do not modify anything except names\n"
)

NAMES = ["held", "first", "sm", "dn", "tr", "fc", "q", "pt"]


def run(text):
    equations = parse_equations(text)
    return simulate(equations, *get_time_bounds(equations))


def test_sample_if_true_holds_value_between_samples():
    result = run(FUNCTIONS)
    held = result.series["held"]
    x = result.series["x"]
    for time, value in zip(result.times, held, strict=True):
        sample_time = 3 * int(time // 3)
        expected = 10 * (sample_time * sample_time / 5 + 1)
        assert value == pytest.approx(expected), time
    assert held[0] == pytest.approx(10 * x[0])


def test_initial_and_active_initial():
    text = (
        """
x = Time ~ ~ |
first = INITIAL(x + 5) ~ ~ |
ai = ACTIVE INITIAL(stock * 2, 7) ~ ~ |
stock = INTEG(1, ai) ~ ~ |
"""
        + CONTROLS
    )
    result = run(text)
    assert set(result.series["first"]) == {5.0}
    assert result.series["stock"][0] == 7.0
    assert result.series["ai"][:2] == [14.0, 16.0]


def test_nested_smooth_and_lookup_variants():
    text = (
        """
x = Time ~ ~ |
y = 1 + 2 * SMOOTH(x, 1) ~ ~ |
tbl([(0,0)-(10,10)],(0,0),(5,10)) ~ ~ |
le = LOOKUP EXTRAPOLATE(tbl, x) ~ ~ |
lf = LOOKUP FORWARD(tbl, 2) ~ ~ |
lb = LOOKUP BACKWARD(tbl, 2) ~ ~ |
"""
        + CONTROLS
    )
    result = run(text)
    assert result.series["y"][0] == 1.0
    assert result.series["le"][-1] == pytest.approx(20.0)
    assert result.series["lf"][0] == 10.0
    assert result.series["lb"][0] == 0.0


@pytest.mark.parametrize(
    "call", ["NPV(1, 0.1, 0, 1)", "RANDOM UNIFORM(0, 1, 1)", "GET XLS DATA('a', 'b', 'c', 'd')"]
)
def test_unsupported_function_message_names_function(call):
    text = f"y = {call} ~ ~ |\n" + CONTROLS
    with pytest.raises(ValueError) as error:
        run(text)
    name = call.split("(")[0]
    assert name in str(error.value)
    assert "crosscheck" in str(error.value)


def test_new_functions_match_pysd(tmp_path):
    pysd = pytest.importorskip("pysd")
    path = tmp_path / "functions.mdl"
    path.write_text(FUNCTIONS, encoding="utf-8")
    expected = pysd.read_vensim(str(path)).run(return_columns=NAMES)
    result = run(FUNCTIONS)
    for name in NAMES:
        for left, right in zip(expected[name], result.series[name], strict=True):
            assert right == pytest.approx(float(left), abs=1e-9), name
