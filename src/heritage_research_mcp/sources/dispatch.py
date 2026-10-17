"""Richmond Daily Dispatch, 1860 to 1865: the University of Richmond's TEI edition, searched from a local index.

The site publishes the whole run as one TEI download and offers no public search API, so the download is cached
and indexed into SQLite (FTS5). Searching then costs the university's server nothing.

The files carry a DOCTYPE that points at an external Perseus DTD, so they are read with regular expressions and
never through an XML parser that could fetch or expand entities.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import os
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from .. import config
from .. import rights as R
from ..download import download_file
from ..errors import HeritageError, NotFound, NotPrepared
from ..models import Media, Record
from ..util import as_year, clamp, date_range, match_query, truncate

NAME = "dispatch"
SITE = "https://dispatch.richmond.edu"
ARCHIVE_URL = f"{SITE}/static/DispatchXML/DispatchTEI.zip"
HOLDER = "University of Richmond, Digital Scholarship Lab and Library (Civil War Newspaper Project)"
SCHEMA = 3
TEXT_LIMIT = 100_000
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024**3

_FILE = re.compile(r"[A-Za-z0-9_.-]+\.xml")
_ID = re.compile(r"([A-Za-z0-9_-]+)/(\d+)/(\d+)")
_TYPE_FIXES = {
    "aritcle": "article",
    "articel": "article",
    "articles": "article",
    "announcements": "announcement",
    "order": "orders",
    "ordered": "orders",
}

_BLOCK = re.compile(
    r"</?(?:lb|p|head|row|cell|item|list|table|figure|div\d|titlePart|opener|closer|l|lg)\b[^>]*>"
)
_TAG = re.compile(r"<[^>]+>")
_MILESTONE = re.compile(r"<milestone[^>]*/>")
_PAGE = re.compile(r'<milestone unit="page" n="(\d+)"\s*/>')
_DIV3 = re.compile(r"<div3\b([^>]*)>(.*?)</div3>", re.S)
_HEAD = re.compile(r"<head\b[^>]*>(.*?)</head>", re.S)
_ISSUE_DATE = re.compile(r'<biblFull>.*?<date value="(\d{4}-\d{2}-\d{2})"', re.S)
_ISSUE_TITLE = re.compile(r'<title type="245">\s*([^<]*)')
_TYPE_ATTR = re.compile(r'type="([^"]*)"')
_SPACES = re.compile(r"[ \t\r\f\v]+")

_building = False


def index_path() -> Path:
    return config.cache_dir() / NAME / "index.sqlite"


def _text(fragment: str) -> str:
    fragment = _MILESTONE.sub("", fragment)
    fragment = _BLOCK.sub("\n", fragment)
    fragment = html.unescape(_TAG.sub("", fragment))
    fragment = _SPACES.sub(" ", fragment)
    fragment = re.sub(r" ?\n ?", "\n", fragment)
    return re.sub(r"\n{2,}", "\n\n", fragment).strip()


def parse_issue(xml: str, filename: str) -> dict[str, Any] | None:
    """One TEI file as its date, printed title and articles; None if it states no date.

    Articles are the `div3` units in order, leaving out `ad-blank` placeholders (advertisements the project chose
    not to key). Numbered that way they match the ids on dispatch.richmond.edu.
    """
    found = _ISSUE_DATE.search(xml)
    if not found:
        return None
    title = _ISSUE_TITLE.search(xml)
    pages = [(m.start(), int(m.group(1))) for m in _PAGE.finditer(xml)]
    articles: list[dict[str, Any]] = []
    for div in _DIV3.finditer(xml):
        attrs = _TYPE_ATTR.search(div.group(1))
        kind = (attrs.group(1) if attrs else "").strip().lower() or "other"
        if kind == "ad-blank":
            continue
        page = 0
        for position, number in pages:
            if position > div.start():
                break
            page = number
        inner = div.group(2)
        head = _HEAD.search(inner)
        articles.append(
            {
                "no": len(articles) + 1,
                "page": page,
                "type": _TYPE_FIXES.get(kind, kind),
                "title": _text(head.group(1)) if head else "",
                "body": _text(_HEAD.sub("", inner, count=1) if head else inner),
            }
        )
    return {
        "file": filename.split(".")[0],
        "filename": filename,
        "date": found.group(1),
        "title": _text(title.group(1)) if title else "",
        "articles": articles,
    }


def _create(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE issues(file TEXT PRIMARY KEY, filename TEXT, date TEXT NOT NULL, title TEXT);
        CREATE TABLE articles(
            id INTEGER PRIMARY KEY, file TEXT NOT NULL, date TEXT NOT NULL,
            page INTEGER, no INTEGER, type TEXT, title TEXT, body TEXT
        );
        CREATE UNIQUE INDEX articles_key ON articles(file, page, no);
        CREATE INDEX articles_date ON articles(date);
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title, body, content='articles', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
        );
        """
    )


