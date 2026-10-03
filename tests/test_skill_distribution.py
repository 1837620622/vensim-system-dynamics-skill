import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/vensim-skill"


def test_doctor_flags_installed_dependencies_below_constraints(monkeypatch):
    sys.path.insert(0, str(SKILL / "scripts"))
    import skill_cli

    versions = {"lxml": "6.0.3", "pillow": "12.2.0", "mcp": "1.30.0", "click": "8.3.3rc1"}

    def installed_version(name):
        if name not in versions:
            raise skill_cli.importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(skill_cli.importlib.metadata, "version", installed_version)
    rows = {row["package"]: row for row in skill_cli.dependency_constraints()}
    assert rows["lxml"]["status"] == rows["pillow"]["status"] == "upgrade_required"
    assert rows["mcp"]["status"] == "satisfies_constraints"
    assert rows["click"]["status"] == "version_needs_review"
    assert rows["anyio"]["status"] == "not_installed"


def test_skill_entrypoint_and_packaged_assets():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: vensim-skill\n")
    assert "description:" in text.split("---", 2)[1]
    assert (SKILL / "agents/openai.yaml").is_file()
    assert (SKILL / "assets/templates/model_template.json").is_file()
    assert (SKILL / "assets/templates/plot_config_classic.json").is_file()
    assert (SKILL / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_machine_readable_versions_stay_in_sync():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    skill_version = re.search(r"^\s+version:\s*[\"']([^\"']+)", skill, re.MULTILINE)
    project_version = re.search(r"^version\s*=\s*[\"']([^\"']+)", pyproject, re.MULTILINE)
    assert skill_version and project_version
    assert skill_version.group(1) == project_version.group(1) == "2.2.6"


def test_local_document_links_resolve():
    files = [ROOT / "README.md", ROOT / "README.en.md", *SKILL.rglob("*.md")]
    for file in files:
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            destination = target.split("#")[0]
            assert (file.parent / destination).exists(), f"Broken link in {file}: {target}"


def test_cli_runs_from_unrelated_directory(tmp_path):
    work = tmp_path / "中文 project with spaces"
    work.mkdir()
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts/skill_cli.py"), "--help"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "plot-data" in result.stdout
    assert "convergence" in result.stdout


def test_platform_wrapper_runs_from_unrelated_directory(tmp_path):
    if os.name == "nt":
        command = ["cmd.exe", "/d", "/c", str(SKILL / "skill.cmd"), "--help"]
    else:
        command = ["bash", str(SKILL / "skill.sh"), "--help"]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "Vensim System Dynamics Skill" in result.stdout
