# heritage-research-mcp

[![CI](https://github.com/magicianmarty/heritage-research-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/magicianmarty/heritage-research-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

**One [MCP](https://modelcontextprotocol.io/) server for historical research across five public archives.** Ask an AI assistant to find a period map, a photograph, a memoir passage or a federal record, and get back records in one shape, each with **the holder's rights statement** and a **provenance trail** for anything downloaded.

| Source | Good for | Key |
|---|---|---|
| [Internet Archive](https://archive.org) | Scanned books, memoirs, regimental histories, official reports, and the **full text inside them** | none |
| [Wikimedia Commons](https://commons.wikimedia.org) | Freely licensed photographs, maps and prints, with the licence read from each file | none |
| [DPLA](https://dp.la) | Finding items held by US libraries, archives and museums | free |
| [US National Archives Catalog](https://catalog.archives.gov) | Federal records, military and census material, with OCR text for many scans | free |
| [Smithsonian Open Access](https://www.si.edu/openaccess) | Objects, photographs, archives and library items; CC0 images with direct high-resolution files | free |

It was built for sourcing reference material where *"where did this come from, and may I reuse it?"* matters as much as finding it.

## What it looks like

Ask for a Civil War map of Fairfax County, Virginia, and `search` with `kind: "map"` returns, among others:

```json
{
  "source": "commons",
  "title": "A Civil War field map of Fairfax County, Virginia with Fort Coccoran. LOC 2014588392",
  "date": "1861",
  "description": "Shows paths and roads that no longer exist and names of some landowners. …",
  "kind": "map",
  "rights": { "reuse": "free", "label": "Public domain", "basis": "holder" },
  "media": [{ "kind": "image", "mime": "image/jpeg", "bytes": 3960781, "width": 4615, "height": 6169 }],
  "landing_url": "https://commons.wikimedia.org/wiki/File:A_Civil_War_field_map_of_Fairfax_County,_Virginia_with_Fort_Coccoran._LOC_2014588392.jpg"
}
```

And `ia_fulltext_search` finds a name inside a scanned book, with the page and a snippet (matches in `**bold**`):

> *Mosby's War Reminiscences* (1887): "…Capture of a Federal **Picket** at **Herndon Station**. The Dash and Excitement of a Cavalry Skirmish…"

`ia_grep_text` then finds every occurrence in that book and `ia_read_text` reads around one. `download_media` caches an original (a 7 MB high-resolution CC0 photograph, say) and writes a `.provenance.json` beside it.

## Searching by type

`search` takes `kind`: **`text`**, **`image`**, **`map`**, **`audio`** or **`video`**. Each archive is asked in its own vocabulary, every result says how (`kind_applied`), and every record carries a best-effort `kind` of its own.

| kind | Internet Archive | Commons | DPLA | Smithsonian | NARA |
|---|---|---|---|---|---|
| `text` | `mediatype:texts` | `filetype:office` (PDF, DjVu) | `type=text` | scanned books, full-text documents, books, manuscripts | Textual Records |
| `image` | `mediatype:image` | `filetype:bitmap` | `type=image` | `online_media_type:"Images"` | Photographs and other Graphic Materials |
| `map` | the "maps" subject and the map collections | `map` in the file title | images with the subject "Maps" | `object_type:"Maps"` | Maps and Charts |
| `audio` | `mediatype:audio` | `filetype:audio` | `type=sound` | `online_media_type:"Sound recordings"` | Sound Recordings |
| `video` | `mediatype:movies` | `filetype:video` | `type=moving image` | `online_media_type:"Video recordings"` | Moving Images |

**Do maps work?** Yes, with honest limits. Commons, DPLA and the Smithsonian have solid map cataloguing and return real maps (the example above came from this filter). The Internet Archive has no map type at all, so `map` there is a subject-and-collection match and is noisy. Because archives catalogue differently, treat `kind` as a strong hint rather than a guarantee, and run a query both with and without it when something seems missing. Multi-word queries need every word to match at all five archives, so fewer words find more.

**Text versus images.** `kind` selects whole records. To search *inside* documents, use `ia_fulltext_search` (the OCR text of Internet Archive books), and `nara_search` with `include_extracted_text` or `nara_extracted_text` for NARA scans.

## The National Archives

NARA holds the federal paper trail: Official Records, Adjutant General and Quartermaster files, Brady's photographs, War Department maps, pension files. Beyond a plain `nara_search`:

- The first page of results carries **`facets`**: how many hits fall in each record group and type of material, so a broad query ("Fairfax") can be narrowed with `record_group=77` or `type_of_materials=map` instead of paging.
- Each record names its **record group and series**, keeps photographer or mapmaker credits, keeps "ca." on estimated dates, and puts places apart from subjects. `nara_get_record` adds a ready-made archival **`citation`**, the use and access restrictions in NARA's own words, related links (Fold3, microfilm publication numbers) and the creating office.
- `nara_extracted_text` returns each scan's machine **OCR** and any **partner or volunteer transcriptions** (FamilySearch's, on pension files), labelled, with AI-generated ones flagged. Use them to find names, then read the scan.
- Restrictions are reported honestly: "Unrestricted" is `free`; "Restricted - Fully/Partly" is `restricted`; "Restricted - Possibly" and "Undetermined" are `unknown`, because NARA is flagging a *possible* copyright or donor issue, not stating one.
- Date filters need both bounds at NARA's end; this server fills the open end, so `start_date` alone works. Undated records never match a date filter, and much of the catalogue is undated.

## Tools

| Tool | What it does |
|---|---|
| `list_sources`, `usage_report` | What is ready to use, and how many requests have been made |
| `search` | Query every ready archive at once, optionally by `kind`, date range, and including full text inside books |
| `get_record` | One record, normalised, with its media files and rights |
| `download_media` | Cache one original locally with a provenance sidecar |
| `ia_search`, `ia_fulltext_search`, `ia_get_item`, `ia_read_text`, `ia_grep_text` | Internet Archive: metadata search, search inside books, files, and reading or grepping OCR text |
| `commons_search`, `commons_file_info`, `commons_category_members` | Commons files and categories |
| `dpla_search`, `dpla_get_item` | DPLA |
| `nara_search`, `nara_get_record`, `nara_children`, `nara_extracted_text` | National Archives Catalog |
| `si_search`, `si_get_content`, `si_terms`, `si_stats` | Smithsonian Open Access |

A source that needs a key is skipped, with a message saying where to get one, until the key is set. The Internet Archive and Commons work immediately.

Search results are compact (a short description, the first two files, no thumbnails) so that many can be read cheaply; `get_record` and the `*_get_*` and `*_file_info` tools return the full record.

## Install

You need [uv](https://docs.astral.sh/uv/) (or any Python 3.11+ environment).

**Claude Code**

```bash
claude mcp add heritage-research -- uvx --from git+https://github.com/magicianmarty/heritage-research-mcp heritage-research-mcp
```

**Claude Desktop and other MCP clients**

```json
{
  "mcpServers": {
    "heritage-research": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/magicianmarty/heritage-research-mcp", "heritage-research-mcp"]
    }
  }
}
```

Pin a release for reproducible installs: `git+https://github.com/magicianmarty/heritage-research-mcp@v0.1.0`.

**Hosted agent platforms.** Any platform that runs stdio MCP servers can run it. Use the same `uvx` command, pass the keys as environment variables from the platform's secret store, and set `HERITAGE_MCP_DISABLE_DOWNLOADS=1` (a hosted agent cannot read files the server downloads, so `download_media` is switched off and the text-returning tools do the work). To get files onto a machine you control, run the command-line downloader there (below) and let the agent call it through whatever shell access the platform gives it.

**Download from a shell.** `heritage-research-mcp download <source> <record_id> [--kind image|pdf|text|audio|video|archive] [--media-index N] [--max-mb N] [--overwrite]` runs the same checked downloader as `download_media` (https only, public addresses, size limit, provenance sidecar) and prints the result as JSON, including the file `path`. It exits 1 with `{"error": ...}` when it fails. `HERITAGE_MCP_DISABLE_DOWNLOADS` only removes the MCP tool; the command always works.

**From source**

```bash
git clone https://github.com/magicianmarty/heritage-research-mcp && cd heritage-research-mcp
uv venv && uv pip install -e ".[dev]"
.venv/bin/heritage-research-mcp doctor
```

Works with both the 1.x and 2.x generations of the MCP Python SDK.

## Keys

Three sources need a free key. [docs/KEYS.md](docs/KEYS.md) walks through each. Put a key in an environment variable or on one line in `~/.config/heritage-research-mcp/keys/<dpla|nara|smithsonian>` (mode 600). Files are the better choice: they stay out of client configs and shell history.

```bash
heritage-research-mcp doctor --live     # shows what is configured, then makes one small real request to each source
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DPLA_API_KEY`, `NARA_API_KEY`, `SMITHSONIAN_API_KEY` | none | API keys (or key files, above) |
| `HERITAGE_MCP_CONTACT` | none | Added to the User-Agent, so providers can reach you |
| `HERITAGE_MCP_CACHE_DIR` | `~/.cache/heritage-research-mcp` | Where `download_media` writes |
| `HERITAGE_MCP_MAX_DOWNLOAD_MB` | `250` | Refuse larger downloads |
| `HERITAGE_MCP_DISABLE_DOWNLOADS` | off | Remove `download_media` (hosted use) |
| `NARA_MONTHLY_LIMIT` | `10000` | Stop before NARA's monthly quota |
| `NARA_API_VERSION` | `v2` | `v3` to use NARA's newer search |

## Rights

Archives state rights in many vocabularies: Creative Commons URLs, rightsstatements.org URIs, free text, or nothing. Each record's `rights` reduces that to a `reuse` value:

| `reuse` | Meaning |
|---|---|
| `free` | Public domain, CC0, or a no-copyright statement |
| `attribution` | Free if credited (`rights.attribution` has the text) |
| `share_alike` | Reuse must carry the same licence |
| `non_commercial`, `no_derivatives`, `restricted` | Conditions that limit reuse |
| `unknown` | **The holder stated nothing. This is not permission.** |

`rights.basis` says where the value came from: `holder`, or `date-heuristic` for one narrow case (a US text first published 96 or more years ago). This is a filtering aid, **not legal advice**. `download_media` adds a `rights_warning` when an item is not marked `free`.

## Being a good citizen

- Requests are paced per source and retried with backoff; `Retry-After` is honoured. Every request carries a User-Agent naming this project.
- **NARA** allows 10,000 queries per key per month and forbids scraping or bulk download through the API. This server counts requests, stops at the limit, returns one page of at most 100 records per call, and includes the attribution NARA requires.
- The Internet Archive full-text endpoint is experimental and may change.

## Security

- Keys are never returned in results and are redacted from errors and logs (HTTP request logging is filtered, because two providers take the key as a query parameter).
- `download_media` fetches only `https` URLs that resolve to public addresses, refuses credentials and non-standard ports, re-checks every redirect, and enforces a size limit. The URL always comes from a record a source returned, never directly from the model.
- See [SECURITY.md](SECURITY.md) to report a problem.

## Status

Verified against the live services on 6 and 7 October 2026: the Internet Archive, Wikimedia Commons, DPLA, Smithsonian Open Access and the National Archives Catalog. The NARA adapter was first written from the published specification and then rebuilt against real responses, which showed that its creators, dates, restrictions and file sizes all needed different handling; its test fixtures are trimmed live captures. If you find a response shape that differs, please open an issue. The `Live smoke` workflow re-checks every configured source weekly.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest            # offline: every request is mocked
.venv/bin/ruff check src tests && .venv/bin/pyright src
```

CI runs the suite on Python 3.11 and 3.13 against both SDK generations. See [docs/DESIGN.md](docs/DESIGN.md) for how it is put together.

## Licence

MIT. Records and files returned by this server remain subject to their holders' terms.
