# Design

## Shape

```
src/heritage_research_mcp/
  server.py          the MCP server and its instructions; `doctor` subcommand
  compat.py          runs on MCP SDK 1.x (FastMCP) and 2.x (MCPServer)
  config.py          where keys, cache and state live; read at call time
  http.py            one polite HTTP layer: pacing, retries, size caps, redaction
  usage.py           request counters, persisted per month (NARA's quota)
  models.py          Record, Rights, Media: the one normalised shape
  rights.py          rights vocabularies -> Rights.reuse
  download.py        safe download into the cache + provenance sidecar
  sources/<name>.py  one adapter per archive: parameters in, Records out
  tools/<name>.py    thin MCP tools over the adapters
```

Adapters are plain async functions returning dicts, so they are testable without MCP. Tools only validate, describe and delegate.

## Decisions

**Normalise, but keep the source tools.** `search` and `get_record` give one shape across archives, which is what an assistant needs to compare and filter. The per-source tools (`ia_grep_text`, `nara_children`, `si_terms`) expose what only that archive can do.

**Rights are a first-class field, and "unknown" is never "free".** Each adapter reads whatever its archive states (a licence URL, a rightsstatements.org URI, a category, free text) and maps it with the same table in `rights.py`. The only inference is a publication-year rule for US texts, and it is labelled `date-heuristic`. Conservative readings win: where DPLA's own category is more generous than the record's rights URI, the URI decides.

**Degrade, don't fail.** A keyless install works for two sources. A missing key is a clear, actionable message on that source only, and `search` carries on with the rest and reports `errors` and `skipped` per source.

**Keys live in files by default.** Two providers take the key as a query parameter, so URLs are secrets. The HTTP client's own request logging prints full URLs, so it is switched to WARNING and filtered. Tests assert that a key never appears in results, errors or logs.

**Downloads are the only place the server fetches a URL it was not coded to know.** Those URLs come from records, but a record is third-party data, so the download layer treats them as untrusted: https only, public addresses only, every redirect re-checked, size capped, written atomically. A DNS answer can change between the check and the connection; the exposure is limited because the URL is never model-supplied, and the server runs with the user's own permissions.

**Respect each provider's terms in code, not just in docs.** NARA's monthly quota is counted on disk and enforced; its no-bulk rule is enforced by returning one capped page per call; its attribution is included in results. Requests are paced per source and `Retry-After` is honoured.

**Two SDK generations.** The MCP Python SDK 2.x renamed `FastMCP` to `MCPServer`, and in 2.x only a `ToolError` reaches the model with its message (anything else is reported as a crash). `compat.py` hides the rename, and every error this package raises subclasses `ToolError`. CI runs both.

## Testing

Everything runs offline against `respx` mocks. The `ia_*`, `commons_*`, `dpla_live` and `si_live_*` fixtures are trimmed real responses, and they have caught real mismatches (Smithsonian mixes authors and subjects in one list; DPLA carries its own rights category). `dpla_items.json`, `nara_search.json`, `si_search.json` and `si_terms.json` are hand-written from the providers' documented shapes; `nara_search.json` should be replaced by a real capture once a NARA key is available. `heritage-research-mcp doctor --live` and the `Live smoke` workflow check the real services.

## Ideas not built

- Chronicling America (Library of Congress) newspapers. Its legacy API is gone and loc.gov challenges scripted requests, so the sanctioned route is the bulk OCR download, indexed locally.
- A local full-text index over cached downloads, so repeated research does not re-fetch.
- IIIF image fetching with cropping, for very large scans such as period maps.
- HathiTrust, which has a catalogue API but blocks scripted full-text access.
