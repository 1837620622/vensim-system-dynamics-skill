from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "skills" / "vensim-skill" / "vensim_system_dynamics" / "tools"
sys.path.insert(0, str(TOOLS))

from vensim_engine import get_time_bounds, parse_equations, simulate  # noqa: E402


def run_model(text: str):
    equations = parse_equations(text)
    return equations, simulate(equations, *get_time_bounds(equations))


def control_block(final_time=2, time_step=1):
    return f"""
INITIAL TIME = 0
    ~ Month
    |
FINAL TIME = {final_time}
    ~ Month
    |
TIME STEP = {time_step}
    ~ Month
    |
SAVEPER = TIME STEP
    ~ Month
    |
"""


def test_integ_accepts_initial_value_reference():
    text = """
Stock = INTEG( In - Out, Initial Stock )
    ~ Unit
    |
Initial Stock = 10
    ~ Unit
    |
In = 1
    ~ Unit/Month
    |
Out = 0
    ~ Unit/Month
    |
""" + control_block()

    _, result = run_model(text)

    assert result.series["Stock"] == [10.0, 11.0, 12.0]


def test_nested_if_then_else_arguments_are_parsed():
    text = """
X = IF THEN ELSE( A > 0, MAX(1, 2), MIN(3, 4) )
    ~ Dmnl
    |
A = 1
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["X"] == [2.0, 2.0]


def test_delay_fixed_uses_prior_input_history():
    text = """
Input = STEP(10, 2)
    ~ Unit/Month
    |
Delayed = DELAY FIXED(Input, 3, 0)
    ~ Unit/Month
    |
""" + control_block(final_time=5)

    _, result = run_model(text)

    assert result.series["Delayed"] == [0.0, 0.0, 0.0, 0.0, 0.0, 10.0]


def test_with_lookup_accepts_expression_argument():
    text = """
Y = WITH LOOKUP( Price / Base Price, ( [0,0)-(2,10)], (0,0), (1,5), (2,10) )
    ~ Dmnl
    |
Price = 10
    ~ Dmnl
    |
Base Price = 10
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["Y"] == [5.0, 5.0]


def test_with_lookup_does_not_swallow_surrounding_expression():
    text = """
Y = WITH LOOKUP( Price, ( [0,0)-(2,10)], (0,0), (1,5), (2,10) ) + 1
    ~ Dmnl
    |
Price = 1
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["Y"] == [6.0, 6.0]


def test_stock_initializers_follow_stock_dependency_order():
    text = """
B = INTEG( 0, A + 2 )
    ~ Unit
    |
A = INTEG( 0, 10 )
    ~ Unit
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["A"] == [10.0, 10.0]
    assert result.series["B"] == [12.0, 12.0]


def test_guard_and_integer_functions_are_allowed():
    text = """
X = INTEGER( XIDZ(10, 0, 3) + ZIDZ(4, 2, 0) )
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["X"] == [5.0, 5.0]


def test_helper_function_names_do_not_collide_with_variables():
    text = """
X = MAX(1, 2) + INTEGER( float )
    ~ Dmnl
    |
float = 3.8
    ~ Dmnl
    |
max = 7
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["X"] == [5.0, 5.0]


def test_reserved_helper_names_do_not_collide_with_variables():
    text = """
X = MAX(1, 2) + _sd_max
    ~ Dmnl
    |
_sd_max = 4
    ~ Dmnl
    |
""" + control_block(final_time=1)

    _, result = run_model(text)

    assert result.series["X"] == [6.0, 6.0]


def test_malformed_lookup_fails_in_strict_mode():
    text = """
Y = []
    ~ Dmnl
    |
""" + control_block(final_time=1)

    equations = parse_equations(text)

    try:
        simulate(equations, *get_time_bounds(equations))
    except ValueError as exc:
        assert "LOOKUP" in str(exc)
    else:
        raise AssertionError("malformed lookup should fail in strict mode")


def test_zero_time_step_fails_before_simulation_loop():
    text = """
X = 1
    ~ Dmnl
    |
""" + control_block(final_time=1, time_step=0)

    equations = parse_equations(text)

    try:
        simulate(equations, *get_time_bounds(equations))
    except ValueError as exc:
        assert "TIME STEP" in str(exc)
    else:
        raise AssertionError("zero time step should fail")


def test_large_power_expression_fails_before_resource_exhaustion():
    text = """
X = 2 ^ 1001
    ~ Dmnl
    |
""" + control_block(final_time=1)

    equations = parse_equations(text)

    try:
        simulate(equations, *get_time_bounds(equations))
    except ValueError as exc:
        assert "指数过大" in str(exc)
    else:
        raise AssertionError("oversized power should fail")


def test_ramp_accepts_vensim_three_argument_form():
    text = """
X = RAMP(2, 1, 3)
    ~ Dmnl
    |
""" + control_block(final_time=4)

    _, result = run_model(text)

    assert result.series["X"] == [0.0, 0.0, 2.0, 4.0, 4.0]


def test_unsupported_function_fails_instead_of_silent_nodata():
    text = """
X = ACTIVE INITIAL( 2 * Time, 10 )
    ~ Dmnl
    |
""" + control_block(final_time=1)

    equations = parse_equations(text)

    try:
        simulate(equations, *get_time_bounds(equations))
    except ValueError as exc:
        assert "ACTIVE INITIAL" in str(exc)
    else:
        raise AssertionError("unsupported function should fail in strict mode")
