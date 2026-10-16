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
                     (dispatch.py searches a local SQLite index instead of a remote API)
  tools/<name>.py    thin MCP tools over the adapters
```

Adapters are plain async functions returning dicts, so they are testable without MCP. Tools only validate, describe and delegate.

## Decisions

**Normalise, but keep the source tools.** `search` and `get_record` give one shape across archives, which is what an assistant needs to compare and filter. The per-source tools (`ia_grep_text`, `nara_children`, `si_terms`) expose what only that archive can do.

**Rights are a first-class field, and "unknown" is never "free".** Each adapter reads whatever its archive states (a licence URL, a rightsstatements.org URI, a category, free text) and maps it with the same table in `rights.py`. The only inference is a publication-year rule for US texts, and it is labelled `date-heuristic`. Conservative readings win: where DPLA's own category is more generous than the record's rights URI, the URI decides.

**Type filtering is translation, not a promise.** `kind` (text, image, map, audio, video) is converted into each archive's own vocabulary, and every result says how (`kind_applied`). Maps are the weak case: Commons, DPLA and the Smithsonian catalogue them well, the Internet Archive has no map type, so the filter there is a subject-and-collection match. Records also carry a best-effort `kind`, derived from whatever the archive provides (a media type, a "Maps" heading, "Cartographic material"). Explicit per-archive filters always win over `kind`.

**Degrade, don't fail.** A keyless install works for two sources. A missing key is a clear, actionable message on that source only, and `search` carries on with the rest and reports `errors` and `skipped` per source.

**Keys live in files by default.** Two providers take the key as a query parameter, so URLs are secrets. The HTTP client's own request logging prints full URLs, so it is switched to WARNING and filtered. Tests assert that a key never appears in results, errors or logs.

**Downloads are the only place the server fetches a URL it was not coded to know.** Those URLs come from records, but a record is third-party data, so the download layer treats them as untrusted: https only, public addresses only, every redirect re-checked, size capped, written atomically. A DNS answer can change between the check and the connection; the exposure is limited because the URL is never model-supplied, and the server runs with the user's own permissions.

**A block is an answer, not an obstacle.** loc.gov's limits page says it blocks clients for an hour past 20 requests a minute and may show a bot challenge even under that rate. So `http.py` paces that source at 3 s, and a `cf-mitigated: challenge` header or a 429 that outlasts the retries raises `Blocked` and starts a 10-minute cool-down for that source only, during which calls fail at once without touching the network. Nothing tries to look like a browser or solve the challenge. `doctor` reports `BLOCKED` without failing, so a cloud runner that is challenged does not turn the weekly smoke test red.

**Respect each provider's terms in code, not just in docs.** NARA's monthly quota is counted on disk and enforced; its no-bulk rule is enforced by returning one capped page per call; its attribution is included in results. Requests are paced per source and `Retry-After` is honoured.

**A source with no search API gets a local index, built only on request.** The Richmond Daily Dispatch publishes the whole run as one TEI download and nothing else usable (its search box calls a Solr core that sits behind HTTP credentials, so there is no search API to use). `dispatch_prepare` downloads the archive through the checked downloader, so it has a provenance sidecar, and `build_index` writes an FTS5 database next to it, swapped in atomically. Until the index exists the source is not "configured", so the keyless default of `search` never starts a 92 MB download on its own. The index carries a schema version; a mismatch is treated as missing.

**Whole years come from the Library's bulk archives, not the API.** loc.gov's JSON API allows 20 requests a minute and challenges scripted clients, so it suits finding a page, not reading a year. The Library publishes OCR as one `.tar.bz2` per digitisation batch (`chroniclingamerica.loc.gov/data/ocr/`) and recommends that route for text analysis. `chronam_bulk.py` picks the batches from the Library's own manifest (`backups/*.yaml`, with `metadata/*.json` for batches newer than the manifest) and its published title directory, streams each archive once, keeps only `ocr.txt` members whose path date is in range, hashes the stream and compares it with the published SHA-256 before committing that batch's rows in one transaction. Member names are only ever matched against a strict pattern and never written to disk, so an archive cannot place a file. Progress is the `done` table (batch, range, verified), which is what makes an interrupted job resumable.

**Narrowing a bulk job.** Archives are whole batches, so a month cannot shrink a download by itself; selection by paper does (`--lccn`). Two further cuts use the archive's sort order, which was checked on a full real archive (names stored in exact lexicographic order, zero date disagreements). A range request of the first 128 KB to 2 MB, decompressed with `BZ2Decompressor` (bzip2 emits nothing until a ~900 KB block is complete), reveals the first member and so a single-paper archive's start date, cached by batch and SHA-256; an archive that starts after the range is dropped. And reading stops once every wanted paper has produced a page past the range end. The second is a trade: a partial read cannot be hashed, so those archives are marked `verified = 0`, and `--verify` turns the shortcut off. The out-of-order guard only protects against disorder seen before the stop point; if an archive were unsorted from its first member, early stop would miss pages, which is why verification is opt-in rather than assumed and why the assumption is stated rather than hidden. The same cool-down as the API applies: a 429 or challenge pauses the job rather than retrying.

**TEI is read with regular expressions, never an XML parser.** The files declare an external Perseus DTD and rely on SGML entities it defines. A parser either fails on them or is told to fetch and expand them; neither is acceptable for third-party input. Articles are the `div3` units in order, minus the `ad-blank` placeholders, which is exactly the numbering dispatch.richmond.edu uses (checked against its issue JSON). Records are keyed by TEI file name, not date, because the source has one date that disagrees with its title and one date shared by two files.

**Two SDK generations.** The MCP Python SDK 2.x renamed `FastMCP` to `MCPServer`, and in 2.x only a `ToolError` reaches the model with its message (anything else is reported as a crash). `compat.py` hides the rename, and every error this package raises subclasses `ToolError`. CI runs both.

## Testing

Everything runs offline against `respx` mocks. The `ia_*`, `commons_*`, `dpla_live` and `si_live_*` fixtures are trimmed real responses, and they have caught real mismatches (Smithsonian mixes authors and subjects in one list; DPLA carries its own rights category). `nara_live_*.json` are trimmed captures from 7 October 2026; the hand-written NARA fixture they replaced had invented fields (`creators`, a record-level `recordGroupNumber`) the real API does not send, and missed `contributors`, `otherExtractedText`, the `1234` file-size placeholder and the HTML-with-200 failure for a bad key. `chronam_search.json` is one real loc.gov result, copied from the output the Library's own notebook records (its pagination block is from the same notebook), so it predates the code and could have drifted; the `resource` response in `test_chronam.py` is hand-written from the keys those notebooks read. The adapter has not run against the live service from the development machine, which loc.gov challenges. `dispatch_issue.xml` is a trimmed real issue (the real header, three real articles, an `ad-blank` placeholder and a real death notice). `dpla_items.json`, `si_search.json` and `si_terms.json` are still hand-written from the providers' documented shapes. `heritage-research-mcp doctor --live` and the `Live smoke` workflow check the real services.

## Ideas not built

- Seeking inside a bulk archive by bzip2 block (each block decodes independently) to jump straight to a date, instead of reading from the start to the stop point. It would cut a month to a small fraction of an archive, at the cost of a fragile reader that must resynchronise on tar headers.
- Skipping archives that end before the range. Only start dates can be read cheaply; ends would need the same block seeking.
- Refreshing the title directory from the live Library site. Its endpoints were challenged when checked, so the directory used is a published snapshot, which can miss papers added since.
- Virginia Chronicle (Library of Virginia, Veridian). Its OAI-PMH endpoints are behind the same kind of challenge, and its `robots.txt` disallows automated crawlers. Asking the library for its data is the remaining route.
- A local full-text index over cached downloads, so repeated research does not re-fetch.
- IIIF image fetching with cropping, for very large scans such as period maps.
- HathiTrust, which has a catalogue API but blocks scripted full-text access.
