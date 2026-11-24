# pkgname — Design

This document explains the verdict logic, data-source modes, similarity
heuristic, recommendation engine, index caching, and concurrency choices
behind `pkgname`. For usage, see [README.md](README.md).

## Verdict logic

Every check produces exactly one verdict:

```
validate(name) ──fail──> exit 3 (InvalidNameError)
     │
normalize(name)  (PEP 503)
     │
exact check (per --source) ──hit──> TAKEN
     │ miss
similarity scan ──any match──> REJECTED_SIMILAR
     │ none
     └────────────────────────> AVAILABLE
```

- **Exact check** compares the normalized name against the local index set
  and/or the PyPI JSON API (`GET https://pypi.org/pypi/{name}/json`).
- **Similarity scan** runs only when there is no exact match (the verdict is
  already `TAKEN`; scanning would add nothing).
- **Recommendations** are generated when the verdict is `TAKEN` or
  `REJECTED_SIMILAR`, or when `--recommend` is passed for `AVAILABLE`.

## Data-source modes

| Mode | Exact check | Similarity scan | Recommendation verification |
|---|---|---|---|
| `auto` (default) | Local index first; PyPI only on index miss | Local index (whole-index scan) | Online only if the exact check already used the network; otherwise offline |
| `hybrid` | PyPI API **and** local index | Local index | Online (PyPI 404 + variant probe) |
| `pypi` | PyPI API only | Variant probing against PyPI | Online |
| `local` | Local index only | Local index | Offline (`verified_offline: true`) |

Design notes:

- **`auto` + index hit = zero network calls**, including recommendation
  verification. This is the fast happy path: a name known to be taken from
  the snapshot never touches the network. (Verified by a respx test with no
  routes registered.)
- **`local` is the only mode that treats the snapshot as authoritative.**
  Its `AVAILABLE` verdicts always carry the warning
  `"offline check: not present in local piwheels snapshot; may exist on PyPI"`.
- A missing/corrupt index in `auto`/`hybrid` degrades to PyPI-only with a
  warning; in `local` it is a hard error (exit 4).

## The piwheels snapshot caveat

