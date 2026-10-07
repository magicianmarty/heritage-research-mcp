# Getting the API keys

The Internet Archive and Wikimedia Commons need no key. DPLA, NARA and Smithsonian each need a free one. Without a key a source is skipped (and says so); nothing else is affected.

| Source | Key | How you get it | Limit | What it is for |
|---|---|---|---|---|
| [Internet Archive](#internet-archive-and-wikimedia-commons-no-key) | none | | no published quota; requests paced at about 5 a second | Books, memoirs, reports, and full text inside them |
| [Wikimedia Commons](#internet-archive-and-wikimedia-commons-no-key) | none | | polite pacing | Freely licensed photographs, maps, prints |
| [DPLA](#dpla) | free | one command; the key is emailed | no routine limit | Finding items across US libraries, archives and museums |
| [NARA](#national-archives-catalog-nara) | free | email request to NARA | 10,000 calls a month | Federal records, military files, photographs, maps, pension files |
| [Smithsonian](#smithsonian-open-access) | free | web form, self-service | set per key by api.data.gov (1,000 when tested) | Museum objects, photographs, archives; CC0 images |

Wherever you get a key, store it the same way:

```bash
umask 077
mkdir -p ~/.config/heritage-research-mcp/keys
printf '%s\n' 'PASTE_THE_KEY_HERE' > ~/.config/heritage-research-mcp/keys/dpla      # or nara, or smithsonian
heritage-research-mcp doctor --live
```

The environment variables `DPLA_API_KEY`, `NARA_API_KEY` and `SMITHSONIAN_API_KEY` also work and take precedence. The server warns if a key file is readable by other users.

## DPLA

**For:** finding items held by libraries, archives and museums across the US, including state and local collections. DPLA indexes descriptions and links to the holder; it does not host the files.

**Get a key:** self-service.

```bash
curl -X POST https://api.dp.la/v2/api_key/YOUR_EMAIL_ADDRESS
```

The key (32 characters) is emailed to that address. DPLA uses the address only for aggregate usage monitoring and urgent notices. There is no routine rate limit, but DPLA reserves the right to restrict abusive use. Read their [policies](https://pro.dp.la/developers/policies).

DPLA's metadata is CC0. It does not host the items: rights belong to the contributing institution, and each record carries that institution's statement.

## National Archives Catalog (NARA)

**For:** federal records. Think Official Records, Adjutant General and Quartermaster files, War Department maps, Brady's photographs, pension files, census material, with machine OCR and partner transcriptions for many scans.

**Get a key:** issued on request by email. NARA does not state a turnaround, so ask early.

1. Create an account at <https://catalog.archives.gov> (the key request asks for your Catalog username).
2. Email **Catalog_API@nara.gov** with your email address and Catalog username, and say briefly what you will use it for. A request that works:

   > Subject: Catalog API key request
   >
   > Please issue a read-only Catalog API key for this account.
   > Email: YOUR_EMAIL
   > Catalog username: YOUR_USERNAME
   > Use: personal historical research through an MCP server that makes targeted, low-volume queries (it counts requests and stops at the monthly limit).

3. Store it as above. It is sent in the `x-api-key` header. The reply email gives a key, the header to use and a 10,000-calls-a-month limit.

If the key is wrong or missing, NARA does not answer with an error: it returns its website's HTML page with HTTP 200. The server recognises that and says so; `heritage-research-mcp doctor --live` is the quickest check. The same page comes back for a query with parentheses next to AND, OR or NOT.

Terms worth knowing, all enforced by this server:

- **10,000 queries per month per key** by default (higher tiers need justification). The counter resets on the first of the month, UTC.
- **No scraping and no downloading "all the data" through the API.** For bulk needs NARA points to the AWS Open Data registry.
- NARA asks for this attribution wherever you use the API, and the server includes it in results: *"This product uses the National Archives Catalog API but is not endorsed or certified by the National Archives."*

The API has a v2 and a v3 (search only so far). The server uses v2; set `NARA_API_VERSION=v3` to use v3 for search.

## Smithsonian Open Access

**For:** objects, photographs, archives and library items from the Smithsonian museums. Records marked CC0 come with direct high-resolution image files.

**Get a key:** self-service, through api.data.gov.

1. Open <https://api.data.gov/signup/>, enter your name and email, and say what you will use it for.
2. api.data.gov issues the key after you submit the form. Store it as above.

The key goes in the `api_key` query parameter. Limits are set by api.data.gov per key (1,000 requests were allowed when tested), and the remaining allowance appears in `usage_report` once the server has seen a response. (api.data.gov's shared `DEMO_KEY` allows only a very small number of requests and is shared, so it is not enough to be useful.)

Records marked CC0 can be reused freely. Others say "Usage conditions apply", which the server reports as `restricted`.

## Internet Archive and Wikimedia Commons (no key)

Nothing to set up. The **Internet Archive** is the one to use for scanned books, memoirs, regimental histories and official reports, and for searching the OCR text *inside* them. Its rights are often unstated, so records say `unknown` until the holder says otherwise. **Wikimedia Commons** is the one to use for photographs, maps and prints you may reuse: each file carries its own licence, which the server reads and reports. Both are shared public services, so the server paces its requests and names this project in its User-Agent. Set `HERITAGE_MCP_CONTACT` to your email or site so they can reach you if something misbehaves.

## Checking a key works

```bash
heritage-research-mcp doctor --live
```

Each source shows `key from file` (or `env`), then `ok` with a record count, or `FAIL` with the reason. Not working? A key file with a stray space or quote is the usual cause (the file holds the bare key on one line). DPLA's email can land in spam. NARA does not return an error for a wrong key: see the note under NARA.

## Rotating a key

Replace the file or variable and restart the client. Nothing else holds the key.
