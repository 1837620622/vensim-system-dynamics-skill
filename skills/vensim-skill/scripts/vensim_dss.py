#!/usr/bin/env python3
"""生成在 Vensim DSS 中出原生结果图的文件。

    vensim-script model.mdl --spec experiment.json --var 变量 [--var ...] --output-dir dss

写出（文件名只用 ASCII，避免命令脚本里的路径编码问题）：

- ``scenario_01.cin`` …：每个情景一份参数变更文件，一行一个 ``常量 = 数值``；
- ``graphs.vgd``：Vensim 自定义图（Custom Graph），每个变量一张，叠加基准和全部情景；
- ``run_all.cmd``：Vensim 命令脚本，载入模型、跑基准 ``base`` 和各情景、载入自定义图；
- ``README.txt``：在 Vensim DSS 中的使用步骤。

情景沿用 ``experiment`` 的 JSON（``mode`` 为 ``scenarios``/``perturb``/``grid``，
蒙特卡洛请在 Vensim 里用 Sensitivity 工具）。只写参数变更，不改模型文件。
命令脚本只能在 Vensim DSS / Pro 中执行（PLE 不支持命令脚本）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from mdl_document import MdlDocument, atomic_write, preflight_outputs
from vensim_engine import canonical_name, parse_equations

MAX_SCENARIOS = 40


def _number(value: float) -> str:
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


def _quote(name: str) -> str:
    """含逗号、括号等字符的变量名在 Vensim 里要加双引号。"""
    return f'"{name}"' if re.search(r"[^\w\s$]", name) else name


def build_files(
    model: Path, spec: dict, variables: list[str], title_prefix: str = "", base_dir=None
):
    from experiments import experiment_runs

    document = MdlDocument.read(model)
    equations = parse_equations(document.semantic_text, expand=False)
    canonical = {canonical_name(name): name for name in equations}
    baseline = {}
    for name, equation in equations.items():
        try:
            baseline[name] = float(equation.rhs)
        except ValueError:
            continue
    if spec.get("mode") == "monte-carlo":
        raise ValueError("蒙特卡洛请在 Vensim 里用 Sensitivity 工具；这里只生成确定性情景")
    runs = experiment_runs(spec, baseline)
    if len(runs) > MAX_SCENARIOS:
        raise ValueError(f"情景最多 {MAX_SCENARIOS} 个，Vensim 自定义图叠加太多曲线无法辨认")
    for variable in variables:
        if canonical_name(variable) not in canonical:
            raise ValueError(f"模型中没有变量: {variable}")
    files = {}
    names = []
    for index, run in enumerate(runs, 1):
        if run["name"].strip().lower() == "base":
            raise ValueError("情景名 base 留给基准运行，请换一个名字")
        lines = []
        for parameter, value in run["params"].items():
            key = canonical_name(parameter)
            if key not in canonical or canonical[key] not in baseline:
                raise ValueError(f"{parameter}: 只能修改模型中的数值常量")
            lines.append(f"{_quote(canonical[key])} = {_number(value)}")
        cin = f"scenario_{index:02d}.cin"
        if lines:
            files[cin] = "\n".join(lines) + "\n"
        names.append((run["name"], cin))
    # 没有参数变更的情景就是基准，不再单独跑 base
    has_base = any(not run["params"] for run in runs)
    labels = ([] if has_base else ["基准"]) + [name for name, _ in names]
    run_names = ([] if has_base else ["base"]) + [
        _run_name(name, i) for i, (name, _) in enumerate(names, 1)
    ]
    cins = ([] if has_base else [None]) + [
        cin if run["params"] else None for (_, cin), run in zip(names, runs, strict=True)
    ]
    graphs = []
    for index, variable in enumerate(variables, 1):
        name = canonical[canonical_name(variable)]
        unit = equations[name].unit
        block = [
            f":GRAPH G{index:02d}",
            f":TITLE {title_prefix}{name}",
            ":X-AXIS Time",
            ":SCALE",
        ]
        for run_name, label in zip(run_names, labels, strict=True):
            block += [f":VAR {_quote(name)}", f":DATASET {run_name}", f":LABEL {label}"]
            if unit:
                block.append(f":UNITS {unit}")
        graphs.append("\n".join(block))
    files["graphs.vgd"] = "\n\n".join(graphs) + "\n"
    # 路径相对命令脚本所在目录写出，整个文件夹连同模型一起拷到 Windows 上也能用
    base_dir = Path(base_dir) if base_dir is not None else model.parent
    try:
        model_ref = Path(os.path.relpath(model.resolve(), base_dir.resolve())).as_posix()
    except ValueError:
        # Windows 上模型和输出目录不在同一个盘符时无法写相对路径，退回绝对路径
        model_ref = str(model.resolve())
    cmd = [f"SPECIAL>LOADMODEL|{model_ref}"]
    for cin, run_name in zip(cins, run_names, strict=True):
        if cin is not None:
            cmd.append(f"SIMULATE>READCIN|{cin}")
        cmd += [f"SIMULATE>RUNNAME|{run_name}", "MENU>RUN|O"]
    cmd += ["MENU>VGD|graphs.vgd"]
    files["run_all.cmd"] = "\n".join(cmd) + "\n"
    mapping = "\n".join(f"  {run}: {label}" for run, label in zip(run_names, labels, strict=True))
    files["README.txt"] = README.format(model=model_ref, mapping=mapping, count=len(variables))
    return files


def _run_name(label: str, index: int) -> str:
    """运行名用作 .vdf 文件名：保留字母数字、汉字和下划线，其余换成下划线。"""
    cleaned = re.sub(r"[^\w]+", "_", label, flags=re.UNICODE).strip("_")
    return cleaned[:40] or f"scenario_{index:02d}"


README = """在 Vensim DSS 中出原生结果图

