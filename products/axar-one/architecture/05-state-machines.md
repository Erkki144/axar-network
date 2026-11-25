# 05 — State machines

Each lifecycle is **separate**. A single overloaded status (G-DM-3) is what this replaces. Transitions are compare-and-swap updates (`WHERE state = :from AND version = :v`) and every transition emits an event.

## Built today

```mermaid
stateDiagram-v2
  direction LR
  state "Drawer session" as D {
    [*] --> open: open (float)
    open --> closed: close (counted; variance≠0 needs manager or step-up)
  }
```
```mermaid
stateDiagram-v2
  direction LR
  state "Order (today)" as O {
    [*] --> open: get-or-create per table (one open per table)
    open --> open: lines changed / sent to kitchen (version+1)
    open --> paid: pay-cash = sale + close in ONE transaction
  }
```

## Target (C2/C3)

```mermaid
stateDiagram-v2
  direction LR
  state "Order" as OR {
    [*] --> draft
    draft --> placed: submit (staff) / checkout (guest)
    placed --> closed: fully paid AND fulfilled or cancelled
    placed --> cancelled: cancel (approval if any item fired)
  }
```
```mermaid
stateDiagram-v2
  direction LR
  state "Payment (per tender / intent)" as P {
    [*] --> created
    created --> pending: hosted checkout started
    pending --> succeeded: VERIFIED webhook / cash recorded
    pending --> failed
    pending --> expired
    succeeded --> partially_refunded
    partially_refunded --> refunded
    succeeded --> refunded
  }
```
```mermaid
stateDiagram-v2
  direction LR
  state "Fulfilment (per ticket line / station)" as F {
    [*] --> queued: released (paid, or pay-later policy)
    queued --> accepted
    accepted --> preparing
    preparing --> ready
    ready --> handed_off: served / collected
    queued --> cancelled
    accepted --> cancelled
    preparing --> cancelled: needs approval + waste disposition
    ready --> cancelled: needs approval + waste disposition
  }
```
```mermaid
stateDiagram-v2
  direction LR
  state "Notification (per channel attempt)" as N {
    [*] --> pending
    pending --> attempted: handed to channel (SSE frame / Web Push accepted)
    pending --> failed: retry with jitter (bounded)
    failed --> pending
    failed --> dead: max attempts, visible to staff
    attempted --> acknowledged: guest or staff confirms
  }
```
"attempted" is never shown as "delivered" (#18 owner gate).

```mermaid
stateDiagram-v2
  direction LR
  state "Export to books" as E {
    [*] --> staged
    staged --> exported: sent
    exported --> acknowledged: provider receipt verified
    exported --> failed
    failed --> staged: retry
    failed --> dead_letter: operator
  }
```

## Cross-lifecycle rules

| Rule | Enforced by |
| --- | --- |
| Kitchen release only after a verified payment, or an explicit pay-later policy (table service) | ORDERS command checks PAY state in the same transaction |
| A redirect "success" from the provider never changes the payment state; only a verified webhook does | PAY webhook inbox + signature check |
| Payment succeeded but stock unavailable → exception + refund workflow, never a silent cancel | WORKFLOW (C3/C4) |
| A refund does not restock food automatically; the line carries a disposition (`restock` / `waste` / `none`) | `pos.refund.completed` v2 |
| A sale consumes either the recipe version or finished stock, never both | STOCK consumption rule (#14) |
