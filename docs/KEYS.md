# Getting the API keys

The Internet Archive and Wikimedia Commons need no key. DPLA, NARA and Smithsonian each need a free one.

Wherever you get a key, store it the same way:

```bash
umask 077
mkdir -p ~/.config/heritage-research-mcp/keys
printf '%s\n' 'PASTE_THE_KEY_HERE' > ~/.config/heritage-research-mcp/keys/dpla      # or nara, or smithsonian
heritage-research-mcp doctor --live
```

The environment variables `DPLA_API_KEY`, `NARA_API_KEY` and `SMITHSONIAN_API_KEY` also work and take precedence. The server warns if a key file is readable by other users.

## DPLA

Self-service.

```bash
curl -X POST https://api.dp.la/v2/api_key/YOUR_EMAIL_ADDRESS
```

The key (32 characters) is emailed to that address. DPLA uses the address only for aggregate usage monitoring and urgent notices. There is no routine rate limit, but DPLA reserves the right to restrict abusive use. Read their [policies](https://pro.dp.la/developers/policies).

DPLA's metadata is CC0. It does not host the items: rights belong to the contributing institution, and each record carries that institution's statement.

## National Archives Catalog (NARA)

Keys are issued on request by email. NARA does not state a turnaround, so ask early.

1. Create an account at <https://catalog.archives.gov> (the key request asks for your Catalog username).
2. Email **Catalog_API@nara.gov** with your email address and Catalog username, and say briefly what you will use it for. A request that works:

   > Subject: Catalog API key request
   >
   > Please issue a read-only Catalog API key for this account.
   > Email: YOUR_EMAIL
   > Catalog username: YOUR_USERNAME
   > Use: personal historical research through an MCP server that makes targeted, low-volume queries (it counts requests and stops at the monthly limit).

3. Store it as above. It is sent in the `x-api-key` header.

Terms worth knowing, all enforced by this server:

- **10,000 queries per month per key** by default (higher tiers need justification). The counter resets on the first of the month, UTC.
- **No scraping and no downloading "all the data" through the API.** For bulk needs NARA points to the AWS Open Data registry.
- NARA asks for this attribution wherever you use the API, and the server includes it in results: *"This product uses the National Archives Catalog API but is not endorsed or certified by the National Archives."*

The API has a v2 and a v3 (search only so far). The server uses v2; set `NARA_API_VERSION=v3` to use v3 for search.

## Smithsonian Open Access

Self-service, through api.data.gov.

1. Open <https://api.data.gov/signup/>, enter your name and email, and say what you will use it for.
2. api.data.gov issues the key after you submit the form. Store it as above.

The key goes in the `api_key` query parameter. Limits are set by api.data.gov per key, and the remaining allowance appears in `usage_report` once the server has seen a response. (api.data.gov's shared `DEMO_KEY` allows only a very small number of requests and is shared, so it is not enough to be useful.)

Records marked CC0 can be reused freely. Others say "Usage conditions apply", which the server reports as `restricted`.

## Rotating a key

Replace the file or variable and restart the client. Nothing else holds the key.
