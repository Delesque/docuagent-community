# Security

DocuAgent is intended to bind to loopback on a trusted local workstation. Do not
publish the HTTP port to a network or use it as an unauthenticated shared service.
Cookie, Origin and Host checks are not a substitute for a production remote
authentication system.

Model providers receive relevant project context. Public repository searches
require confirmation of the query. Search results and model output are untrusted;
neither can grant permission to run commands or reuse third-party code.

DocuAgent records anonymous aggregate usage counters locally by default. It does not
store project paths, code, prompts, model responses, provider names or API keys in
these metrics. Network upload is disabled until the user explicitly agrees in
Settings. Once enabled, batches may be sent to the product's HTTPS metrics endpoint
at most once every 24 hours. Users can inspect and clear the local counters, and a
failed upload leaves pending data on the local machine.

Worktrees and file scopes protect project writes but are not an OS sandbox.
Plugins, MCP servers, verification commands and generated applications may run
with your user's privileges. Inspect them before enabling or executing them.

Do not include API keys, customer repositories or secrets in public issues.
Before public launch, maintainers must configure a private vulnerability-reporting
channel and publish a supported-version policy. No support SLA is promised by
this preview.
