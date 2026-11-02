"""Stage 10 gate: README covers every required topic with commands that exist,
PROMPT_LOG follows the required format, and the package has no dead code."""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("heading", ["## Folder structure", "## Requirements", "## Install", "## Generate",
                                     "## Test, validate, reproduce", "## Output files", "## Design decisions",
                                     "## Known limitations", "## Assumptions made"])
def test_readme_has_required_sections(heading):
    assert heading in README


def test_readme_states_overview_seed_and_synthetic_origin():
    assert README.startswith("# ")
    assert "20261022" in README and "All data is synthetic" in README
    assert "python -m generator --profile starter" in README and "\npython -m generator " in README


def _help(module: str) -> str:
    return subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout


def test_every_command_in_the_readme_exists():
    commands = re.findall(r"^python -m ([\w.]+)(.*?)(?:\s+#.*)?$", README, flags=re.MULTILINE)
    assert {m for m, _ in commands} >= {"generator", "pytest", "generator.validator", "generator.reproducibility",
                                         "generator.load_sqlite"}
    for module, args in commands:
        if module == "pytest":
            continue
        options = set(re.findall(r"--[\w-]+", args))
        assert options <= set(re.findall(r"--[\w-]+", _help(module))), (module, options)


def test_readme_lists_every_output_file_kind():
    for name in ("csv/<table>.csv", "json/<table>.json", "sql/schema.sql", "sql/seed.sql", "ground_truth.csv",
                 "source_manifest.json", "validation_summary.json", "checksums.sha256"):
        assert name in README, name


def test_prompt_log_format():
    text = (ROOT / "PROMPT_LOG.md").read_text(encoding="utf-8")
    header = text.split("## Prompt 1", 1)[0]
    assert "AI responses are omitted" in header and "kept manually" in header
    numbers = [int(n) for n in re.findall(r"^## Prompt (\d+) — \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE)]
    assert numbers == list(range(1, len(numbers) + 1)) and numbers
    for line in ("ROLE", "If a test fails twice, stop and explain instead of weakening the test.",
                 "CUS-000001 | ACC-CUS-000001-01 (the -NN suffix is the account sequence for that",
                 "- Ask me only if something is truly blocking. Otherwise state the assumption and continue."):
        assert line in text, line


def test_no_dead_code_in_the_package():
    """Every top-level function, class and constant of the package is referenced somewhere."""
    sources = {p: p.read_text(encoding="utf-8") for p in [*ROOT.glob("generator/*.py"), *ROOT.glob("tests/*.py")]}
    unused = []
    for path in ROOT.glob("generator/*.py"):
        for node in ast.parse(sources[path]).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            else:
                continue
            for name in names:
                uses = sum(len(re.findall(rf"\b{re.escape(name)}\b", s)) for s in sources.values())
                if uses <= 1 and name != "main":
                    unused.append(f"{path.name}:{name}")
    assert not unused, unused


def test_every_module_and_public_function_has_a_docstring():
    missing = []
    for path in ROOT.glob("generator/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            missing.append(path.name)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                if not ast.get_docstring(node) and node.name != "main":
                    missing.append(f"{path.name}:{node.name}")
    assert not missing, missing