`pip.json` (from <https://piwheels.org/packages.json>) only lists packages
for which wheels were built/known to piwheels. It is **not** the complete
PyPI namespace:

- `name ∈ pip.json` → almost certainly taken on PyPI (high confidence).
- `name ∉ pip.json` → **unknown**; the package may still exist on PyPI as a
  pure-Python project. `pkgname` confirms with the live PyPI API unless
  `--source local` was chosen explicitly.

## Local index: parsing and caching

- **Streaming tokenizer, not `json.load`.** The published file is ~20 MB
  (920k entries at the time of writing) and minified onto a single line, so
  a line-based parser would fall back to loading the whole document. Instead,
  `LocalIndex._iter_entry_texts` walks the character stream tracking
  string/escape state and bracket depth, yielding complete top-level array
  elements one at a time. Memory is bounded by the largest single entry
  (~tens of bytes), regardless of file size or layout (one-per-line files
  parse identically). Unparseable elements are counted as "skipped"; a
  salvage step retries from the last `[` in case garbage was glued to a
  valid entry. A file that yields zero entries is treated as corrupt.
- **Persistent cache.** Normalized names are cached at
  `~/.cache/pkgname/index-<sha256>.msgpack` (override: `$PKGNAME_CACHE_DIR`,
  `$XDG_CACHE_HOME`). The cache header stores sha256+mtime_ns+size; a hit
  requires all three to match the current source file, so edits (even with
  preserved size) invalidate. If `msgpack` is not installed, a
  newline-delimited text cache is used instead (first line = JSON header).
  Writes are atomic (temp file + rename). A corrupt cache is ignored and
  rebuilt from source.
- **Lazy derived structures.** The membership `set` and the
  length→names buckets are built on first use, not at load time. This keeps
  `pkgname index` (stats only) at ~110 ms warm, and `check` pays ~150 ms
  each only when a fuzzy scan actually runs.
- **Length bucketing.** Levenshtein distance is at least the length
  difference, so fuzzy scans only consider names within ±`--distance`
  characters of the query length. Buckets make pool construction a dict
  lookup instead of a Python scan over ~1M names (measured: 160 ms → ~1 ms
  per query).
- **Singleton.** `get_index()` returns a process-wide instance keyed by
  resolved path (`--index` > `$PKGNAME_INDEX` > `./pip.json`), lazy-loaded
  on first use. `reset_index()` exists for tests.

## Similarity heuristic

PyPI's upload-time squatting rule is not public, so `pkgname` uses a
conservative, configurable approximation (`SimilarityConfig`):

1. **Exact** after normalization → distance 0.
2. **Separator-only** difference (`walkfs` vs `walk-fs`) → similar.
3. **Levenshtein distance ≤ `--distance`** (default 2) → similar.
4. **Confusable affix** add/remove (`py-`, `python-`, `py`, `-py`, `-cli`,
   `-tool`, `-lib`, `-kit`, `-utils`, `-dev`, `-core`, `get-`, `-next`,
   `-ng`, `-alt`) → similar, **independent of the distance threshold**
   (e.g. `walk-fs-cli` vs `walk-fs` is distance 4 but still similar).

Rules 2–4 are checked against the whole local index: rapidfuzz
`process.extract` with `Levenshtein.distance` and `score_cutoff=distance`
over the length-filtered pool (results are post-filtered with an explicit
distance computation, so scorer/cutoff semantics can never leak through),
plus O(1) set lookups for affix add/remove (equivalent to a substring pass
over the index, but far cheaper).

Every match carries a `reason`: `"exact match"`, `"separator variant"`,
`"edit distance N"`, `"suffix variant \`-cli\`"`, `"prefix variant \`py-\`"`.

Without a local index, the fallback is **variant probing**: generate
neighbor variants (separator forms, affix add/remove, single-char
deletions — bounded to ~80) and check each against the PyPI API
concurrently. Any variant that exists is a conflict.

## Recommendation engine

### Candidate generation (all strategies, deduped after normalization)

- **Separator swaps**: `walk-fs` ↔ `walkfs` (also via anchor-splitting
  `walkfs` → `[walk, fs]` using the synonym keys).
- **Suffix additions**: `-cli -tool -lib -kit -utils -dev -core -next -ng
  -alt -lite -mini -py 2 3`; **prefix additions**: `py- python- get-`.
- **Synonyms** per segment: `fs→filesystem`, `walk→traverse|scan|iter`,
  `dir→directory`, `pkg→package`, `cfg→config`, `util→utils`,
  `db→database`.
- **Word reversal**: `walk-fs` → `fs-walk`.
- **Abbreviations**: drop trailing vowels, then all vowels (conservative:
  every segment must keep ≥2 chars).

Generation is bounded (`MAX_CANDIDATES = 2000`).

### Two-phase validation

- **Phase A (local index)**: reject if the candidate is in the index, or if
  `is_too_similar` (the *same* rules used for the verdict: exact, affix,
  or edit distance ≤ threshold) finds a conflict. Cheap checks (set
  membership, affix lookups) run before the fuzzy scan.
- **Phase B (PyPI API)**: survivors must return 404, and their own neighbor
  variants must all 404 (re-verifying similarity against the live name
  space). Skipped in `--source local` and in `auto` when the verdict came
  from the index (zero-network happy path); then `verified_offline: true`.
  Phase B stops as soon as `--limit` valid recommendations are found.

### Ranking

Deterministic, best first:

```
score = 1.0 − 0.08·edit_distance − 0.04·strategy_rank − 0.005·length
```

with strategy ranks `separator(0) < suffix(1) < prefix(2) < synonym(3) =
reversal(3) < abbreviation(4)`. Edit distance is the primary key (per spec),
strategy preference secondary, length tertiary; ties break by name. Scores
are clamped to [0, 1] and rounded to 4 decimals.

## PyPI client: concurrency and caching

- **Async httpx** with a descriptive `User-Agent: pkgname/{version}`.
- **Bounded concurrency**: a semaphore (default 10) limits in-flight
  requests; variant probing fans out with `asyncio.gather` under the same
  semaphore, so probing stays polite.
- **Retries**: tenacity `AsyncRetrying`, exponential backoff (1s, 2s, …,
  capped), max 3 attempts, on 429/5xx and transport/timeout errors only.
  Other 4xx fail fast. Exhausted retries surface as a typed
  `PyPIClientError` → exit 4 with a clean message, never a traceback.
- **In-process LRU cache** (default 4096 entries) keyed by normalized name,
  including 404s, so repeated probes of the same variant are free.

## Error handling and exit codes

| Code | Meaning | Source |
|---|---|---|
| 0 | AVAILABLE | verdict |
| 1 | TAKEN | verdict |
| 2 | REJECTED_SIMILAR | verdict |
| 3 | Invalid input | `InvalidNameError` |
| 4 | Network / PyPI error; index missing/corrupt in `--source local` | `PyPIClientError`, `IndexUnavailableError` |
| 5 | Internal error | any other exception (message includes a GitHub issue link) |

The top-level CLI handler catches everything; only `--verbose` re-raises.

## Measured performance (real snapshot: 920,101 names, 21.6 MB)

| Operation | Time |
|---|---|
| Cold parse (hash + stream-parse + cache write) | ~5 s (once) |
| Warm cache load (`pkgname index`) | ~110 ms (spec: <200 ms) |
| `pkgname index` warm, end-to-end | ~0.5 s (spec: <1 s) |
| `check` TAKEN via index, warm, end-to-end | ~1.4 s |
| Similarity scan vs 920k names (`find_similar`) | ~85 ms |
| Recommendation Phase A (21 candidates) | ~180 ms |

The spec's "<300 ms" happy-path target for `check` is not reachable in
CPython once interpreter startup + imports (~250 ms for typer/rich/httpx/
rapidfuzz) are counted; the binding acceptance criteria (correct verdict,
zero network calls, <1 s warm `index`) are all met. The slowest path is
recommendation verification in `--source pypi` mode (no index → every
candidate is probed live); bound it with `--limit`.

## Interpretation decisions (where the spec was ambiguous)

1. **Recommendation validity = verdict validity.** Phase A rejects a
   candidate if it would itself be `TAKEN`/`REJECTED_SIMILAR` under the
   same rules (distance ≤ threshold **or** affix/separator variant). This
   is stricter than a literal reading of "distance ≤ --distance AND
   affix rules", but it is the only reading consistent with "recommend
   alternatives that are themselves AVAILABLE" — e.g. `walk-fs-cli` is
   correctly rejected when `walk-fs` exists.
2. **Similarity scan is skipped after an exact match.** The verdict is
   already `TAKEN`; scanning would only add latency (and, in `pypi` mode,
   ~80 API calls).
3. **`auto` + index hit ⇒ fully offline**, including recommendation
   verification (`verified_offline: true`). Online verification kicks in
   only when the exact check already used the network.
4. **Affix rules ignore `--distance`.** They exist precisely to catch
   conflicts beyond the edit-distance threshold.
5. **The real `pip.json` is minified single-line**, not one-entry-per-line.
   The streaming tokenizer handles both layouts; see "Local index" above.
