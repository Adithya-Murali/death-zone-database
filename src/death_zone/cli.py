"""Command-line entry point: one subcommand per pipeline step.

`[project.scripts]` in pyproject.toml maps the name `dzdb` to the `app` object
below, so installing the project puts a `dzdb` command on PATH. Typer turns
each decorated function into a subcommand and its arguments into flags:

    dzdb download        fetch the upstream archive (idempotent, checksummed)
    dzdb extract         unpack the tables and their memo sidecars
    dzdb silver          build the typed, decoded, keyed warehouse tables
    dzdb gold            build the dashboard tables and export Parquet
    dzdb inspect         look at what is on disk between steps

Each step is independently runnable so an orchestrator can schedule them as
separate tasks rather than one monolith. Nothing beyond ingest exists yet.
"""

from __future__ import annotations

import logging

import typer

app = typer.Typer(
    add_completion=False,
    help="Build the Nepal Himalaya expedition warehouse from the Himalayan Database.",
    no_args_is_help=True,
)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Log each step.")) -> None:
    """Runs before any subcommand; this is where global flags belong."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def download(force: bool = typer.Option(False, help="Re-fetch even if a copy is on disk.")) -> None:
    """Fetch the official Himalayan Database archive."""
    from death_zone.etl.download import download_archive

    record = download_archive(force=force)
    typer.echo(
        f"{record.filename}  {record.bytes / 1e6:.1f} MB  "
        f"sha256:{record.short_digest}  fetched {record.fetched_at}"
    )


@app.command()
def extract(force: bool = typer.Option(False, help="Overwrite already-extracted tables.")) -> None:
    """Unpack the tables from the archive."""
    from death_zone.etl.download import extract_tables

    for path in extract_tables(force=force):
        typer.echo(f"{path.name}  {path.stat().st_size / 1e6:.1f} MB")


@app.command()
def silver(
    force: bool = typer.Option(
        False, help="Rebuild even when the source and transform code are unchanged."
    ),
) -> None:
    """Build the silver tables from the extracted DBF files."""
    from death_zone.etl.silver import build

    for name, rows in build(force=force).items():
        typer.echo(f"{name}  {rows:,} rows")


@app.command()
def gold(
    force: bool = typer.Option(
        False, help="Rebuild even when the source and transform code are unchanged."
    ),
) -> None:
    """Build the gold tables, running silver first if needed."""
    from death_zone.etl.gold import build

    for name, rows in build(force=force).items():
        typer.echo(f"{name}  {rows:,} rows")


@app.command()
def inspect(
    table: str = typer.Argument("", help="One table; omit for an overview of every stage."),
    rows: int = typer.Option(0, help="Also print this many sample records."),
    fill: bool = typer.Option(False, help="Also print per-column fill rates (reads the table)."),
) -> None:
    """Show what exists on disk, and the shape of one table."""
    from death_zone import config
    from death_zone.etl import describe

    if not table:
        _overview(describe)
        return

    if table not in config.HDB_TABLES:
        raise typer.BadParameter(f"unknown table {table!r}; expected one of {config.HDB_TABLES}")

    for name, type_code, length, decimals in describe.columns(table):
        typer.echo(f"  {name:<12} {type_code} ({length},{decimals})")

    if fill:
        typer.echo("\n  populated:")
        for col in sorted(describe.fill(table), key=lambda c: c.percent):
            extra = ""
            if col.type_code == "L":
                extra = f"   True={col.true_} False={col.false_} None={col.none_}"
            typer.echo(f"  {col.percent:6.2f}%  {col.column:<12} {col.type_code}{extra}")

    if rows:
        for record in describe.sample(table, rows):
            typer.echo("")
            for key, value in record.items():
                shown = repr(value)
                typer.echo(f"  {key:<12} {shown[:100]}")


def _overview(describe) -> None:
    """Every stage, in pipeline order, saying plainly what has not run."""
    from death_zone import config

    tables = describe.source_tables()
    typer.echo(f"source  {config.INTERIM_DIR / config.HDB_TABLE_SUBDIR}")
    if not tables:
        typer.echo("  (nothing extracted; run `dzdb extract`)")
    for t in tables:
        typer.echo(
            f"  {t.name:<10}{t.records:>8,} rows  {t.fields:>3} cols  "
            f"upstream {t.upstream_date}  {t.megabytes:>6.1f} MB"
        )

    typer.echo(f"\nwarehouse  {config.WAREHOUSE}")
    counts = describe.warehouse_tables()
    if not counts:
        typer.echo("  (not built)")
    for name, n in counts.items():
        typer.echo(f"  {name:<24}{n:>9,} rows")

    records = describe.builds()
    if records:
        typer.echo("")
        for r in records:
            typer.echo(f"  build {r.step:<10} source:{r.short_source}  built {r.built_at}")


if __name__ == "__main__":
    app()
