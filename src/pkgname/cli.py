"""Command-line interface: ``pkgname check`` and ``pkgname index``.

Exit codes:
    0  AVAILABLE
    1  TAKEN
    2  REJECTED_SIMILAR
    3  Invalid input (bad characters, too long, ...)
    4  Network / PyPI error (or, in --source local, index missing/corrupt)
    5  Internal error (bug; surfaces a GitHub issue link)

The CLI never leaks a raw traceback unless ``--verbose`` is passed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DISTANCE,
    DEFAULT_LIMIT,
    DEFAULT_TIMEOUT,
    SCHEMA_VERSION,
)
from .index import IndexUnavailableError, LocalIndex, get_index, resolve_index_path
from .models import CheckResult, Match, Status
from .normalize import InvalidNameError, normalize, validate, warnings_for
from .pypi import PyPIClient, PyPIClientError
from .recommend import recommend
from .similarity import SimilarityConfig, classify, find_similar

EXIT_CODES = {
    Status.AVAILABLE: 0,
    Status.TAKEN: 1,
    Status.REJECTED_SIMILAR: 2,
}
EXIT_INVALID = 3
EXIT_NETWORK = 4
EXIT_INTERNAL = 5

ISSUE_LINK = "https://github.com/example/pkgname/issues"


class SourceMode(str, Enum):
    auto = "auto"
    hybrid = "hybrid"
    pypi = "pypi"
    local = "local"


app = typer.Typer(
    help="Check whether a PyPI package name is available.",
    no_args_is_help=True,
)


def _version_callback(value):
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
):
    pass


def main():
    app()


@app.command()
def check(
    name = typer.Argument(..., help="Package name to check."),
    source = typer.Option(SourceMode.auto, "--source", help="Data-source mode."),
    index_path = typer.Option(None, "--index", help="Path to pip.json."),
    json_output = typer.Option(False, "--json", help="Emit machine-readable JSON only."),
    recommend_flag = typer.Option(
        None, "--recommend/--no-recommend", help="Force or disable recommendations."
    ),
    limit = typer.Option(DEFAULT_LIMIT, "--limit", min=1, help="Max recommendations."),
    distance = typer.Option(DEFAULT_DISTANCE, "--distance", min=0, help="Levenshtein threshold."),
    timeout = typer.Option(DEFAULT_TIMEOUT, "--timeout", min=0.1, help="Per-request timeout (seconds)."),
    concurrency = typer.Option(DEFAULT_CONCURRENCY, "--concurrency", min=1, help="Max in-flight requests."),
    verbose = typer.Option(False, "--verbose", "-v", help="Verbose logging to stderr."),
):
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = asyncio.run(
            _run_check(
                name,
                source=source.value,
                index_path=index_path,
                recommend_flag=recommend_flag,
                limit=limit,
                distance=distance,
                timeout=timeout,
                concurrency=concurrency,
            )
        )
    except InvalidNameError as exc:
        _emit_error(name, str(exc), json_output)
        raise typer.Exit(EXIT_INVALID) from exc
    except (PyPIClientError, IndexUnavailableError) as exc:
        _emit_error(name, str(exc), json_output)
        raise typer.Exit(EXIT_NETWORK) from exc
    except Exception as exc:
        if verbose:
            raise
        _emit_error(
            name,
            f"internal error: {exc} (please report a bug: {ISSUE_LINK})",
            json_output,
        )
        raise typer.Exit(EXIT_INTERNAL) from exc
    _render(
        result,
        json_output,
        recommendations_requested=_wants_recommendations(recommend_flag, result.status),
    )
    raise typer.Exit(EXIT_CODES[result.status])


@app.command()
def index(
    index_path = typer.Option(None, "--index", help="Path to pip.json."),
    rebuild = typer.Option(False, "--rebuild", help="Ignore cache and rebuild."),
    json_output = typer.Option(False, "--json", help="Emit machine-readable JSON."),
):
    idx = LocalIndex(resolve_index_path(index_path), use_cache=not rebuild)
    idx.load()
    stats = idx.stats()
    if json_output:
        typer.echo(json.dumps(stats.to_dict(), indent=2, sort_keys=True))
    else:
        console = Console()
        table = Table(title="Local index")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Source path", stats.source_path)
        table.add_row("File size", f"{stats.file_size:,} bytes")
        table.add_row("SHA-256", stats.sha256 or "-")
        table.add_row("Entries", f"{stats.entry_count:,}")
        table.add_row("Cache path", stats.cache_path or "-")
        table.add_row("Load time", f"{stats.load_time_ms:.1f} ms")
        table.add_row("Available", str(stats.available))
        if stats.error:
            table.add_row("Error", stats.error)
        console.print(table)
    if not idx.available:
        raise typer.Exit(EXIT_NETWORK)


def _wants_recommendations(flag, status):
    return flag if flag is not None else status != Status.AVAILABLE


async def _run_check(
    name,
    *,
    source,
    index_path,
    recommend_flag,
    limit,
    distance,
    timeout,
    concurrency,
):
    validate(name)
    normalized = normalize(name)
    sim_config = SimilarityConfig(distance=distance)
    result = CheckResult(
        query=name,
        normalized=normalized,
        status=Status.AVAILABLE,
        source=source,
        used_local_index=False,
        warnings=warnings_for(normalized),
    )

    index = None
    if source in ("auto", "hybrid", "local"):
        index = get_index(index_path)
        if index.available:
            result.used_local_index = True
        elif source == "local":
            raise IndexUnavailableError(f"local index unavailable: {index.error}")
        else:
            result.warnings.append(f"local index unavailable ({index.error}); falling back to PyPI API")

    client = None
    try:
        if source in ("auto", "hybrid", "pypi"):
            client = PyPIClient(timeout=timeout, concurrency=concurrency)

        exact, used_network = await _exact_check(source, normalized, index, client)

        matches = []
        if exact is not None:
            matches = [exact]
        elif index is not None and index.available:
            matches = find_similar(
                normalized,
                index.names_list,
                index.names_set,
                sim_config,
                length_buckets=index.length_buckets,
            )
        elif client is not None:
            matches = await client.probe_variants(normalized, sim_config)

        result.matches = matches
        result.status = classify(exact=exact is not None, matches=matches)

        if source == "local" and result.status == Status.AVAILABLE:
            result.warnings.append("offline check: not present in local piwheels snapshot; may exist on PyPI")

        if _wants_recommendations(recommend_flag, result.status):
            verify_online = source != "local" and used_network
            result.recommendations = await recommend(
                normalized,
                index=index if (index is not None and index.available) else None,
                client=client if verify_online else None,
                sim_config=sim_config,
                limit=limit,
                verify_online=verify_online,
            )
    finally:
        if client is not None:
            await client.close()
    return result


async def _exact_check(
    source,
    normalized,
    index,
    client,
):
    if source == "local":
        if index is not None and normalized in index:
            return (
                Match(name=normalized, normalized=normalized, reason="exact match", distance=0),
                False,
            )
        return None, False

    if source == "auto":
        if index is not None and index.available and normalized in index:
            return (
                Match(name=normalized, normalized=normalized, reason="exact match", distance=0),
                False,
            )
        if client is not None:
            return await _pypi_exact(normalized, client), True
        return None, False

    if source == "hybrid":
        if client is not None:
            match = await _pypi_exact(normalized, client)
            if match is not None:
                return match, True
        if index is not None and index.available and normalized in index:
            return (
                Match(name=normalized, normalized=normalized, reason="exact match", distance=0),
                True,
            )
        return None, True

    # source == "pypi"
    if client is not None:
        return await _pypi_exact(normalized, client), True
    return None, False


async def _pypi_exact(normalized, client):
    info = await client.get_project(normalized)
    if info is None:
        return None
    return Match(
        name=info.name,
        normalized=normalized,
        reason="exact match",
        distance=0,
        summary=info.summary,
        version=info.version,
        origin="pypi",
    )


def _source_line(result):
    if result.source == "local":
        return "offline only (local piwheels snapshot)"
    if result.source == "pypi":
        return "PyPI API only"
    if result.used_local_index:
        return "local index (piwheels) + PyPI API"
    return "PyPI API (local index unavailable)"


def _render(result, as_json, *, recommendations_requested):
    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    console = Console()
    style = {
        Status.AVAILABLE: "green",
        Status.TAKEN: "red",
        Status.REJECTED_SIMILAR: "yellow",
    }[result.status]
    console.print(
        Panel(
            f"[{style} bold]{result.status.value}[/]",
            title=f"pkgname check {result.query}",
            expand=False,
        )
    )
    console.print(f"source: {_source_line(result)}")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if result.matches:
        table = Table(title="Conflicting projects")
        table.add_column("Name")
        table.add_column("Reason")
        table.add_column("Distance", justify="right")
        table.add_column("Version")
        table.add_column("Summary")
        for match in result.matches:
            table.add_row(
                match.name,
                match.reason,
                str(match.distance),
                match.version or "-",
                match.summary or "-",
            )
        console.print(table)
    if result.recommendations:
        table = Table(title="Recommended alternatives")
        table.add_column("Name")
        table.add_column("Strategy")
        table.add_column("Score", justify="right")
        table.add_column("Verified")
        for rec in result.recommendations:
            table.add_row(
                rec.name,
                rec.strategy,
                f"{rec.score:.2f}",
                "offline" if rec.verified_offline else "PyPI 404",
            )
        console.print(table)
    elif recommendations_requested and result.status != Status.AVAILABLE:
        console.print("No safe alternatives found. Try a longer name or re-run with --distance 1.")


def _emit_error(query, message, as_json):
    if as_json:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "query": query,
            "normalized": None,
            "status": "ERROR",
            "source": None,
            "used_local_index": False,
            "warnings": [],
            "matches": [],
            "recommendations": [],
            "errors": [message],
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        Console(stderr=True).print(f"[red]error:[/] {message}")


if __name__ == "__main__":
    main()