def build_index(archive: Path, dest: Path, sha256: str | None = None) -> dict[str, Any]:
    """Read every issue in the TEI zip into a fresh SQLite index and swap it in atomically."""
    if not zipfile.is_zipfile(archive):
        archive.unlink(missing_ok=True)
        archive.with_name(archive.name + ".provenance.json").unlink(missing_ok=True)
        raise HeritageError(
            f"{NAME}: the download was not a zip file, so it was discarded. Run dispatch_prepare with refresh=true."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    issues = articles = skipped = 0
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        _create(con)
        with zipfile.ZipFile(archive) as bundle:
            infos = [i for i in bundle.infolist() if _FILE.fullmatch(i.filename)]
            if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
                raise HeritageError(
                    f"{NAME}: the archive expands past {MAX_TOTAL_BYTES // 1024**3} GB; refusing it"
                )
            for info in sorted(infos, key=lambda i: i.filename):
                if info.file_size > MAX_ENTRY_BYTES:
                    skipped += 1
                    continue
                parsed = parse_issue(bundle.read(info).decode("utf-8", errors="replace"), info.filename)
                if parsed is None:
                    skipped += 1
                    continue
                issues += 1
                con.execute(
                    "INSERT INTO issues VALUES (?, ?, ?, ?)",
                    (parsed["file"], parsed["filename"], parsed["date"], parsed["title"]),
                )
                con.executemany(
                    "INSERT INTO articles(file, date, page, no, type, title, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (parsed["file"], parsed["date"], a["page"], a["no"], a["type"], a["title"], a["body"])
                        for a in parsed["articles"]
                    ],
                )
                articles += len(parsed["articles"])
        con.execute("INSERT INTO articles_fts(articles_fts) VALUES ('rebuild')")
        built = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("built_at", built),
                ("issues", str(issues)),
                ("articles", str(articles)),
                ("sha256", sha256 or ""),
            ],
        )
        con.execute(f"PRAGMA user_version = {SCHEMA}")
        con.commit()
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, dest)
    return {"issues": issues, "articles": articles, "skipped_files": skipped, "built_at": built}


