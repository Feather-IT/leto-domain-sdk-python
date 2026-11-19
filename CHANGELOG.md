# Changelog

All notable changes to this package are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package uses
[semantic versioning](https://semver.org).

## [Unreleased]

### Added

- First version of the Python SDK, generated from the LETO Domain API OpenAPI document 1.1.0:
  108 operations in 14 resources, on `LetoClient` (synchronous) and `AsyncLetoClient` (asyncio).
- Bearer API key authentication, User-Agent `leto-domain-sdk-python/<version>`.
- Exceptions per error class with the API error code, data, extra fields and request ID.
- Retries with backoff and `Retry-After` for GET requests and requests with an Idempotency-Key.
  A charging request is not resent after a 502 or 504 error from the API itself (the registry
  may have applied it), only after a proxy error page, a connection error, 429 or 503.
- Automatic `Idempotency-Key` on registration, renewal, restore and transfer.
- `Page` and `AsyncPage` with `auto_paginate()` for every list endpoint, including
  `GET /transactions`.
- `register_with_quote()`, `renew_with_quote()` and `initiate_with_quote()` helpers.
- `TypedDict` shapes for every request and response, and `py.typed`.
