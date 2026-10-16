"""Chronicling America bulk OCR: stream the Library's per-batch archives and keep a range of years in a local index.

The Library publishes the OCR of every page as one .tar.bz2 per digitisation batch, the route it recommends for
text analysis at scale. A batch holds many titles over many years, so an archive is read end to end, only the
plain-text files dated inside the requested range are kept, and the (much larger) ALTO XML is discarded as it
streams past. Every archive is checked against the SHA-256 the Library publishes, and a job can be repeated: batches
already indexed for a range are skipped.
"""

from __future__ import annotations

import asyncio
import bz2
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sqlite3
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .. import config
from ..errors import Blocked, HeritageError, NotFound, NotPrepared
from ..http import http
from ..models import Record
from ..rights import newspaper_page
from ..util import as_year, clamp, date_range, match_query, truncate

NAME = "chronam_local"
OCR_BASE = "https://chroniclingamerica.loc.gov/data/ocr"
TITLES_URL = (
    "https://raw.githubusercontent.com/LibraryOfCongress/data-exploration/master/"
    "loc.gov%20JSON%20API/Chronicling_America_Title_Essay_Datasets/chronam.csv"
)
SCHEMA = 2
PROBE_BYTES = (128 * 1024, 512 * 1024, 2 * 1024 * 1024)
DEFAULT_MAX_GB = 60.0
MAX_PAGE_BYTES = 4 * 1024 * 1024
MANIFEST_MAX_AGE = 7 * 86400
SUPPLEMENT_PER_RUN = 40
LISTING_SOURCE = "chronam_bulk"

_BATCH = re.compile(r"[A-Za-z0-9_]+")
_LCCN = re.compile(r"[A-Za-z0-9]+")
_ID = re.compile(r"([A-Za-z0-9]+)/(\d{4}-\d{2}-\d{2})/ed-(\d+)/(\d+)")
_MEMBER = re.compile(
    r"(?:\./)?(?P<lccn>[A-Za-z0-9]+)/(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/ed-(?P<ed>\d+)/seq-(?P<seq>\d+)/ocr\.txt"
)
_FIRST_MEMBER = re.compile(r"(?:\./)?([A-Za-z0-9]+)/(\d{4})/(\d{2})/(\d{2})/")
_HREF = re.compile(r'href="([^"?#]+)"')

_job: dict[str, Any] = {"state": "idle"}
_job_lock = threading.Lock()


def _dir() -> Path:
    return config.cache_dir() / "chronam"


def index_path() -> Path:
    return _dir() / "index.sqlite"


def parse_manifest(text: str) -> dict[str, dict[str, Any]]:
    """The Library's flat YAML list of batches, read without a YAML library."""
    batches: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    in_lccns = False
    for line in text.splitlines():
        if line.startswith("- "):
            current = {"lccns": []}
            in_lccns = False
            line = "  " + line[2:]
        if current is None or not line.strip():
            continue
        item = line.strip()
        if in_lccns and item.startswith("- "):
            current["lccns"].append(item[2:].strip().strip("'\""))
            continue
        key, _, value = item.partition(":")
        in_lccns = key == "lccns" and not value.strip()
        if not in_lccns:
            current[key] = value.strip().strip("'\"")
        if key == "batch":
            batches[current["batch"]] = current
    out = {}
    for name, b in batches.items():
        if not _BATCH.fullmatch(name) or not b.get("size", "").isdigit() or not b.get("sha256"):
            continue
        out[name] = {
            "batch": name,
            "lccns": [x for x in b["lccns"] if _LCCN.fullmatch(x)],
            "size": int(b["size"]),
            "sha256": b["sha256"],
            "pages": int(b.get("page_count") or 0),
        }
    return out


_SPAN_END = re.compile(r",\s*[\d?]{4}\s*-\s*([\d?]{4})\s*$")


