# pkgname

Check whether a Python package name is usable on PyPI — and when it isn't,
get ranked alternative names that are free *and* acceptable.

`pkgname` classifies a name into exactly one verdict:

| Verdict | Meaning | Exit code |
|---|---|---|
| `AVAILABLE` | No exact normalized match on PyPI, and not too similar to any existing project. | 0 |
| `TAKEN` | An existing project has the same name after PEP 503 normalization (`foo-bar`, `foo_bar`, `Foo.Bar` all collapse to `foo-bar`). | 1 |
| `REJECTED_SIMILAR` | No exact match, but too similar to an existing project (e.g. `walkfs` vs `walk-fs`). PyPI's upload-time squatting protection would likely reject it, and users could confuse the two. | 2 |

For `TAKEN` and `REJECTED_SIMILAR` (and on `--recommend` for `AVAILABLE`),
`pkgname` outputs a ranked list of alternative names that are themselves
`AVAILABLE`.

## Install

```bash
pip install pkgname          # from PyPI (once published)
pipx install pkgname         # isolated CLI install
# or from source:
git clone https://github.com/example/pkgname && cd pkgname
pip install -e .             # runtime deps
pip install -e ".[dev]"      # + pytest, respx, ruff, mypy
```

Requires Python 3.10+. Optional: `pip install "pkgname[cache]"` for the
faster msgpack index cache (falls back to plain text without it).

## Quick start

```bash
pkgname check requests                    # TAKEN (exit 1)
pkgname check walkfs                      # REJECTED_SIMILAR (exit 2) + alternatives
pkgname check some-unique-random-name-xyz # AVAILABLE (exit 0)
pkgname check walkfs --json               # machine-readable output
pkgname index                             # local index statistics
```

## Data sources: `--source`

