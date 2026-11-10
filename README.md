# Death Zone Database

Expedition analytics for the Nepal Himalayas, built from [The Himalayan Database](https://www.himalayandatabase.com/) — the record of every expedition to Nepal's significant peaks since 1905.

The pipeline reads the official database in its native dBASE/FoxPro format, decodes it against the published data dictionary, builds a DuckDB warehouse, and exports Parquet for a Streamlit dashboard focused on the 8000 m peaks and the hired staff who make those expeditions possible.

Note: This code was made using Claude Code.
The medallion design, techstack and dashboard items were
provided by me, but the actual step of writing the code was
performed by Claude.

## Running it
The system requires the following specs to run this:
1. Docker desktop up and running
2. Python >= 3.12

Steps:
```bash
# Get the containers running
docker compose up

# Create virtual environemnt and install necessary packages
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e .

# Install the project, the dev group, and the dashboard extra
uv sync --all-groups --extra dashboard

# Build the data
uv run dzdb download        # fetch the archive (idempotent, checksummed)
uv run dzdb extract         # unpack the DBF tables and memo sidecars
uv run dzdb silver          # typed, decoded, keyed tables in DuckDB
uv run dzdb gold            # dashboard tables, exported to data/gold/*.parquet
```

`uv sync --all-groups` installs the project plus every [PEP 735](https://peps.python.org/pep-0735/) dependency *group* — here just `dev`: pytest, ruff and pre-commit. Groups are not extras, so it does **not** pull in Streamlit. `--extra dashboard` adds that. For pipeline work alone, `uv sync --all-groups` is enough.

Each step is independently runnable so an orchestrator can schedule them as separate tasks rather than one monolith. Every step also skips its own work when neither the source data nor the transform code has changed; pass `--force` to rebuild anyway.

`make fetch`, `make silver` and `make gold` wrap these. `make help` lists every target.

## Running the dashboard

The dashboard needs `dzdb gold` to have run — it reads `data/gold/*.parquet` and cannot rebuild them.

```bash
uv run --extra dashboard streamlit run streamlit_app.py
```

Or `make dashboard`, which runs exactly that. Then open:

**<http://localhost:8501>**

Streamlit picks port 8501 by default. If it is already in use, pass another one and open that instead:

```bash
uv run --extra dashboard streamlit run streamlit_app.py --server.port 8600
```

Useful flags while developing:

```bash
--server.runOnSave false   # do not rerun the script on every file save
--server.address 127.0.0.1 # listen locally only (the default binds all interfaces)
--server.headless true     # do not try to open a browser
```

Stop it with `Ctrl-C` in the terminal running it.

`streamlit_app.py` at the repository root is also what Streamlit Community Cloud runs: it puts `src/` on `sys.path`, since Cloud does not pip-install the project and the package lives under `src/`. Deploying needs `data/gold/*.parquet` committed — `.gitignore` ignores all of `data/` except those four files, 868 KB in total.

### Looking at the data between steps

```bash
uv run dzdb inspect                   # what exists at each stage, with row counts
uv run dzdb inspect exped --fill      # per-column fill rates for one source table
uv run python scripts/show-tables.py  # interactive: pick a layer, table and row count
```

`scripts/show-tables.py` is standalone — it imports only `duckdb` and locates the data directory relative to itself, so it runs without the package installed.

## Layout

```
streamlit_app.py       dashboard entry point; also what Streamlit Cloud runs

src/death_zone/
├── config.py          paths and upstream URLs; DZDB_DATA_DIR overrides the data root
├── cli.py             the `dzdb` command: one subcommand per pipeline step
├── etl/
│   ├── download.py    fetch the archive, unpack the tables
│   ├── load.py        read the .dbf files via dbfread
│   ├── codes.py       integer code tables, transcribed from Appendix B
│   ├── silver.py      typed, decoded, keyed tables
│   ├── gold.py        dashboard tables + Parquet export
│   ├── provenance.py  source + code fingerprints, so a rerun can skip the work
│   └── describe.py    what is on disk at each stage
└── dashboard/
    ├── app.py         layout, charts, captions
    └── queries.py     SQL against the gold Parquet, with Streamlit caching

data/raw/              gitignored; the archive as downloaded, plus its manifest
data/interim/          gitignored; the unpacked tables
data/gold/             committed; 868 KB of Parquet the dashboard reads
tests/                 the extraction contract
scripts/               requirements export/check, and the table viewer
archive/               gitignored; code cut out of the live tree
```

### Data layers

| Layer | Where | What it is |
| --- | --- | --- |
| Source | `data/interim/himdata/*.dbf` | The upstream tables, byte-for-byte, from a checksummed archive |
| Silver | `data/death_zone.duckdb` | Typed, decoded, keyed, narrowed — **correct** |
| Gold | `data/gold/*.parquet` | Denormalised for reading, fixed definitions — **agreed** |

**There is no bronze layer.** The extracted `.dbf` files already *are* the upstream rows unchanged, and re-deriving them from the archive takes four seconds, so a DuckDB copy of them would be a third identical copy buying no capability.

**Silver** is where the data becomes trustworthy: `expedition_id_year` as a real primary key, empty strings turned into `NULL`, ambiguous zeros resolved where a companion column disambiguates them, integer codes decoded, and 182 upstream columns cut to the ones an analysis uses. `silver_peak` (503 rows), `silver_expedition` (11,854), `silver_climber` (94,999), `silver_code` (111).

**Gold** buys no speed — at 95,000 rows DuckDB answers any of these in milliseconds either way. It exists to pin down *definitions*. `gold_climb` is the denormalised climber × expedition × peak join the dashboard reads; `gold_worker_risk`, `gold_death_cause` and `gold_support` state in SQL exactly what "non-hired", "unsupported" and "success" mean, once, so no two panels can quietly disagree.

Gold is Parquet rather than DuckDB tables for one reason: a DuckDB read-write connection takes a file lock that shuts out readers entirely. If the dashboard queried the warehouse, `dzdb gold` and the dashboard could never run at the same time.

## The data

The official download is a 43 MB zip holding a Visual FoxPro 9 application plus its tables. We only use the tables for our use case.
The website releases data twice a year: Spring-Summer and Autumn-Winter
**Only the tables are used; the bundled `.exe` is never run.** The
format is self-describing, so no Windows, Wine or CrossOver is needed.

A fourth upstream table, `refer` (16,451 literature citations), is deliberately not extracted: it is a bibliography and no planned analysis reads it. Add it back to `HDB_TABLES` in `config.py` and it extracts again.

**The field definitions and every integer code list are in Appendix B of the Himalayan Database Program Guide**, a separate 35 MB PDF download — not on any web page, and not in the archive.

As of the Autumn-Winter 2025 release (data files dated 2026-07-10):

| Table | Rows | Fields |
| --- | --- | --- |
| `exped` | 11,854 | 66 |
| `members` | 94,999 | 78 |
| `peaks` | 503 | 25 |

Published twice a year and replaced in place at the same URL, which is why the download step records a checksum rather than trusting the filename.

- **Encoding is cp1252**, not UTF-8. Climber names carry accents.

### Attribution

The Himalayan Database is free to download and asks to be cited. The archive is not redistributed here — `dzdb download` fetches it from source, and `data/` is gitignored except for the gold layer.

> The Himalayan Database, compiled by Elizabeth Hawley and maintained by
> Richard Salisbury, Billi Bierling and colleagues. <https://www.himalayandatabase.com/>

## Dependencies

`pyproject.toml` is the source of truth, in three separate tables:

- `[project.dependencies]` ([PEP 621](https://peps.python.org/pep-0621/)) — `duckdb`, `pandas`, `pyarrow`, `typer`, `dbfread`. Needed to run the pipeline.
- `[project.optional-dependencies]` — the `dashboard` extra: `streamlit`, which brings Altair with it, so the charts add no further dependency.
- `[dependency-groups]` ([PEP 735](https://peps.python.org/pep-0735/)) — the `dev` group: `pytest`, `ruff`, `pre-commit`. Never shipped.

Extras and groups are installed differently: `uv sync --all-groups` gets `dev`, `--extra dashboard` gets Streamlit. There is deliberately no `[tool.uv.sources]`, so plain `pip install -e .` resolves the project without uv.

### How requirements.txt is generated

`uv.lock` pins the exact resolved set with hashes. **`requirements.txt` is generated from `uv.lock` by `scripts/export-requirements.sh`**, which runs:

```bash
uv export --format requirements.txt --no-dev --extra dashboard --no-emit-project
```

So it holds the runtime set *including* the dashboard extra — which is what makes the Streamlit Cloud deploy work, since Cloud installs from this file — and excludes the dev group and the project itself. It is a build artefact: never edit it by hand.

```bash
make lock      # uv lock + re-export requirements.txt
```

Three things stop the export going stale:

1. A **pre-commit hook** re-exports it whenever `pyproject.toml` or `uv.lock`
   changes. pre-commit fails a commit when a hook rewrites a file, so re-stage
   and commit again.
2. A second hook runs `scripts/check-requirements.sh` on every commit, which diffs the committed file against a fresh export and fails on any difference.
3. **CI** runs the same check, so it cannot be bypassed with `--no-verify`.

Install the hooks once:

```bash
make hooks
```

Python 3.12 is pinned in `.python-version`. Every library in the stack requires ≥3.10 and most require ≥3.11.
