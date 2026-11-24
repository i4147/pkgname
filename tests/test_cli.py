"""CLI tests: all --source modes, all exit codes, JSON output."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from pkgname.cli import app

runner = CliRunner()
MISSING_INDEX = "/nonexistent-dir/pip.json"


def _pypi_router(handler: object) -> None:
    respx.get(url__regex=r"https://pypi\.org/pypi/.*").mock(side_effect=handler)  # type: ignore[arg-type]


def test_local_taken(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app, ["check", "pyqt-search-bar", "--source", "local", "--index", str(fixture_index_path)]
    )
    assert result.exit_code == 1
    assert "TAKEN" in result.stdout


def test_local_available_with_warning(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "some-unique-random-name-xyz",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
        ],
    )
    assert result.exit_code == 0
    assert "AVAILABLE" in result.stdout
    # Rich wraps long lines; compare on whitespace-collapsed output.
    assert "may exist on PyPI" in " ".join(result.stdout.split())


def test_local_rejected_similar(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app, ["check", "walkfs", "--source", "local", "--index", str(fixture_index_path)]
    )
    assert result.exit_code == 2
    assert "REJECTED_SIMILAR" in result.stdout
    assert "walk-fs" in result.stdout
    assert "separator variant" in result.stdout
    assert "Recommended alternatives" in result.stdout


def test_local_missing_index_exit_4() -> None:
    result = runner.invoke(
        app, ["check", "anything", "--source", "local", "--index", MISSING_INDEX]
    )
    assert result.exit_code == 4
    assert "error" in result.stderr.lower() or "error" in result.stdout.lower()


def test_invalid_input_exit_3() -> None:
    result = runner.invoke(app, ["check", "bad name!"])
    assert result.exit_code == 3
    assert "invalid package name" in result.stderr or "invalid package name" in result.stdout


def test_auto_taken_via_index_zero_network(fixture_index_path: Path) -> None:
    # No respx routes registered: any HTTP request would raise.
    with respx.mock:
        result = runner.invoke(
            app, ["check", "pyqt-search-bar", "--index", str(fixture_index_path)]
        )
    assert result.exit_code == 1
    assert "TAKEN" in result.stdout
    assert len(respx.calls) == 0


def test_auto_available_pypi_404(fixture_index_path: Path) -> None:
    with respx.mock:
        _pypi_router(lambda request: httpx.Response(404))
        result = runner.invoke(
            app,
            ["check", "some-unique-random-name-xyz", "--index", str(fixture_index_path)],
        )
    assert result.exit_code == 0
    assert "AVAILABLE" in result.stdout


def test_auto_rejected_similar_via_index(fixture_index_path: Path) -> None:
    # walkfs is absent from the index but similar to walk-fs; PyPI 404s it.
    with respx.mock:
        _pypi_router(lambda request: httpx.Response(404))
        result = runner.invoke(app, ["check", "walkfs", "--index", str(fixture_index_path)])
    assert result.exit_code == 2
    assert "walk-fs" in result.stdout


def test_pypi_only_taken_with_missing_index_warning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pypi/requests/json":
            return httpx.Response(
                200,
                json={"info": {"name": "requests", "summary": "HTTP", "version": "2.31.0"}},
            )
        return httpx.Response(404)

    with respx.mock:
        _pypi_router(handler)
        result = runner.invoke(app, ["check", "requests", "--index", MISSING_INDEX])
    assert result.exit_code == 1
    assert "TAKEN" in result.stdout
    assert "falling back to PyPI API" in result.stdout


def test_pypi_source_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pypi/requests/json":
            return httpx.Response(
                200,
                json={"info": {"name": "requests", "summary": "HTTP", "version": "2.31.0"}},
            )
        return httpx.Response(404)

    with respx.mock:
        _pypi_router(handler)
        result = runner.invoke(app, ["check", "requests", "--source", "pypi"])
    assert result.exit_code == 1
    assert "PyPI API only" in result.stdout


def test_hybrid_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pypi/requests/json":
            return httpx.Response(
                200,
                json={"info": {"name": "requests", "summary": "HTTP", "version": "2.31.0"}},
            )
        return httpx.Response(404)

    with respx.mock:
        _pypi_router(handler)
        result = runner.invoke(app, ["check", "requests", "--source", "hybrid"])
    assert result.exit_code == 1
    assert "TAKEN" in result.stdout


def test_network_error_exit_4_no_traceback() -> None:
    with respx.mock:
        _pypi_router(lambda request: httpx.Response(403))
        result = runner.invoke(app, ["check", "requests", "--source", "pypi"])
    assert result.exit_code == 4
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_rate_limit_429_surfaces_exit_4() -> None:
    with respx.mock:
        _pypi_router(lambda request: httpx.Response(429))
        result = runner.invoke(app, ["check", "requests", "--source", "pypi"])
    assert result.exit_code == 4
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_json_output_local_taken(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "pyqt-search-bar",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["status"] == "TAKEN"
    assert payload["used_local_index"] is True
    assert payload["normalized"] == "pyqt-search-bar"
    assert payload["matches"][0]["reason"] == "exact match"
    assert payload["errors"] == []


def test_json_output_local_available_warning(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "some-unique-random-name-xyz",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "AVAILABLE"
    assert any("may exist on PyPI" in w for w in payload["warnings"])


def test_json_output_error_for_invalid_name() -> None:
    result = runner.invoke(app, ["check", "bad name!", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "ERROR"
    assert payload["errors"]


def test_no_recommend_flag(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "walkfs",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
            "--no-recommend",
            "--json",
        ],
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["recommendations"] == []


def test_recommend_flag_on_available(fixture_index_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "some-unique-random-name-xyz",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
            "--recommend",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "AVAILABLE"
    assert payload["recommendations"]
    assert all(r["verified_offline"] is True for r in payload["recommendations"])


def test_distance_flag(fixture_index_path: Path) -> None:
    # walk-fx is distance 1 from walk-fs with no affix relation, so a
    # threshold of 0 makes it AVAILABLE.
    result = runner.invoke(
        app,
        [
            "check",
            "walk-fx",
            "--source",
            "local",
            "--index",
            str(fixture_index_path),
            "--distance",
            "0",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "AVAILABLE"


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_index_command_json(fixture_index_path: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["index", "--index", str(fixture_index_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["available"] is True
    assert payload["entry_count"] == 20
    assert payload["cache_path"]
    assert payload["sha256"]


def test_index_command_missing_file_exit_4() -> None:
    result = runner.invoke(app, ["index", "--index", MISSING_INDEX])
    assert result.exit_code == 4
