# heritage-research-mcp

An [MCP](https://modelcontextprotocol.io/) server that lets an AI assistant search and fetch from five public archives through one interface:

| Source | Good for | Key |
|---|---|---|
| [Internet Archive](https://archive.org) | Scanned books, memoirs, regimental histories, government reports, and **full text inside books** | none |
| [Wikimedia Commons](https://commons.wikimedia.org) | Freely licensed photographs, maps and prints, with the licence read from each file | none |
| [DPLA](https://dp.la) | Finding items in US libraries, archives and museums | free |
| [US National Archives Catalog](https://catalog.archives.gov) | Federal records, military and census material, with OCR text for many scans | free |
| [Smithsonian Open Access](https://www.si.edu/openaccess) | Objects, photographs, archives and library items | free |

Every record comes back in the same shape, **with the holder's rights statement reduced to one comparable value**, and every download is cached with a **provenance sidecar** (source, URL, time, SHA-256, rights). It was built for sourcing historical reference material where "where did this come from and may I reuse it?" matters as much as finding it.

## Tools

| Tool | What it does |
|---|---|
| `list_sources`, `usage_report` | What is ready to use, and how many requests have been made |
| `search` | Query every ready source at once; optionally search the text inside Internet Archive books |
| `get_record` | One record, normalised, with its media files and rights |
| `download_media` | Cache one original locally with a provenance sidecar |
| `ia_search`, `ia_fulltext_search`, `ia_get_item`, `ia_read_text`, `ia_grep_text` | Internet Archive: metadata search, search inside books, files, and reading or grepping a book's OCR text |
| `commons_search`, `commons_file_info`, `commons_category_members` | Commons files and categories |
| `dpla_search`, `dpla_get_item` | DPLA |
| `nara_search`, `nara_get_record`, `nara_children`, `nara_extracted_text` | National Archives Catalog |
| `si_search`, `si_get_content`, `si_terms`, `si_stats` | Smithsonian Open Access |

A source that needs a key is skipped, with a message saying where to get one, until the key is set. The Internet Archive and Commons work immediately.

## Install

You need [uv](https://docs.astral.sh/uv/) (or any Python 3.11+ environment).

**Claude Code**

```bash
claude mcp add heritage-research -- uvx --from git+https://github.com/magicianmarty/heritage-research-mcp heritage-research-mcp
```

**Claude Desktop and other clients**

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

**From source**

```bash
git clone https://github.com/magicianmarty/heritage-research-mcp && cd heritage-research-mcp
uv venv && uv pip install -e ".[dev]"
.venv/bin/heritage-research-mcp doctor
```

Works with both the 1.x and 2.x generations of the MCP Python SDK.

## Keys

Three sources need a free key. [docs/KEYS.md](docs/KEYS.md) walks through each. Put a key in an environment variable (`DPLA_API_KEY`, `NARA_API_KEY`, `SMITHSONIAN_API_KEY`) or on one line in `~/.config/heritage-research-mcp/keys/<dpla|nara|smithsonian>` (mode 600). Keys in a file are the better choice: they stay out of client configs and shell history.

Check what is configured, and make one small real request to each source:

```bash
heritage-research-mcp doctor --live
```

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

- Requests are paced per source and retried with backoff; `Retry-After` is honoured. Every request carries a User-Agent naming this project (set `HERITAGE_MCP_CONTACT` to add your own contact).
- **NARA** allows 10,000 queries per key per month and forbids scraping or bulk download through the API. This server counts requests, stops at the limit, returns one page of at most 100 records per call, and includes the attribution NARA requires.
- The Internet Archive full-text endpoint is experimental and may change.

## Security

- Keys are never returned in results and are redacted from errors and logs (HTTP client request logging is filtered, because two providers take the key as a query parameter).
- `download_media` fetches only `https` URLs that resolve to public addresses, refuses credentials and non-standard ports, re-checks every redirect, and enforces a size limit (250 MB by default; `HERITAGE_MCP_MAX_DOWNLOAD_MB`). The URL always comes from a record a source returned, never directly from the model.
- See [SECURITY.md](SECURITY.md) to report a problem.

## Status

Verified against the live services on 6 October 2026: the Internet Archive, Wikimedia Commons, DPLA and Smithsonian Open Access. The NARA adapter follows that provider's published API specification and is tested against fixtures of the documented response shapes; it has not yet been exercised with a real key. If you find a response shape that differs, please open an issue. The `Live smoke` workflow re-checks every configured source weekly.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest            # offline: every request is mocked
.venv/bin/ruff check src tests && .venv/bin/pyright src
```

CI runs the suite on Python 3.11 and 3.13 against both SDK generations. See [docs/DESIGN.md](docs/DESIGN.md) for how it is put together.

## Licence

MIT. Records and files returned by this server remain subject to their holders' terms.
