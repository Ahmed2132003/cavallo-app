# Certificate Pinning - Decision Record (P-100)

- Status: DEFERRED (deliberate decision, not an oversight)
- Part: P-100, Phase 19 (Security Hardening)
- Date recorded: 2026-10-03
- Architecture reference: Section 15 (certificate pinning is a Could-Have / deferred item for the MVP)
- Code changed by this decision: none. lib/core/network/dio_client.dart (P-004) is untouched and no dependency was added.

## Decision

Certificate pinning is NOT implemented in the MVP. It is formally deferred.

## Why

1. There is nothing real to pin yet. Section 7 item 9 (domain names for API / staging / production) is still open: the production API has no real domain and no real TLS certificate. The mobile CONFIG.md only shows placeholder URLs (api.example.com).
2. A wrong pin is worse than no pin. If the pinned hash does not match the certificate the server presents, every installed app loses all network access until a new build is released. The release path is also unresolved (Section 7 item 8: App Store / Play Store accounts, Phase 22).
3. Normal TLS validation stays active. At the time of this decision lib/ contains no badCertificateCallback override and no custom SecurityContext, so the platform's default certificate validation applies, and a bad certificate is already mapped to a failure by ErrorInterceptor (DioExceptionType.badCertificate).
4. Section 15 itself classifies pinning as optional for the MVP.

## What is needed to implement it later

1. A real production API domain (Section 7 item 9) with a valid certificate already serving traffic, plus a staging domain to test pin failures against.
2. A choice of what to pin: the leaf certificate's public-key (SPKI) hash, or the issuing CA / intermediate (easier rotation). Either way, ship at least one backup pin so a certificate rotation cannot lock users out.
3. A working release path (Section 7 item 8) so a corrected pin can be shipped quickly.
4. Implementation point: lib/core/network/dio_client.dart (P-004), through a custom HttpClientAdapter (IOHttpClientAdapter hook), or the dio_certificate_pinning package named in the master plan (check that it is still maintained before adopting it). Pinning must apply to staging/prod only, never to the dev URLs (localhost / 10.0.2.2).
5. Test required at that time: a request to a host whose certificate does not match the pin must fail with DioExceptionType.badCertificate, and a request to the correctly pinned host must succeed.

## When to revisit

When Section 7 item 9 is resolved, and before Phase 22 (Production Build and Release Readiness) is closed.