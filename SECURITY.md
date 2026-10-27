# Security policy

Security fixes target the current development branch. This application is intended
for local use on `127.0.0.1`, with one working profile per OS account.

Report vulnerabilities privately to [Guido Dipietro](https://github.com/GuidoDipietro)
through [GitHub Private Vulnerability Reporting](https://github.com/GuidoDipietro/wca-agentic-translation-tool/security/advisories/new)
when enabled. Activation is still pending verification; if the reporting page is
unavailable, do not publish vulnerability details in an issue.

Include the affected commit, OS/Python version, impact and a minimal reproduction
using fictional data. Do not attach `.env`, `data/auth/`, tokens, private
translations or unredacted provider responses.

If a credential is exposed, revoke it at the provider and reconnect through the
application. Removing it from a file or Git history does not revoke access.

Automated tests use simulated providers. Dependency audits and local secret scans
do not guarantee the absence of vulnerabilities. Remaining integration checks
are tracked in [CHECKLIST.md](CHECKLIST.md).
