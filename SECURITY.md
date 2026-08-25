# Security policy

This repository is a research prototype. It has no authentication, durable session storage,
payment integration, or production deployment hardening. Do not deploy the FastAPI application to
an untrusted network without adding those controls.

Report a vulnerability through the repository's private GitHub security advisory feature. Do not
post API keys, raw LLM traces, private conversations, annotation workbooks, or local dataset files
in a public issue.

If a credential is committed accidentally, revoke it first. Removing it in a later commit is not
sufficient because Git history remains accessible.
