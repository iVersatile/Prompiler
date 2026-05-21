"""Unit tests for prompiler.cli.

Covers P0 stub:
- ``--version`` prints the package version and exits 0.
- ``main()`` with no subcommand prints help and returns 0.
- ``validate <missing path>`` returns 2 with a stderr diagnostic.
- ``validate <empty dir>`` returns 0.

Covers P1.10 (PLAN.md L101 + L108):
- ``validate`` accepts a single spec file or a directory of specs.
- Clean specs (single + multi-file dir) -> exit 0.
- Lint issues (missing-description, duplicate-field-name, reserved-name)
  -> exit 1, code + path rendered to stderr.
- Load errors (YAML syntax, Pydantic validation) -> exit 1, ``file:line:col``
  rendered to stderr.
- Mixed ``.yaml`` / ``.yml`` extensions are both scanned.
- Walk is recursive across subdirectories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from prompiler import __version__
from prompiler.cli import main


def _clean_extract_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "invoice",
        "task": "extract",
        "description": "Extract billing details from a single invoice document.",
        "fields": [
            {
                "name": "vendor_name",
                "type": "string",
                "required": True,
                "description": "Legal name of the issuing vendor.",
            },
            {
                "name": "total_amount",
                "type": "decimal",
                "required": True,
                "description": "Grand total on the invoice in vendor currency.",
            },
        ],
    }


def _clean_classify_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "email_category",
        "task": "classify",
        "description": "Route inbound support email into one routing bucket.",
        "labels": [
            {"name": "billing", "description": "Payments, invoices, refunds."},
            {"name": "technical", "description": "Product not working."},
        ],
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.unit
def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


@pytest.mark.unit
def test_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower()


@pytest.mark.unit
def test_validate_empty_dir_returns_zero(tmp_path: Path) -> None:
    target = tmp_path / "prompts"
    target.mkdir()
    rc = main(["validate", str(target)])
    assert rc == 0


@pytest.mark.unit
def test_validate_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "does-not-exist"
    rc = main(["validate", str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "path not found" in err
    assert str(missing) in err


@pytest.mark.unit
def test_validate_single_clean_file(tmp_path: Path) -> None:
    f = _write_yaml(tmp_path / "invoice.yaml", _clean_extract_spec())
    rc = main(["validate", str(f)])
    assert rc == 0


@pytest.mark.unit
def test_validate_dir_of_clean_specs(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "invoice.yaml", _clean_extract_spec())
    _write_yaml(tmp_path / "email.yml", _clean_classify_spec())
    rc = main(["validate", str(tmp_path)])
    assert rc == 0


@pytest.mark.unit
def test_validate_walks_subdirectories(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    _write_yaml(nested / "invoice.yaml", _clean_extract_spec())
    rc = main(["validate", str(tmp_path)])
    assert rc == 0


@pytest.mark.unit
def test_validate_missing_description(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _clean_extract_spec()
    spec["fields"][0].pop("description")
    f = _write_yaml(tmp_path / "invoice.yaml", spec)
    rc = main(["validate", str(f)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing-description" in err
    assert str(f) in err


@pytest.mark.unit
def test_validate_duplicate_field_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _clean_extract_spec()
    spec["fields"].append(
        {
            "name": "vendor_name",
            "type": "string",
            "required": False,
            "description": "Second vendor name (duplicate to trigger lint).",
        }
    )
    f = _write_yaml(tmp_path / "invoice.yaml", spec)
    rc = main(["validate", str(f)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "duplicate-field-name" in err


@pytest.mark.unit
def test_validate_reserved_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _clean_extract_spec()
    spec["fields"][0]["name"] = "class"
    f = _write_yaml(tmp_path / "invoice.yaml", spec)
    rc = main(["validate", str(f)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "reserved-name" in err


@pytest.mark.unit
def test_validate_yaml_syntax_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "broken.yaml"
    f.write_text("name: invoice\n  bad: : :\n", encoding="utf-8")
    rc = main(["validate", str(f)])
    assert rc == 1
    err = capsys.readouterr().err
    assert str(f) in err
    assert ":" in err


@pytest.mark.unit
def test_validate_pydantic_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _clean_extract_spec()
    spec["spec_version"] = 2
    f = _write_yaml(tmp_path / "invoice.yaml", spec)
    rc = main(["validate", str(f)])
    assert rc == 1
    err = capsys.readouterr().err
    assert str(f) in err


@pytest.mark.unit
def test_validate_mixed_clean_and_dirty_dir_returns_one(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "clean.yaml", _clean_extract_spec())
    dirty = _clean_extract_spec()
    dirty["name"] = "dirty"
    dirty["fields"][0].pop("description")
    _write_yaml(tmp_path / "dirty.yaml", dirty)
    rc = main(["validate", str(tmp_path)])
    assert rc == 1


@pytest.mark.unit
def test_validate_non_yaml_files_ignored(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "clean.yaml", _clean_extract_spec())
    (tmp_path / "README.md").write_text("not a spec\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    rc = main(["validate", str(tmp_path)])
    assert rc == 0
