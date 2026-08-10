# Sprint 9 PR2A menu security gate

## Decision

**DEFERRED — SECURITY PREREQUISITE.** PR2A adds no mobile menu route and does
not treat a menu result as a manual or provider-backed LogFood command.

## Evidence

The existing web fetch layer validates HTTP(S) URLs, rejects non-public IP
classes, revalidates redirect hops, and bounds redirects, timeouts, response
bytes, extracted text and item counts. Existing web routes also have rate limits
and result caches.

The mobile gate still fails on three independent points:

1. Routes/helpers log full URLs, sub-links, extracted item names and macro
   payloads, which is incompatible with the mobile privacy boundary.
2. DNS pinning temporarily replaces process-global `socket.getaddrinfo`; that is
   unsafe when concurrent requests resolve different hosts.
3. Redis caching is optional and there is no in-flight single-flight/dedup or
   explicit blocking-capacity boundary around repeated scrape/OCR/LLM work.
   Concurrent identical cache misses can duplicate expensive work.

## Re-entry criteria

A future prerequisite must add request-local connection pinning, privacy-safe
structured logs, bounded concurrency and in-flight deduplication, then test
redirects/DNS rebinding, private/link-local targets, time/size/prompt bounds,
concurrent duplicates and log redaction. Persistence must still use a separately
approved canonical command; manual logging does not authorize menu nutrition.