1. 保持本文件夹与模型的相对位置不变，脚本里的模型路径：{model}
   （相对本文件夹）。Vensim 找不到文件时，把 run_all.cmd 里的路径改成绝对路径。
2. Vensim DSS 菜单 File > Run Command Script（不同版本也可能在 Tools 菜单下），
   选择 run_all.cmd。脚本依次运行基准和各情景，再载入 graphs.vgd。
3. 在控制面板 Graphs 页选择 G01、G02 … 共 {count} 张自定义图，Display 后可用
   Copy/Save 导出图片；图上曲线编号和图例都是 Vensim 原生绘制。

运行名与情景对应：
{mapping}

说明：
- 每个情景的参数变更在 scenario_XX.cin 里，一行一个“常量 = 数值”，可手工改。
- 命令脚本需要 Vensim DSS 或 Pro，PLE 版本不能执行；也可以在 Simulate 设置里
  手工载入 .cin 文件逐个运行，运行名照上表填写。
- 文件默认用 UTF-8 编码；旧版 Vensim 在中文 Windows 上读不出中文变量名时，用
  --encoding gbk 重新生成。
"""


def command_vensim_script(model, spec_path, variables, output_dir, encoding="utf-8", title=""):
    output_dir = Path(output_dir)
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8-sig"))
    if not variables:
        variables = spec.get("variables", [])
    if not variables:
        raise ValueError("请用 --var 或在实验 JSON 的 variables 中列出要出图的变量")
    files = build_files(Path(model), spec, list(variables), title, output_dir)
    targets = [output_dir / name for name in files]
    for path in targets:
        if path.exists():
            raise ValueError(f"输出已存在，请换一个目录: {path}")
    preflight_outputs(targets, [Path(model), Path(spec_path)])
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        newline_text = text.replace("\n", "\r\n")  # Vensim 运行在 Windows，统一 CRLF
        data = (
            newline_text.encode(encoding)
            if encoding != "utf-8-sig"
            else newline_text.encode("utf-8-sig")
        )
        atomic_write(output_dir / name, data)
    print(f"已生成 Vensim DSS 文件: {output_dir}")
    for name in files:
        print(f"  {name}")
    print("在 Vensim DSS 中运行 run_all.cmd，步骤见 README.txt")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("model", type=Path)
    parser.add_argument("--spec", type=Path, required=True, help="experiment 的情景 JSON")
    parser.add_argument("--var", action="append", default=[], help="要出图的变量，可多次指定")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--encoding", choices=["utf-8", "utf-8-sig", "gbk"], default="utf-8")
    parser.add_argument("--title-prefix", default="", help="自定义图标题前缀")
    args = parser.parse_args(argv)
    return command_vensim_script(
        args.model, args.spec, args.var, args.output_dir, args.encoding, args.title_prefix
    )


if __name__ == "__main__":
    raise SystemExit(main())
