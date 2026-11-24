# 06 — Trust boundaries and permissions

| Boundary | Untrusted input | Control (built / planned) |
| --- | --- | --- |
| Staff browser → API | everything in the request, including amounts, IDs and headers | server pricing; strict pydantic bodies (`extra=forbid`); session + device token; role and scope checks (**built**) |
| Device ↔ server | stolen device token | revocation, PIN still required, lockouts (**built**) |
| Guest phone → API (#18) | QR contents, cart, any IDs | signed, revocable venue/table locator; scoped opaque order token; rate limits; no staff permissions (**planned C5**) |
| PSP webhook → API | body, headers | HMAC signature + timestamp tolerance; inbox dedup by provider event ID; amount/currency/order reconciliation (**planned C3**) |
| POS → CONNECT | event envelope | read from the DB / authenticated transport only; payload hash is integrity, not authentication (CONNECT doc) |
| Text from invoices, issues, guests → AI (future) | prompt injection | AI proposes typed actions only; policy validates; a human approves external effects; no ledger writes (ADR-0007) |

The permission matrix is in `products/axar-pos/docs/SLICE_6_AUTH.md`; that file is authoritative until moved here.

**Secrets:**
- the server key comes from the environment / secret store;
- tokens are stored as hashes;
- PINs and passwords use argon2id;
- TOTP seeds are encrypted with AES-GCM;
- none of these are logged (tested).

**PII:**
- staff display names only;
- guest flows (#18) must not store names for pickup screens, use minimal retention and must not bundle marketing consent.