def index_ready() -> bool:
    path = index_path()
    if not path.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _connect() -> sqlite3.Connection:
    if not index_ready():
        raise NotPrepared(NAME)
    con = sqlite3.connect(f"file:{index_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def status() -> dict[str, Any]:
    if not index_ready():
        return {"ready": False}
    con = _connect()
    try:
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
    finally:
        con.close()
    return {
        "ready": True,
        "issues": int(meta.get("issues", 0)),
        "articles": int(meta.get("articles", 0)),
        "built_at": meta.get("built_at"),
        "path": str(index_path()),
    }


async def prepare(*, refresh: bool = False) -> dict[str, Any]:
    """Download the TEI archive (about 92 MB) and index it. Does nothing if an index exists, unless `refresh`."""
    global _building
    if index_ready() and not refresh:
        return {**status(), "built": False}
    if _building:
        raise HeritageError(f"{NAME}: the index is already being built; try again in a minute")
    _building = True
    try:
        got = await download_file(
            source=NAME,
            record_id="tei-archive",
            url=ARCHIVE_URL,
            title="Richmond Daily Dispatch, TEI edition (all issues)",
            landing_url=f"{SITE}/download",
            mime="application/zip",
            overwrite=refresh,
        )
        stats = await asyncio.to_thread(build_index, Path(got["path"]), index_path(), got.get("sha256"))
    finally:
        _building = False
    return {**status(), **stats, "built": True, "archive": got["path"], "archive_bytes": got["bytes"]}


def _rights(date: str, page: int, issue_title: str) -> Any:
    base = R.from_year(as_year(date)) or R.unknown("Published outside the years this edition covers.")
    cite = (
        f"Daily Dispatch (Richmond, Va.), {date}, p. {page}. Digital edition, University of Richmond, {SITE}"
    )
    note = (
        "The paper itself is out of copyright. The digital edition states '© 2004 by the University of Richmond': "
        "cite it, and ask the university before republishing its text in bulk."
    )
    return base.model_copy(
        update={
            "statement": "© 2004 by the University of Richmond (digital edition)",
            "url": SITE,
            "attribution": cite,
            "note": f"{base.note} {note}" if base.note else note,
        }
    )


def _landing(date: str, page: int, no: int) -> str:
    year, month, day = (int(p) for p in date.split("-"))
    return f"{SITE}/{year}/{month}/{day}/{page}/{no}"


def _record(row: sqlite3.Row, *, snippet: str | None = None, full: bool = False) -> Record:
    body: str = row["body"] or ""
    title = truncate(row["title"], 160) or truncate(body, 80)
    text = f"{row['title']}\n\n{body}" if row["title"] else body
    extra: dict[str, Any] = {
        "issue": row["date"],
        "issue_title": row["issue_title"],
        "page": row["page"],
        "article_no": row["no"],
        "section": row["type"],
        "tei_file": row["filename"],
    }
    if full:
        extra["text"] = text[:TEXT_LIMIT]
        if len(text) > TEXT_LIMIT:
            extra["text_truncated"] = True
    return Record(
        source=NAME,
        id=f"{row['file']}/{row['page']}/{row['no']}",
        title=title,
        date=row["date"],
        description=snippet if snippet is not None else truncate(text, 1200),
        subjects=[row["type"]] if row["type"] else [],
        places=["Richmond, Virginia"],
        type="newspaper article",
        kind="text",
        holder=HOLDER,
        landing_url=_landing(row["date"], row["page"], row["no"]),
        rights=_rights(row["date"], row["page"], row["issue_title"] or ""),
        media=[
            Media(
                kind="data",
                url=f"{SITE}/static/DispatchXML/{row['filename']}",
                mime="application/xml",
                label="TEI source for the whole issue",
            )
        ],
        extra=extra,
    )


def _search_sync(
    query: str, date_from: str | None, date_to: str | None, section: str | None, rows: int, offset: int
) -> dict[str, Any]:
    match = match_query(query)
    low, high = date_range(date_from, date_to)
    where = "articles_fts MATCH ? AND a.date >= ? AND a.date <= ?"
    args: list[Any] = [match, low, high]
    if section:
        where += " AND a.type = ?"
        args.append(section.strip().lower())
    con = _connect()
    try:
        total = con.execute(
            f"SELECT count(*) FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid WHERE {where}",
            args,
        ).fetchone()[0]
        found = con.execute(
            f"""
            SELECT a.*, i.title AS issue_title, i.filename,
                   snippet(articles_fts, -1, '**', '**', '…', 40) AS snip
            FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid
            JOIN issues i ON i.file = a.file
            WHERE {where}
            ORDER BY bm25(articles_fts, 5.0, 1.0) LIMIT ? OFFSET ?
            """,
            [*args, rows, offset],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise HeritageError(f"{NAME}: could not run that search ({exc}); simplify the query") from exc
    finally:
        con.close()
    records = [_record(r, snippet=r["snip"]).to_dict(brief=True) for r in found]
    return {"total": total, "returned": len(records), "records": records}


async def search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    section: str | None = None,
    rows: int = 10,
    offset: int = 0,
    kind: str | None = None,
) -> dict[str, Any]:
    if kind is not None and kind.strip().lower() not in {"", "text"}:
        return {
            "total": 0,
            "returned": 0,
            "records": [],
            "kind_applied": "none: the Dispatch is newspaper text only",
        }
    out = await asyncio.to_thread(
        _search_sync, query, date_from, date_to, section, clamp(rows, 1, 50), max(0, offset)
    )
    if kind:
        out["kind_applied"] = "text"
    return out


def _get_sync(record_id: str) -> Record:
    found = _ID.fullmatch(record_id.strip())
    if not found:
        raise HeritageError("a Dispatch id looks like D_021_040/2/17 (TEI file, page, article number)")
    file, page, no = found.group(1), int(found.group(2)), int(found.group(3))
    con = _connect()
    try:
        row = con.execute(
            "SELECT a.*, i.title AS issue_title, i.filename FROM articles a JOIN issues i ON i.file = a.file "
            "WHERE a.file = ? AND a.page = ? AND a.no = ?",
            (file, page, no),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise NotFound(f"{NAME} has no article {record_id!r}")
    return _record(row, full=True)


async def get_article(record_id: str) -> dict[str, Any]:
    return {"record": (await asyncio.to_thread(_get_sync, record_id)).to_dict()}


def _issue_sync(issue: str, include_adverts: bool) -> dict[str, Any]:
    key = issue.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        column = "i.date"
    elif re.fullmatch(r"[A-Za-z0-9_-]+", key):
        column = "i.file"
    else:
        raise HeritageError("give an issue date (YYYY-MM-DD) or a TEI file name such as D_021_040")
    skip = "" if include_adverts else " AND a.type NOT IN ('advert', 'sales')"
    con = _connect()
    try:
        issues = con.execute(
            f"SELECT file, date, title FROM issues i WHERE {column} = ? ORDER BY file", (key,)
        ).fetchall()
        if not issues:
            raise NotFound(f"{NAME} has no issue {issue!r}; the run covers 1860-11-01 to 1865-12-30")
        out = []
        for i in issues:
            rows = con.execute(
                f"SELECT page, no, type, title, length(body) AS chars FROM articles a WHERE a.file = ?{skip} ORDER BY page, no",
                (i["file"],),
            ).fetchall()
            out.append(
                {
                    "id_prefix": i["file"],
                    "date": i["date"],
                    "issue_title": i["title"],
                    "articles": [
                        {
                            "id": f"{i['file']}/{r['page']}/{r['no']}",
                            "page": r["page"],
                            "section": r["type"],
                            "title": truncate(r["title"], 120),
                            "chars": r["chars"],
                        }
                        for r in rows
                    ],
                }
            )
    finally:
        con.close()
    return {"issues": out}


async def issue(issue: str, *, include_adverts: bool = False) -> dict[str, Any]:
    return await asyncio.to_thread(_issue_sync, issue, include_adverts)