`pkgname` is offline-first: it uses a locally cached snapshot of the
[piwheels](https://piwheels.org/packages.json) package list, and confirms
against the live PyPI JSON API.

| Mode | Behavior |
|---|---|
| `auto` (default) | If the name is in the local index → `TAKEN` immediately (zero network). Otherwise query the PyPI API for the exact name; similarity is scanned against the local index. |
| `hybrid` | Always query the PyPI API for the exact name **and** consult the local index for similarity. Best accuracy, slightly slower. |
| `pypi` | Network-only; ignores `pip.json`. Use in CI. |
| `local` | Offline-only; `pip.json` is the sole source of truth. `AVAILABLE` results carry a warning that the name may still exist on PyPI. |

### The local index (`pip.json`)

Download the piwheels snapshot and put it in the working directory:

```bash
curl -o pip.json https://piwheels.org/packages.json
```

Path resolution: `--index PATH` > `$PKGNAME_INDEX` > `./pip.json`.
Normalized names are cached under `~/.cache/pkgname/` (override with
`$PKGNAME_CACHE_DIR` or `$XDG_CACHE_HOME`), keyed by the source file's
sha256+mtime+size, so warm runs load in well under 200 ms.

```bash
pkgname index                       # stats table
pkgname index --rebuild             # ignore cache, re-parse
pkgname index --json                # machine-readable stats
```

## Usage examples

### All three verdicts

```bash
$ pkgname check pyqt-search-bar --source local
TAKEN
source: offline only (local piwheels snapshot)
Conflicting projects
  Name             Reason       Distance  Version  Summary
  pyqt-search-bar  exact match  0         -        -
Recommended alternatives
  Name               Strategy   Score  Verified
  pyqt-search-bar-cli suffix   0.83   offline
  ...

$ pkgname check walkfs --source local
REJECTED_SIMILAR
source: offline only (local piwheels snapshot)
Conflicting projects
  Name     Reason             Distance  Version  Summary
  walk-fs  separator variant  1         -        -
  pywalkfs edit distance 2    2         -        -
Recommended alternatives
  Name        Strategy  Score  Verified
  walkfs-cli  suffix    0.91   offline
  ...

$ pkgname check some-unique-random-name-xyz --source local
AVAILABLE
source: offline only (local piwheels snapshot)
warning: offline check: not present in local piwheels snapshot; may exist on PyPI
```

### All four `--source` modes

```bash
pkgname check requests --source auto     # index first, PyPI fallback
pkgname check requests --source hybrid   # PyPI exact + index similarity
pkgname check requests --source pypi     # PyPI only (CI-friendly)
pkgname check requests --source local    # offline only
```

### Other options

```bash
pkgname check walkfs --distance 1        # stricter similarity threshold
pkgname check walkfs --limit 5           # at most 5 recommendations
pkgname check walkfs --no-recommend      # verdict only, no alternatives
pkgname check walkfs --recommend         # alternatives even when AVAILABLE
pkgname check walkfs --timeout 5 --concurrency 20
pkgname check walkfs -v                  # verbose logging to stderr
pkgname --version
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `AVAILABLE` |
| 1 | `TAKEN` |
| 2 | `REJECTED_SIMILAR` |
| 3 | Invalid input (bad characters, too long, empty) |
| 4 | Network / PyPI error (or, in `--source local`, index missing/corrupt) |
| 5 | Internal error (bug — the message includes a GitHub issue link) |

The CLI never prints a raw traceback unless `--verbose` is passed.

## JSON schema

`--json` emits a stable, versioned document (`schema_version: 2`).
Keys are sorted; recommendations are sorted by score (best first).

```json
{
  "schema_version": 2,
  "query": "walkfs",
  "normalized": "walkfs",
  "status": "REJECTED_SIMILAR",
  "source": "auto",
  "used_local_index": true,
  "warnings": [],
  "matches": [
    {
      "name": "walk-fs",
      "normalized": "walk-fs",
      "reason": "separator variant",
      "distance": 1,
      "summary": "Filesystem walking utilities",
      "version": "1.4.2",
      "origin": "pypi"
    }
  ],
  "recommendations": [
    {
      "name": "walkfs-cli",
      "normalized": "walkfs-cli",
      "strategy": "suffix",
      "score": 0.91,
      "verified_offline": false
    }
  ],
  "errors": []
}
```

Field notes:

- `status` is one of `AVAILABLE` / `TAKEN` / `REJECTED_SIMILAR`. On a
  hard failure (invalid input, network error) the document has
  `status: "ERROR"`, `normalized: null`, and the message in `errors`.
- `matches[].reason` is one of `"exact match"`, `"separator variant"`,
  `"edit distance N"`, `"suffix variant \`-cli\`"`, `"prefix variant \`py-\`"`.
- `matches[].origin` is `"local index (piwheels)"` or `"pypi"`.
  `summary`/`version` are only populated when metadata was fetched from PyPI.
- `recommendations[].verified_offline` is `true` in `--source local`
  (validated against the snapshot only) and `false` when confirmed via
  PyPI 404 + variant probing.
- `warnings` includes the offline-mode caveat for `--source local`
  `AVAILABLE` results, plus stdlib/keyword/brand notices.

## How it works (summary)

1. **Validate** the name against the PEP 508 grammar and PyPI's 214-char
   limit; warn (never fail) for stdlib modules, Python keywords, and
   well-known brands.
2. **Normalize** per PEP 503 (`re.sub(r"[-_.]+", "-", name).lower()`).
3. **Exact check** per `--source` (local index set membership and/or the
   PyPI JSON API, `https://pypi.org/pypi/{name}/json`).
4. **Similarity scan** against the whole local index with rapidfuzz
   Levenshtein distance ≤ `--distance`, plus confusable prefix/suffix and
   separator rules. Without an index, neighbor variants are generated and
   probed against PyPI concurrently instead.
5. **Recommendations**: generate candidates (separator swaps, affix
   additions, synonyms, word reversal, abbreviations), filter against the
   index, confirm survivors on PyPI, rank deterministically.

See [DESIGN.md](DESIGN.md) for the full design.

## Assumptions & limitations

- **The piwheels snapshot is a subset of PyPI.** piwheels only lists
  packages for which wheels were built/known to piwheels; it is *not* the
  complete PyPI namespace. Concretely:
  - `name ∈ pip.json` → almost certainly taken on PyPI (high confidence).
  - `name ∉ pip.json` → **unknown**; the package may still exist on PyPI
    as a pure-Python project. `pkgname` confirms with the live PyPI API
    unless you explicitly chose `--source local`, in which case
    `AVAILABLE` results carry a warning and should be treated as
    "not known to be taken".
- **The similarity rule is a heuristic.** PyPI's exact upload-time
  squatting rule is not public and may change. `pkgname` uses a
  conservative, configurable approximation (edit distance, separator
  variants, confusable affixes). It may occasionally flag a name PyPI
  would accept, or miss one PyPI would reject. Loosen/tighten with
  `--distance`.
- **Names are compared after PEP 503 normalization**, so `foo-bar`,
  `foo_bar`, and `Foo.Bar` are the same name (matching PyPI's behavior).
- **The index snapshot goes stale.** Refresh `pip.json` periodically
  (`curl -o pip.json https://piwheels.org/packages.json`); the cache
  invalidates automatically on content change.
- **Recommendations are best-effort.** A recommended name passed every
  check `pkgname` can perform at runtime, but PyPI may still reject it at
  upload time for reasons outside this tool's visibility (e.g. manual
  review, trademark complaints).

## Development

```bash
pip install -e ".[dev]"
pytest -q                 # unit tests (no network; respx-mocked)
ruff check .              # lint
mypy src                  # type check
PKGNAME_RUN_INTEGRATION=1 pytest -q -m integration        # real PyPI
PKGNAME_RUN_INTEGRATION=1 pytest -q -m integration_index  # real ./pip.json
```

## License

MIT.
