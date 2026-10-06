# Security

Please report vulnerabilities privately through GitHub's "Report a vulnerability" button on the Security tab, not in a public issue.

What this server handles that is worth protecting:

- **API keys** for DPLA, NARA and Smithsonian. They are read from environment variables or from `~/.config/heritage-research-mcp/keys/`, are never written to results, and are redacted from error messages and logs. A leak of a key into any output is a bug.
- **Downloads.** `download_media` fetches only `https` URLs on public addresses, refuses credentials and non-standard ports, re-checks every redirect, and enforces a size limit. A way around any of those is a bug.
