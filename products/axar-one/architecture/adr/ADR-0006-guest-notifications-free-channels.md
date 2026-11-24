# ADR-0006 — Guest notifications without paid channels

Status: **Accepted as the owner's decision** (#18 comments, 24 Nov); implementation in C2/C5.

**Decision.**
- Live order status uses SSE with an event cursor, resume, heartbeat and a polling fallback. A reconnect always fetches an authoritative snapshot first.
- Optional Web Push (VAPID, service worker) is offered only after feature detection and an explicit opt-in:
  - no personal data in the push payload;
  - subscriptions expire and can be revoked.
- iOS limits (Home Screen app only) are explained honestly, and installation is never required.
- A pickup display with anonymous, day-scoped codes is always available.
- **No SMS** and no paid provider.
- "Accepted by the push service" is shown as *sent*, never as *delivered*.
- A notification failure never rolls back a paid order or blocks the kitchen.