def _title_end(title: str) -> str | None:
    """The last year a title's own span says it ran ("Richmond, Va., 1849-1850"), with unknown digits taken high."""
    found = _SPAN_END.search(title.strip())
    return found.group(1).replace("?", "9") if found else None


def parse_titles(text: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        lccn = (row.get("lccn") or "").strip("/").split("/")[-1]
        if not _LCCN.fullmatch(lccn):
            continue
        title = (row.get("title") or "").strip()
        last = (row.get("last_issue") or "").strip() or None
        end = _title_end(title)
        out[lccn] = {
            "lccn": lccn,
            "title": title,
            "state": (row.get("state") or "").strip(),
            "first": (row.get("first_issue") or "").strip() or None,
            "last": last or (f"{end}-12-31" if end else None),
        }
    return out


def _fresh(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < MANIFEST_MAX_AGE
    except OSError:
        return False


def _load_manifest_cache() -> dict[str, Any] | None:
    path = _dir() / "manifest.json"
    if not _fresh(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("batches"), dict) else None


def _save_manifest_cache(data: dict[str, Any]) -> None:
    path = _dir() / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


async def _read_backup_manifest() -> dict[str, dict[str, Any]]:
    listing = await http.get_text(LISTING_SOURCE, f"{OCR_BASE}/backups/")
    names = [h for h in _HREF.findall(listing) if h.endswith(".yaml")]
    for href in reversed(names[-8:]):
        try:
            text = await http.get_text(LISTING_SOURCE, f"{OCR_BASE}/backups/{href.replace('%3A', ':')}")
        except Blocked:
            raise
        except HeritageError:
            continue
        batches = parse_manifest(text)
        if batches:
            return batches
    raise HeritageError("could not read the Library's list of OCR batches (backups/ manifests)")


async def manifest(supplement: int = SUPPLEMENT_PER_RUN) -> dict[str, dict[str, Any]]:
    """Batches the Library lists, saved as they are learned so a rate limit part-way loses nothing.

    The backup manifest can lag the directory, so batches present in the directory but not in it are looked up one
    by one (a few per call). `unchecked_batches()` says how many remain.
    """
    manifest_cache_state["rate_limited"] = 0
    cache = _load_manifest_cache()
    if cache is None:
        cache = {"batches": await _read_backup_manifest(), "missing": None}
        _save_manifest_cache(cache)
    if cache.get("missing") is None:
        index = await http.get_text(LISTING_SOURCE, f"{OCR_BASE}/")
        listed = {h.removesuffix(".tar.bz2") for h in _HREF.findall(index) if h.endswith(".tar.bz2")}
        cache["missing"] = sorted(n for n in listed - set(cache["batches"]) if _BATCH.fullmatch(n))
        _save_manifest_cache(cache)
    for name in list(cache["missing"])[:supplement]:
        try:
            meta = await http.get_json(LISTING_SOURCE, f"{OCR_BASE}/metadata/{name}.tar.bz2.json")
        except Blocked:
            manifest_cache_state["rate_limited"] = 1
            break
        except HeritageError:
            meta = None
        cache["missing"].remove(name)
        if isinstance(meta, dict) and str(meta.get("size", "")).isdigit() and meta.get("sha256"):
            cache["batches"][name] = {
                "batch": name,
                "lccns": [x for x in meta.get("lccns", []) if _LCCN.fullmatch(str(x))],
                "size": int(meta["size"]),
                "sha256": str(meta["sha256"]),
                "pages": int(meta.get("page_count") or 0),
            }
        _save_manifest_cache(cache)
    manifest_cache_state["unchecked"] = len(cache["missing"])
    manifest_cache_state.setdefault("rate_limited", 0)
    return cache["batches"]


manifest_cache_state: dict[str, int] = {"unchecked": 0, "rate_limited": 0}


async def titles() -> dict[str, dict[str, Any]]:
    cache = _dir() / "titles.csv"
    if _fresh(cache):
        return parse_titles(cache.read_text(encoding="utf-8"))
    text = await http.get_text("github", TITLES_URL)
    found = parse_titles(text)
    if not found:
        raise HeritageError("the Chronicling America title directory could not be read")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return found


def _overlaps(title: dict[str, Any], low: str, high: str) -> bool:
    first, last = title["first"], title["last"]
    return bool(first) and first <= high and (not last or last >= low)


def choose(
    manifest: dict[str, dict[str, Any]],
    titles: dict[str, dict[str, Any]],
    low: str,
    high: str,
    state: str | None = None,
    lccns: list[str] | None = None,
) -> dict[str, Any]:
    wanted_state = state.strip().lower() if state else None
    wanted_lccn = {x.strip() for x in lccns or []}
    picked = {
        k: t
        for k, t in titles.items()
        if _overlaps(t, low, high)
        and (not wanted_state or t["state"].lower() == wanted_state)
        and (not wanted_lccn or k in wanted_lccn)
    }
    batches = sorted(
        (b for b in manifest.values() if set(b["lccns"]) & set(picked)), key=lambda b: (b["size"], b["batch"])
    )
    return {"titles": picked, "batches": batches}


def _starts_path() -> Path:
    return _dir() / "starts.json"


def _load_starts() -> dict[str, list[str]]:
    try:
        data = json.loads(_starts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def probe_start(batch: dict[str, Any]) -> tuple[str, str] | None:
    """The paper and date of an archive's first page, from a small range request. None if it cannot be read."""
    starts = _load_starts()
    key = f"{batch['batch']}:{batch['sha256']}"
    if key in starts:
        return starts[key][0], starts[key][1]
    url = f"{OCR_BASE}/{batch['batch']}.tar.bz2"
    for size in PROBE_BYTES:
        try:
            resp = await http.request(
                LISTING_SOURCE,
                url,
                headers={"Range": f"bytes=0-{size - 1}", "Accept": "*/*"},
                max_bytes=size + 4096,
            )
        except Blocked:
            raise
        except HeritageError:
            return None
        if resp.status != 206:
            return None
        try:
            head = bz2.BZ2Decompressor().decompress(resp.content)[:512]
        except (OSError, ValueError):
            return None
        if len(head) < 512:
            continue
        found = _FIRST_MEMBER.search(head[:100].split(b"\0")[0].decode("utf-8", errors="replace"))
        if not found:
            return None
        start = (found.group(1), f"{found.group(2)}-{found.group(3)}-{found.group(4)}")
        starts[key] = list(start)
        _starts_path().parent.mkdir(parents=True, exist_ok=True)
        _starts_path().write_text(json.dumps(starts), encoding="utf-8")
        return start
    return None


async def select(
    low: str, high: str, state: str | None = None, lccns: list[str] | None = None, probe: bool = True
) -> dict[str, Any]:
    """The batches to read for a range, dropping single-paper archives that start after it ends."""
    chosen = choose(await manifest(), await titles(), low, high, state, lccns)
    chosen["skipped_by_start_date"] = []
    chosen["probe_paused"] = False
    if not probe:
        return chosen
    kept = []
    for batch in chosen["batches"]:
        start = None
        if len(batch["lccns"]) == 1 and not chosen["probe_paused"]:
            try:
                start = await probe_start(batch)
            except Blocked:
                chosen["probe_paused"] = True
        if start and start[1] > high:
            chosen["skipped_by_start_date"].append(batch["batch"])
        else:
            kept.append(batch)
    chosen["batches"] = kept
    return chosen


async def plan(
    date_from: str | None, date_to: str | None, *, state: str | None = None, lccns: list[str] | None = None
) -> dict[str, Any]:
    if date_from is None or date_to is None:
        raise HeritageError("give date_from and date_to (for example 1863 and 1863)")
    low, high = date_range(date_from, date_to)
    chosen = await select(low, high, state, lccns)
    done = _done(low, high)
    todo = [b for b in chosen["batches"] if b["batch"] not in done]
    gb = sum(b["size"] for b in todo) / 1e9
    return {
        "range": [low, high],
        "titles": len(chosen["titles"]),
        "title_list": [
            {"lccn": t["lccn"], "title": t["title"], "state": t["state"]}
            for t in sorted(chosen["titles"].values(), key=lambda t: (t["state"], t["title"]))[:60]
        ],
        "batches": len(chosen["batches"]),
        "already_indexed": len(chosen["batches"]) - len(todo),
        "to_download": len(todo),
        "download_gb": round(gb, 1),
        "archives_skipped_because_they_start_after_the_range": len(chosen["skipped_by_start_date"]),
        "start_dates_paused_by_rate_limit": chosen["probe_paused"],
        "newer_batches_not_yet_checked": manifest_cache_state["unchecked"],
        "paused_by_rate_limit": bool(manifest_cache_state["rate_limited"]),
        "largest_batches": [{"batch": b["batch"], "mb": round(b["size"] / 1e6)} for b in todo[-3:]],
        "note": (
            "If newer_batches_not_yet_checked is above 0, run the plan again to check more of the Library's newer "
            "batches (a few are checked per call). Archives hold whole batches, so every year in a batch is downloaded and only the requested range is "
            "kept. Titles come from a Library title directory that may be missing papers added since it was "
            "published."
        ),
    }


def _connect(write: bool = False) -> sqlite3.Connection:
    path = index_path()
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, isolation_level=None)
        if con.execute("PRAGMA user_version").fetchone()[0] not in (0, SCHEMA):
            con.close()
            path.unlink()
            con = sqlite3.connect(path, isolation_level=None)
        if con.execute("PRAGMA user_version").fetchone()[0] == 0:
            con.executescript(
                """
                CREATE TABLE pages(
                    id INTEGER PRIMARY KEY, lccn TEXT NOT NULL, date TEXT NOT NULL, ed INTEGER, seq INTEGER,
                    batch TEXT, text TEXT, UNIQUE(lccn, date, ed, seq)
                );
                CREATE INDEX pages_date ON pages(date);
                CREATE TABLE titles(lccn TEXT PRIMARY KEY, title TEXT, state TEXT);
                CREATE TABLE done(batch TEXT, low TEXT, high TEXT, pages INTEGER, finished TEXT,
                                  verified INTEGER, PRIMARY KEY(batch, low, high));
                CREATE VIRTUAL TABLE pages_fts USING fts5(
                    text, content='pages', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
                );
                """
            )
            con.execute(f"PRAGMA user_version = {SCHEMA}")
        return con
    if not path.is_file():
        raise NotPrepared(NAME)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def ready() -> bool:
    try:
        con = _connect()
    except (NotPrepared, sqlite3.Error):
        return False
    try:
        return con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA and bool(
            con.execute("SELECT 1 FROM pages LIMIT 1").fetchone()
        )
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _done(low: str, high: str) -> set[str]:
    if not index_path().is_file():
        return set()
    con = sqlite3.connect(f"file:{index_path()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT batch FROM done WHERE low <= ? AND high >= ?", (low, high)).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {r[0] for r in rows}


class _Counting(io.RawIOBase):
    """A readable view of an HTTP body that hashes and counts what passes through."""

    def __init__(self, chunks: Any) -> None:
        self._chunks = iter(chunks)
        self._view = memoryview(b"")
        self.sha = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not len(self._view):
            try:
                self._view = memoryview(next(self._chunks))
            except StopIteration:
                return 0
        n = min(len(buffer), len(self._view))
        buffer[:n] = self._view[:n]
        self.sha.update(self._view[:n])
        self._view = self._view[n:]
        self.count += n
        return n


def index_stream(
    con: sqlite3.Connection,
    batch: dict[str, Any],
    stream: Any,
    low: str,
    high: str,
    lccns: set[str] | None,
    verify: bool = False,
) -> int:
    """Read one archive from `stream` into the index inside a transaction; returns the pages kept.

    Members are sorted by paper, then date. Once every wanted paper in the archive has produced a page dated after
    `high`, the rest cannot matter and reading stops; the archive is then recorded as not checksum-verified.
    Reading stops early only while each paper's dates have stayed in order, and never with `verify`.
    """
    reader = _Counting(stream)
    kept = 0
    wanted = (lccns & set(batch["lccns"])) if lccns else set(batch["lccns"])
    last_seen: dict[str, str] = {}
    passed: set[str] = set()
    ordered = True
    stopped = False
    con.execute("BEGIN")
    try:
        first = con.execute("SELECT coalesce(max(id), 0) FROM pages").fetchone()[0]
        with tarfile.open(fileobj=reader, mode="r|bz2") as archive:
            for member in archive:
                found = _MEMBER.fullmatch(member.name) if member.isfile() else None
                if not found or member.size > MAX_PAGE_BYTES:
                    continue
                date = f"{found['y']}-{found['m']}-{found['d']}"
                paper = found["lccn"]
                if date < last_seen.get(paper, ""):
                    ordered = False
                last_seen[paper] = date
                if date > high:
                    passed.add(paper)
                    if not verify and ordered and wanted and wanted <= passed:
                        stopped = True
                        break
                if not low <= date <= high or (lccns and paper not in lccns):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="replace")
                cursor = con.execute(
                    "INSERT OR IGNORE INTO pages(lccn, date, ed, seq, batch, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (found["lccn"], date, int(found["ed"]), int(found["seq"]), batch["batch"], text),
                )
                kept += cursor.rowcount
        if not stopped:
            while reader.read(1 << 20):
                pass
            if reader.sha.hexdigest() != batch["sha256"] or reader.count != batch["size"]:
                raise HeritageError(
                    f"{batch['batch']}: the download does not match the Library's checksum; discarded"
                )
        con.execute("INSERT INTO pages_fts(rowid, text) SELECT id, text FROM pages WHERE id > ?", (first,))
        con.execute(
            "INSERT OR REPLACE INTO done VALUES (?, ?, ?, ?, ?, ?)",
            (
                batch["batch"],
                low,
                high,
                kept,
                dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                0 if stopped else 1,
            ),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    return kept


def _download(batch: dict[str, Any]) -> Any:
    url = f"{OCR_BASE}/{batch['batch']}.tar.bz2"
    headers = {"User-Agent": config.user_agent(), "Accept": "*/*"}
    with httpx.Client(timeout=httpx.Timeout(60.0, read=180.0), follow_redirects=False) as client:
        with client.stream("GET", url, headers=headers) as response:
            if response.headers.get("cf-mitigated", "").lower() == "challenge" or response.status_code == 429:
                raise Blocked(
                    LISTING_SOURCE, response.status_code, "the Library is limiting this client; resume later"
                )
            if response.status_code != 200:
                raise HeritageError(f"{batch['batch']}: download failed (HTTP {response.status_code})")
            yield from response.iter_raw(1 << 16)


def run_job(
    chosen: dict[str, Any], low: str, high: str, restrict: bool, progress: Any = None, verify: bool = False
) -> dict[str, Any]:
    """Index every chosen batch not already done for this range. Safe to rerun after an interruption."""
    done = _done(low, high)
    todo = [b for b in chosen["batches"] if b["batch"] not in done]
    lccns = set(chosen["titles"]) if restrict else None
    con = _connect(write=True)
    pages = 0
    try:
        con.executemany(
            "INSERT OR IGNORE INTO titles VALUES (?, ?, ?)",
            [(t["lccn"], t["title"], t["state"]) for t in chosen["titles"].values()],
        )
        for number, batch in enumerate(todo, 1):
            if progress:
                progress(number - 1, len(todo), batch["batch"], pages)
            pages += index_stream(con, batch, _download(batch), low, high, lccns, verify)
        if progress:
            progress(len(todo), len(todo), None, pages)
    finally:
        con.close()
    return {"batches_indexed": len(todo), "pages_added": pages}


def job_status() -> dict[str, Any]:
    with _job_lock:
        out = dict(_job)
    out["index"] = summary()
    return out


def start_job(
    chosen: dict[str, Any],
    low: str,
    high: str,
    restrict: bool,
    max_gb: float = DEFAULT_MAX_GB,
    verify: bool = False,
) -> dict[str, Any]:
    done = _done(low, high)
    todo = [b for b in chosen["batches"] if b["batch"] not in done]
    gb = sum(b["size"] for b in todo) / 1e9
    if gb > max_gb:
        raise HeritageError(
            f"this would download {gb:.0f} GB, over the {max_gb:.0f} GB limit. Narrow it with a state or LCCN, or "
            "pass a larger max_gb if you mean it."
        )
    with _job_lock:
        if _job.get("state") == "running":
            raise HeritageError("an indexing job is already running; check chronam_bulk_status")
        _job.clear()
        _job.update(
            {
                "state": "running",
                "range": [low, high],
                "total": len(todo),
                "done": 0,
                "pages": 0,
                "current": None,
                "download_gb": round(gb, 1),
            }
        )

    def progress(finished: int, total: int, current: str | None, pages: int) -> None:
        with _job_lock:
            _job.update({"done": finished, "total": total, "current": current, "pages": pages})

    def work() -> None:
        try:
            result = run_job(chosen, low, high, restrict, progress, verify)
            with _job_lock:
                _job.update({"state": "finished", **result, "current": None})
        except Blocked as exc:
            with _job_lock:
                _job.update({"state": "paused", "error": str(exc), "current": None})
        except BaseException as exc:
            with _job_lock:
                _job.update({"state": "failed", "error": f"{type(exc).__name__}: {exc}", "current": None})

    threading.Thread(target=work, name="chronam-bulk-index", daemon=True).start()
    return job_status()


async def start(
    date_from: str,
    date_to: str,
    *,
    state: str | None = None,
    lccns: list[str] | None = None,
    max_gb: float = DEFAULT_MAX_GB,
    verify: bool = False,
) -> dict[str, Any]:
    low, high = date_range(date_from, date_to)
    chosen = await select(low, high, state, lccns)
    if not chosen["batches"]:
        raise NotFound("no Chronicling America archives hold papers for that range and filter")
    return start_job(chosen, low, high, bool(state or lccns), max_gb, verify)


def summary() -> dict[str, Any]:
    if not index_path().is_file():
        return {"ready": False}
    try:
        con = _connect()
    except sqlite3.Error:
        return {"ready": False}
    try:
        total, first, last = con.execute("SELECT count(*), min(date), max(date) FROM pages").fetchone()
        by_title = con.execute(
            "SELECT p.lccn, coalesce(t.title, p.lccn) AS title, coalesce(t.state, '') AS state, count(*) AS pages, "
            "min(p.date) AS first, max(p.date) AS last FROM pages p LEFT JOIN titles t ON t.lccn = p.lccn "
            "GROUP BY p.lccn ORDER BY pages DESC LIMIT 40"
        ).fetchall()
        batches = con.execute("SELECT count(*) FROM done").fetchone()[0]
        unverified = con.execute("SELECT count(*) FROM done WHERE verified = 0").fetchone()[0]
    except sqlite3.Error:
        return {"ready": False}
    finally:
        con.close()
    return {
        "ready": bool(total),
        "pages": total,
        "first": first,
        "last": last,
        "batches_indexed": batches,
        "batches_read_early_without_checksum": unverified,
        "titles": [dict(r) for r in by_title],
    }


def _record(row: sqlite3.Row, *, snippet: str | None = None, full: bool = False) -> Record:
    date, lccn, ed, seq = row["date"], row["lccn"], row["ed"], row["seq"]
    paper = row["title"] or lccn
    landing = f"https://www.loc.gov/resource/{lccn}/{date}/ed-{ed}/?sp={seq}"
    title = f"{truncate(paper, 80)}, {date}, p. {seq}"
    text: str = row["text"] if "text" in row.keys() else ""
    extra: dict[str, Any] = {
        "newspaper": paper,
        "lccn": lccn,
        "edition": ed,
        "page": seq,
        "batch": row["batch"],
    }
    if full:
        extra["text"] = text
    return Record(
        source=NAME,
        id=f"{lccn}/{date}/ed-{ed}/{seq}",
        title=title,
        date=date,
        description=snippet if snippet is not None else truncate(text, 400),
        places=[row["state"]] if row["state"] else [],
        type="newspaper page",
        kind="text",
        holder="Library of Congress, Chronicling America",
        landing_url=landing,
        rights=newspaper_page(as_year(date), False, title, landing),
        extra=extra,
    )


def _search_sync(
    query: str,
    date_from: str | None,
    date_to: str | None,
    state: str | None,
    lccn: str | None,
    rows: int,
    offset: int,
) -> dict[str, Any]:
    match = match_query(query)
    low, high = date_range(date_from, date_to)
    where = "pages_fts MATCH ? AND p.date >= ? AND p.date <= ?"
    args: list[Any] = [match, low, high]
    if state:
        where += " AND lower(t.state) = ?"
        args.append(state.strip().lower())
    if lccn:
        where += " AND p.lccn = ?"
        args.append(lccn.strip())
    join = "pages_fts JOIN pages p ON p.id = pages_fts.rowid LEFT JOIN titles t ON t.lccn = p.lccn"
    con = _connect()
    try:
        total = con.execute(f"SELECT count(*) FROM {join} WHERE {where}", args).fetchone()[0]
        found = con.execute(
            f"SELECT p.id, p.lccn, p.date, p.ed, p.seq, p.batch, t.title AS title, t.state AS state, "
            f"snippet(pages_fts, 0, '**', '**', '…', 40) AS snip FROM {join} WHERE {where} "
            f"ORDER BY bm25(pages_fts) LIMIT ? OFFSET ?",
            [*args, rows, offset],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise HeritageError(f"{NAME}: could not run that search ({exc}); simplify the query") from exc
    finally:
        con.close()
    records = [_record(r, snippet=r["snip"]).to_dict(brief=True) for r in found]
    return {
        "total": total,
        "returned": len(records),
        "records": records,
        "attribution": "Chronicling America, Library of Congress. OCR text is raw and uncorrected.",
    }


async def search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    state: str | None = None,
    lccn: str | None = None,
    rows: int = 10,
    offset: int = 0,
    kind: str | None = None,
) -> dict[str, Any]:
    if kind is not None and kind.strip().lower() not in {"", "text"}:
        return {"total": 0, "records": [], "kind_applied": "none: Chronicling America is newspaper text only"}
    out = await asyncio.to_thread(
        _search_sync, query, date_from, date_to, state, lccn, clamp(rows, 1, 50), max(0, offset)
    )
    if kind:
        out["kind_applied"] = "text"
    return out


def local_page(record_id: str) -> dict[str, Any] | None:
    """One indexed page with its full OCR text, or None if it is not in the local index."""
    found = _ID.fullmatch(record_id.strip())
    if not found or not index_path().is_file():
        return None
    lccn, date, ed, seq = found.group(1), found.group(2), int(found.group(3)), int(found.group(4))
    try:
        con = _connect()
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT p.*, t.title AS title, t.state AS state FROM pages p LEFT JOIN titles t ON t.lccn = p.lccn "
            "WHERE p.lccn = ? AND p.date = ? AND p.ed = ? AND p.seq = ?",
            (lccn, date, ed, seq),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return _record(row, full=True).to_dict() if row else None
