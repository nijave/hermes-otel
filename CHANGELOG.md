# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-23

### Added

- Initial release.
- OTLP/HTTP span export building a session → turn → chat/tool tree using official GenAI semconv (`invoke_agent`, `chat`, `execute_tool`).
- Capture modes: `none`, `sanitized` (default), and `full`.
- LRU-bounded session registry (~256 trees) with eviction warnings; partial traces still exported.
- Observer-hook consumption of `hermes.observer.v1` events, with real-discovery integration tests via plugin discovery.
- Wire round-trip test asserting exported span names against a live OTel Collector.
- Docker-compose E2E suite running against a real collector with stdout assertions.
- CI: pytest on Python 3.11/3.12 plus plugin manifest doctor check.

[0.1.0]: https://github.com/nijave/hermes-otel/releases/tag/v0.1.0
