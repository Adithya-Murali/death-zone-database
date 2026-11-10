"""Fetch the official Himalayan Database archive and unpack its tables.

The upstream zip is republished in place twice a year, so the SHA-256 recorded
here is how a rerun tells "already downloaded" from "upstream changed".
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from death_zone import config

USER_AGENT = "death-zone-database/0.1 (+https://github.com/)"
CHUNK = 1 << 16
MANIFEST_NAME = "download-manifest.json"


@dataclass(frozen=True)
class DownloadRecord:
    """Provenance for one fetch of the upstream archive."""

    url: str
    filename: str
    bytes: int
    sha256: str
    fetched_at: str

    @property
    def short_digest(self) -> str:
        return self.sha256[:12]


def _manifest_path() -> Path:
    return config.RAW_DIR / MANIFEST_NAME


def read_manifest() -> DownloadRecord | None:
    path = _manifest_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return DownloadRecord(**data)


def _write_manifest(record: DownloadRecord) -> None:
    _manifest_path().write_text(json.dumps(asdict(record), indent=2) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download_archive(*, force: bool = False) -> DownloadRecord:
    """Download the archive unless an identical copy is already on disk."""
    config.ensure_dirs()
    target = config.RAW_DIR / config.HDB_ARCHIVE_NAME
    previous = read_manifest()

    if target.exists() and not force:
        digest = _sha256(target)
        if previous is not None and previous.sha256 == digest:
            return previous
        # Present but unrecorded, or recorded with a different digest: trust the
        # file on disk and refresh the manifest rather than re-downloading.
        record = DownloadRecord(
            url=config.HDB_ARCHIVE_URL,
            filename=target.name,
            bytes=target.stat().st_size,
            sha256=digest,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_manifest(record)
        return record

    request = Request(config.HDB_ARCHIVE_URL, headers={"User-Agent": USER_AGENT})
    partial = target.with_suffix(target.suffix + ".part")
    with urlopen(request) as response, partial.open("wb") as handle:  # noqa: S310 - fixed https URL
        shutil.copyfileobj(response, handle, CHUNK)
    partial.replace(target)

    record = DownloadRecord(
        url=config.HDB_ARCHIVE_URL,
        filename=target.name,
        bytes=target.stat().st_size,
        sha256=_sha256(target),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    _write_manifest(record)
    return record


def extract_tables(*, force: bool = False) -> list[Path]:
    """Unpack the four wanted tables: the .dbf records and their .fpt sidecars.

    Names are normalised to a lower-case stem and extension, so each table's
    two files differ only in extension. Upstream mixes cases, and a reader
    looking for a memo sidecar by name finds nothing when the stems disagree on
    a case-sensitive filesystem.

    The archive also contains Himal 2.75.exe, three Windows DLLs and the .CDX
    indexes. Those are deliberately left inside the zip: nothing here runs or
    reads them.
    """
    config.ensure_dirs()
    archive = config.RAW_DIR / config.HDB_ARCHIVE_NAME
    if not archive.exists():
        raise FileNotFoundError(f"archive not found at {archive}; run the download step first")

    destination = config.INTERIM_DIR / config.HDB_TABLE_SUBDIR
    destination.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _normalised_name(Path(info.filename))
            if not name:
                continue
            out = destination / name
            if out.exists() and not force:
                extracted.append(out)
                continue
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            extracted.append(out)

    missing = sorted(set(_wanted_names()) - {path.name for path in extracted})
    if missing:
        raise RuntimeError(
            f"{archive.name} is missing {missing}; the archive layout may have changed upstream"
        )
    return sorted(extracted)


def _wanted_names() -> list[str]:
    """Every file extraction should produce: one per table per suffix."""
    return [name + suffix for name in config.HDB_TABLES for suffix in config.TABLE_SUFFIXES]


def _normalised_name(member: Path) -> str:
    """The flat lower-case name a zip member is written under, or "" to skip it.

    Matching is on the basename, not the path, so a renamed upstream directory
    does not silently change where the tables land.
    """
    stem = member.stem.lower()
    suffix = member.suffix.lower()
    if stem not in config.HDB_TABLES or suffix not in config.TABLE_SUFFIXES:
        return ""
    return stem + suffix


def table_path(name: str, suffix: str = ".dbf") -> Path:
    """Path to one extracted file, under the normalised lower-case naming."""
    return config.INTERIM_DIR / config.HDB_TABLE_SUBDIR / (name.lower() + suffix.lower())
