"""Cross-register consistency check and document trace (#60).

Read-only. ``check(s, tenant_id)`` recomputes every register from the
registers it is derived from and reports each invariant as a finding:

    ok       the invariant holds
    warning  not wrong, but not finished yet (e.g. a consumer still has
             events to process, a register series has a configured jump)
    fail     the registers disagree: someone must investigate

``trace_sale`` follows one sale through every register it touched: order,
kitchen tickets, payments, receivable, refunds, outbox events, which
consumers applied them, stock movements, the accounting export queue and the
device journal / sync inbox. It is the "where did this sale go" answer.

Nothing here writes. Every query is filtered by ``tenant_id`` (and RLS
applies on PostgreSQL on top of that).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, inventory, services
from .consumers import BOOKS_EVENTS
from .models import (
    CustomerInvoice,
    DeadLetter,
    DeviceInbox,
    DeviceJournalEntry,
    DrawerCashMovement,
    DrawerSession,
    EventConsumer,
    ExportQueue,
    InboxEntry,
    KitchenTicket,
    KitchenTicketLine,
    Order,
    OutboxEvent,
    Payment,
    Product,
    Refund,
    RefundAllocation,
    RefundLine,
    Register,
    Sale,
    SaleLine,
    SaleRefundState,
    StockMovement,
)

SETTLING = ("captured", "pending")   # payment states that count towards a bill
MAX_EXAMPLES = 20                    # problems listed per finding (the count is always exact)


class _Finding:
    def __init__(self, check: str, title: str):
        self.check, self.title = check, title
        self.problems: list[dict] = []
        self.pending: list[dict] = []
        self.checked = 0

    def fail(self, **detail) -> None:
        self.problems.append(detail)

    def wait(self, **detail) -> None:
        self.pending.append(detail)

    def out(self) -> dict:
        status = "fail" if self.problems else ("warning" if self.pending else "ok")
        return {"check": self.check, "title": self.title, "status": status, "checked": self.checked,
                "problem_count": len(self.problems), "problems": self.problems[:MAX_EXAMPLES],
                "pending_count": len(self.pending), "pending": self.pending[:MAX_EXAMPLES]}


def check(s: Session, tenant_id: str) -> dict:
    """Run every invariant for one tenant. ``ok`` is False if any check fails."""
    findings = [rule(s, tenant_id) for rule in RULES]
    out = [f.out() for f in findings]
    return {"tenant_id": tenant_id, "ok": all(f["status"] != "fail" for f in out),
            "complete": all(f["status"] == "ok" for f in out),
            "summary": dict(Counter(f["status"] for f in out)), "checks": out}


# --------------------------------------------------------------------- money


def _drawers(s: Session, tenant_id: str) -> _Finding:
    """R1: each drawer's running totals equal the immutable sales/payments and
    refunds ledgers; a closed drawer's expected cash = float + in - out."""
    f = _Finding("R1.drawer_cash", "Kassasahtli jooksev summa = müügi-, tagastus- ja sularahaliikumiste register")
    for d in s.scalars(select(DrawerSession).where(DrawerSession.tenant_id == tenant_id).order_by(DrawerSession.id)):
        f.checked += 1
        sales = services.ledger_cash_sales(s, tenant_id, d.id)
        refunds = services.ledger_cash_refunds(s, tenant_id, d.id)
        if sales != (d.cash_sales_cents, d.sale_count):
            f.fail(drawer_id=d.id, register_id=d.register_id, what="cash in",
                   running=[d.cash_sales_cents, d.sale_count], ledger=list(sales))
        if refunds != (d.cash_refunds_cents, d.refund_count):
            f.fail(drawer_id=d.id, register_id=d.register_id, what="cash out",
                   running=[d.cash_refunds_cents, d.refund_count], ledger=list(refunds))
        # #68: non-sale cash (safe drop, float top-up) is its own append-only ledger.
        moved = dict(s.execute(
            select(DrawerCashMovement.direction, func.coalesce(func.sum(DrawerCashMovement.amount_cents), 0))
            .where(DrawerCashMovement.tenant_id == tenant_id, DrawerCashMovement.drawer_session_id == d.id)
            .group_by(DrawerCashMovement.direction)).all())
        cash_in, cash_out = int(moved.get("in", 0)), int(moved.get("out", 0))
        if cash_in != d.cash_in_cents:
            f.fail(drawer_id=d.id, register_id=d.register_id, what="cash in (non-sale)",
                   running=d.cash_in_cents, ledger=cash_in)
        if cash_out != d.cash_out_cents:
            f.fail(drawer_id=d.id, register_id=d.register_id, what="cash out (non-sale)",
                   running=d.cash_out_cents, ledger=cash_out)
        if d.status == "closed":
            expected = d.opening_float_cents + sales[0] - refunds[0] + cash_in - cash_out
            if d.expected_cents != expected or d.variance_cents != (d.counted_cents or 0) - expected:
                f.fail(drawer_id=d.id, what="closing", expected_cents=d.expected_cents, recomputed_cents=expected,
                       variance_cents=d.variance_cents)
    return f


def _sale_totals(s: Session, tenant_id: str) -> _Finding:
    """R2: a sale's totals equal the sum of its immutable lines."""
    f = _Finding("R2.sale_totals", "Müügi kogusumma = ridade summa (neto + km)")
    sums = {sid: (int(g), int(v), int(n), int(c)) for sid, g, v, n, c in s.execute(
        select(SaleLine.sale_id, func.sum(SaleLine.gross_cents), func.sum(SaleLine.vat_cents),
               func.sum(SaleLine.net_cents), func.count(SaleLine.id))
        .where(SaleLine.tenant_id == tenant_id).group_by(SaleLine.sale_id))}
    for sale in s.scalars(select(Sale).where(Sale.tenant_id == tenant_id)):
        f.checked += 1
        g, v, n, c = sums.get(sale.id, (0, 0, 0, 0))
        if c == 0 or (g, v, n) != (sale.total_cents, sale.vat_cents, sale.net_cents) \
                or sale.net_cents + sale.vat_cents != sale.total_cents:
            f.fail(sale_id=sale.id, sale=[sale.total_cents, sale.vat_cents, sale.net_cents], lines=[g, v, n],
                   line_count=c)
    return f


def _tenders(s: Session, tenant_id: str) -> _Finding:
    """R3: what was paid covers the sale exactly. Bill sales: the settling
    payments add up to the total. Cash sales: due = total + rounding and
    tendered - change = due."""
    f = _Finding("R3.tenders", "Makseviisid katavad müügi täpselt")
    paid = defaultdict(int)
    for sid, amount in s.execute(select(Payment.sale_id, Payment.amount_cents).where(
            Payment.tenant_id == tenant_id, Payment.sale_id.is_not(None), Payment.status.in_(SETTLING))):
        paid[sid] += int(amount)
    for sale in s.scalars(select(Sale).where(Sale.tenant_id == tenant_id)):
        f.checked += 1
        if sale.id in paid:
            if paid[sale.id] != sale.total_cents:
                f.fail(sale_id=sale.id, kind="bill", total_cents=sale.total_cents, payments_cents=paid[sale.id])
        elif sale.payment_method == "cash":
            if sale.cash_due_cents != sale.total_cents + sale.cash_rounding_cents \
                    or sale.tendered_cents - sale.change_cents != sale.cash_due_cents:
                f.fail(sale_id=sale.id, kind="cash", total_cents=sale.total_cents,
                       due_cents=sale.cash_due_cents, tendered_cents=sale.tendered_cents,
                       change_cents=sale.change_cents)
        else:
            f.fail(sale_id=sale.id, kind=sale.payment_method, reason="non-cash sale without settling payments")
    return f


def _refunds(s: Session, tenant_id: str) -> _Finding:
    """R4: refunds never exceed the sale, line by line, and the per-sale and
    per-line counters equal the immutable refund ledger."""
    f = _Finding("R4.refunds", "Tagastused ≤ müük; loendurid = tagastusregister")
    rl = defaultdict(lambda: [0, 0])
    for line_id, q, g in s.execute(select(RefundLine.sale_line_id, RefundLine.quantity, RefundLine.gross_cents)
                                   .where(RefundLine.tenant_id == tenant_id)):
        rl[line_id][0] += int(q)
        rl[line_id][1] += int(g)
    for a in s.scalars(select(RefundAllocation).where(RefundAllocation.tenant_id == tenant_id)):
        f.checked += 1
        q, g = rl.get(a.sale_line_id, (0, 0))
        if (a.refunded_quantity, a.refunded_gross_cents) != (q, g) or q > a.original_quantity:
            f.fail(sale_line_id=a.sale_line_id, sold=a.original_quantity, counter=[a.refunded_quantity,
                   a.refunded_gross_cents], ledger=[q, g])
    per_sale = defaultdict(lambda: [0, 0])
    for sid, total, cash in s.execute(select(Refund.original_sale_id, Refund.total_cents, Refund.cash_paid_cents)
                                      .where(Refund.tenant_id == tenant_id)):
        per_sale[sid][0] += int(total)
        per_sale[sid][1] += int(cash)
    totals = dict(s.execute(select(Sale.id, Sale.total_cents).where(Sale.tenant_id == tenant_id)).all())
    for st in s.scalars(select(SaleRefundState).where(SaleRefundState.tenant_id == tenant_id)):
        g, cash = per_sale.get(st.sale_id, (0, 0))
        if (st.refunded_gross_cents, st.refunded_cash_cents) != (g, cash) or g > totals.get(st.sale_id, 0):
            f.fail(sale_id=st.sale_id, sale_total=totals.get(st.sale_id), counter=[st.refunded_gross_cents,
                   st.refunded_cash_cents], ledger=[g, cash])
    return f


def _numbering(s: Session, tenant_id: str) -> _Finding:
    """R5: tenant receipt and refund numbers are 1..n without gaps or
    repeats; per-register series (K1-000123) have no repeats. A gap in a
    register series is a warning: it is legitimate after an owner moved the
    series floor, and the journal verification explains it."""
    f = _Finding("R5.numbering", "Tšekinumbrid järjest, kordusteta")
    for name, col in (("receipt", Sale.receipt_no), ("refund", Refund.refund_no)):
        model = Sale if name == "receipt" else Refund
        nos = sorted(n for (n,) in s.execute(select(col).where(model.tenant_id == tenant_id)))
        f.checked += len(nos)
        if nos != list(range(1, len(nos) + 1)):
            f.fail(series=name, numbers_seen=len(nos), max=nos[-1] if nos else 0,
                   missing=sorted(set(range(1, (nos[-1] if nos else 0) + 1)) - set(nos))[:MAX_EXAMPLES],
                   repeated=[n for n, c in Counter(nos).items() if c > 1][:MAX_EXAMPLES])
    by_reg = defaultdict(list)
    for reg, n in s.execute(select(Sale.register_id, Sale.register_receipt_no).where(
            Sale.tenant_id == tenant_id, Sale.register_receipt_no.is_not(None))):
        by_reg[reg].append(int(n))
    for reg, nos in by_reg.items():
        nos.sort()
        if len(set(nos)) != len(nos):
            f.fail(series="register", register_id=reg, repeated=[n for n, c in Counter(nos).items() if c > 1])
        elif nos[-1] - nos[0] + 1 != len(nos):
            f.wait(series="register", register_id=reg, first=nos[0], last=nos[-1], count=len(nos),
                   note="gap in the register series: check the series floor changes")
    return f


# -------------------------------------------------------------------- events


def _events_by_aggregate(s: Session, tenant_id: str, type_: str) -> dict[str, list[OutboxEvent]]:
    out = defaultdict(list)
    for ev in s.scalars(select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id, OutboxEvent.type == type_)):
        out[ev.aggregate_id].append(ev)
    return out


def _events_per_document(s: Session, tenant_id: str) -> _Finding:
    """R6: every sale has exactly one ``pos.sale.completed`` and every refund
    exactly one ``pos.refund.completed`` with an intact payload hash and the
    same totals; no event points to a document that does not exist."""
    f = _Finding("R6.events_per_document", "Iga müük/tagastus = täpselt üks terve sündmus")
    for type_, model, total_of in (("pos.sale.completed", Sale, lambda d: d.total_cents),
                                   ("pos.refund.completed", Refund, lambda d: d.total_cents)):
        evs = _events_by_aggregate(s, tenant_id, type_)
        docs = {d.id: d for d in s.scalars(select(model).where(model.tenant_id == tenant_id))}
        for doc_id, doc in docs.items():
            f.checked += 1
            got = evs.get(doc_id, [])
            if len(got) != 1:
                f.fail(document=doc_id, event_type=type_, events=len(got))
                continue
            ev = got[0]
            if hashlib.sha256(ev.payload.encode()).hexdigest() != ev.payload_hash:
                f.fail(document=doc_id, event_id=ev.id, reason="payload hash mismatch")
            elif json.loads(ev.payload).get("total_cents") != total_of(doc):
                f.fail(document=doc_id, event_id=ev.id, reason="event total differs from the document")
        for doc_id in set(evs) - set(docs):
            f.fail(event_type=type_, aggregate_id=doc_id, reason="event without its document")
    return f


def _event_pipeline(s: Session, tenant_id: str) -> _Finding:
    """R7: every consumer is caught up, nothing is dead-lettered or blocked.
    Lag alone is a warning (the worker may simply not have run yet)."""
    f = _Finding("R7.event_pipeline", "Sündmuste töötlejad on järel, surnud kirju pole")
    from .events import CONSUMERS

    head = events.latest_seq(s, tenant_id)
    cursors = {c.name: c for c in s.scalars(select(EventConsumer).where(EventConsumer.tenant_id == tenant_id))}
    for name in sorted(CONSUMERS):
        f.checked += 1
        c = cursors.get(name)
        cursor = c.cursor if c else 0
        if c is not None and c.blocked_at is not None:
            f.fail(consumer=name, reason="blocked", failing_seq=c.failing_seq, error=c.last_error)
        elif c is not None and c.failing_seq is not None:
            # Retrying with backoff: not "just lag". Show WHY, so a manager can fix the
            # cause (e.g. an outlet without a default warehouse) before it blocks.
            f.wait(consumer=name, reason="retrying", failing_seq=c.failing_seq, attempts=c.attempts,
                   error=c.last_error, cursor=cursor, head=head, lag=head - cursor)
        elif cursor < head:
            f.wait(consumer=name, cursor=cursor, head=head, lag=head - cursor)
    for dl in s.scalars(select(DeadLetter).where(DeadLetter.tenant_id == tenant_id, DeadLetter.resolved_at.is_(None))):
        f.fail(consumer=dl.consumer, event_id=dl.event_id, recorded_seq=dl.recorded_seq, reason="dead letter",
               error=dl.error)
    return f


def _applied(s: Session, tenant_id: str, consumer: str) -> set[str]:
    return set(s.scalars(select(InboxEntry.event_id).where(InboxEntry.tenant_id == tenant_id,
                                                           InboxEntry.consumer == consumer)))


def _books_export(s: Session, tenant_id: str) -> _Finding:
    """R8: each sale/refund event applied by ``books.export`` has exactly one
    export row with the same payload hash, and no export row lacks its event."""
    f = _Finding("R8.books_export", "Raamatupidamise ekspordijärjekord = müügi- ja tagastussündmused")
    applied = _applied(s, tenant_id, "books.export")
    rows = defaultdict(list)
    for r in s.scalars(select(ExportQueue).where(ExportQueue.tenant_id == tenant_id,
                                                 ExportQueue.destination == "books")):
        rows[r.event_id].append(r)
    relevant = {ev.id: ev for ev in s.scalars(select(OutboxEvent).where(
        OutboxEvent.tenant_id == tenant_id, OutboxEvent.type.in_({t for t, _ in BOOKS_EVENTS})))
        if (ev.type, ev.version) in BOOKS_EVENTS}
    for ev_id, ev in relevant.items():
        f.checked += 1
        if ev_id not in applied:
            f.wait(event_id=ev_id, event_type=ev.type, reason="not exported yet")
        elif len(rows.get(ev_id, [])) != 1 or rows[ev_id][0].payload_hash != ev.payload_hash:
            f.fail(event_id=ev_id, event_type=ev.type, export_rows=len(rows.get(ev_id, [])))
    for ev_id in set(rows) - set(relevant):
        f.fail(event_id=ev_id, reason="export row without a books event")
    pending = s.scalar(select(func.count(ExportQueue.id)).where(
        ExportQueue.tenant_id == tenant_id, ExportQueue.status != "acknowledged")) or 0
    if pending:
        # No delivery adapter exists yet (AXAR CONNECT is not in main): rows wait here.
        f.wait(export_rows_not_acknowledged=pending, reason="no accounting delivery adapter in main yet")
    return f


def _stock_consumption(s: Session, tenant_id: str) -> _Finding:
    """R9: every sale applied by ``stock.consume`` consumed stock for each of
    its stocked lines (recipe ingredients or the stocked item), and no sale
    consumption points to a sale that does not exist."""
    f = _Finding("R9.stock_consumption", "Iga müügi laoliikumised on olemas (retsept / laokaup)")
    applied = _applied(s, tenant_id, "stock.consume")
    events_by_sale = {ev.aggregate_id: ev for ev in s.scalars(select(OutboxEvent).where(
        OutboxEvent.tenant_id == tenant_id, OutboxEvent.type == "pos.sale.completed"))}
    moved = defaultdict(set)   # sale_id -> {line_no}
    for ref_id, ref_line in s.execute(select(StockMovement.ref_id, StockMovement.ref_line).where(
            StockMovement.tenant_id == tenant_id, StockMovement.kind == "sale_consumption")):
        moved[ref_id].add(int(ref_line) // 100)
    stocked = {sku: mode for sku, mode in s.execute(select(Product.sku, Product.stock_mode)
                                                    .where(Product.tenant_id == tenant_id))}
    for sale_id, ev in events_by_sale.items():
        f.checked += 1
        if ev.id not in applied:
            f.wait(sale_id=sale_id, reason="stock not consumed yet")
            continue
        lines = json.loads(ev.payload)["lines"]
        want = {n for n, ln in enumerate(lines, start=1) if stocked.get(ln["sku"], "none") != "none"}
        missing = sorted(want - moved.get(sale_id, set()))
        if not missing:
            continue
        if moved.get(sale_id):
            # one transaction consumes the whole sale: a partial set is always wrong
            f.fail(sale_id=sale_id, missing_lines=missing)
        else:
            # nothing moved: either the products became stocked after this sale was
            # applied (legitimate; the stock settings are not versioned yet) or the
            # consumer skipped it. Not provable from the registers alone: shown for review.
            f.wait(sale_id=sale_id, missing_lines=missing,
                   reason="no stock movement although the products are stocked now")
    sales = set(s.scalars(select(Sale.id).where(Sale.tenant_id == tenant_id)))
    for sale_id in set(moved) - sales:
        f.fail(sale_id=sale_id, reason="stock movement for a sale that does not exist")
    return f


def _stock_ledger(s: Session, tenant_id: str) -> _Finding:
    """R10: every stock balance equals the sum of its movements (qty and
    value). Negative stock is allowed by design but listed as a warning."""
    f = _Finding("R10.stock_ledger", "Laoseis = laoliikumiste summa")
    r = inventory.reconcile(s, tenant_id)
    f.checked = s.scalar(select(func.count()).select_from(StockMovement)
                         .where(StockMovement.tenant_id == tenant_id)) or 0
    for m in r["mismatches"]:
        f.fail(**m)
    for n in r["negative_stock"]:
        f.wait(reason="negative stock", **n)
    return f


# ------------------------------------------------------------ orders & money due


def _orders_and_kitchen(s: Session, tenant_id: str) -> _Finding:
    """R11: a settled order points to a sale with the same lines; what went to
    the kitchen equals the order's sent quantities."""
    f = _Finding("R11.orders_kitchen", "Tellimus = müük; köögi tšekid = saadetud kogused")
    ticket_qty = defaultdict(int)
    for order_id, pid, q in s.execute(
            select(KitchenTicket.order_id, KitchenTicketLine.product_id, KitchenTicketLine.quantity)
            .join(KitchenTicketLine, KitchenTicketLine.ticket_id == KitchenTicket.id)
            .where(KitchenTicket.tenant_id == tenant_id)):
        ticket_qty[(order_id, int(pid))] += int(q)
    covered = defaultdict(int)
    for oid, amount in s.execute(select(Payment.order_id, Payment.amount_cents).where(
            Payment.tenant_id == tenant_id, Payment.status.in_(SETTLING))):
        covered[oid] += int(amount)
    for o in s.scalars(select(Order).where(Order.tenant_id == tenant_id)):
        f.checked += 1
        for ln in o.lines:
            if ticket_qty.get((o.id, ln.product_id), 0) != ln.sent_quantity or ln.sent_quantity > ln.quantity:
                f.fail(order_id=o.id, product_id=ln.product_id, ordered=ln.quantity, sent=ln.sent_quantity,
                       on_tickets=ticket_qty.get((o.id, ln.product_id), 0))
        if o.status in ("paid", "invoiced", "closed") and o.sale_id is None:
            f.fail(order_id=o.id, status=o.status, reason="settled order without a sale")
        if o.sale_id is None and o.id in covered and covered[o.id] >= _order_total(s, tenant_id, o):
            f.fail(order_id=o.id, status=o.status, covered_cents=covered[o.id],
                   reason="bill fully covered by payments but no receipt issued")
        if o.sale_id is not None:
            sale = s.get(Sale, o.sale_id)
            want = sorted((ln.product_id, ln.quantity) for ln in o.lines if ln.quantity > 0)
            got = sorted((ln.product_id, ln.quantity) for ln in sale.lines) if sale else None
            if got != want:
                f.fail(order_id=o.id, sale_id=o.sale_id, order_lines=want, sale_lines=got)
    return f


def _sale_prices(s: Session, tenant_id: str) -> _Finding:
    """R14 (P1-1a): every sale line's unit price is the approved price that was
    valid for that outlet at the moment of the sale; every approved price was
    approved by someone other than its proposer (four eyes)."""
    from . import catalog
    from .models import PriceVersion

    f = _Finding("R14.sale_prices", "Müügihind = sel hetkel kehtinud kinnitatud hind; neli silma")
    products = {p.id: p for p in s.scalars(select(Product).where(Product.tenant_id == tenant_id))}
    cache: dict[tuple, int | None] = {}
    for sale in s.scalars(select(Sale).where(Sale.tenant_id == tenant_id).order_by(Sale.created_at)):
        loc = sale.register.location_id
        for ln in sale.lines:
            f.checked += 1
            key = (ln.product_id, loc, sale.created_at)
            if key not in cache:
                p = products.get(ln.product_id)
                cache[key] = catalog.resolve(s, tenant_id, p, loc, sale.created_at)[0] if p else None
            if cache[key] != ln.unit_price_cents:
                f.fail(sale_id=sale.id, line_no=ln.line_no, product_id=ln.product_id, location_id=loc,
                       charged_cents=ln.unit_price_cents, valid_price_cents=cache[key],
                       at=sale.created_at.isoformat())
    for v in s.scalars(select(PriceVersion).where(PriceVersion.tenant_id == tenant_id,
                                                  PriceVersion.status == "approved")):
        f.checked += 1
        if v.created_by == "system:legacy":
            continue
        if not v.decided_by or v.decided_by == v.created_by:
            f.fail(price_version_id=v.id, product_id=v.product_id, reason="four eyes broken",
                   proposed_by=v.created_by, approved_by=v.decided_by)
    return f


def _order_total(s: Session, tenant_id: str, o: Order) -> int:
    """Current price of an open bill (same pricing as the bill screen)."""
    from datetime import datetime, timezone

    from .payments import _lines

    if not o.lines:
        return 0
    now = datetime.now(timezone.utc)
    try:
        return sum(x.gross_cents for x in services._price_lines(s, tenant_id, _lines(o), now.date(), None,
                                                                location_id=o.location_id, at=now))
    except services.DomainError:  # e.g. a product no longer sold here: cannot be "fully covered"
        return 2 ** 62


def _receivables(s: Session, tenant_id: str) -> _Finding:
    """R12: a transfer receivable never collects more than it is for, and its
    status follows what was collected."""
    f = _Finding("R12.receivables", "Arve laekumine ≤ arve summa; staatus vastab")
    for inv in s.scalars(select(CustomerInvoice).where(CustomerInvoice.tenant_id == tenant_id)):
        f.checked += 1
        want = "paid" if inv.paid_cents == inv.amount_cents else ("partially_paid" if inv.paid_cents else "issued")
        if inv.paid_cents > inv.amount_cents or inv.status != want:
            f.fail(invoice_no=inv.invoice_no, amount_cents=inv.amount_cents, paid_cents=inv.paid_cents,
                   status=inv.status, expected_status=want)
    return f


def _sync_inbox(s: Session, tenant_id: str) -> _Finding:
    """R13 (central): every accepted till journal entry points to the
    document it created, and each document was created by one entry only.
    Quarantined entries wait for a manager (warning)."""
    f = _Finding("R13.offline_sync", "Kassade võrguühenduseta kirjed = keskserveri dokumendid")
    sales = set(s.scalars(select(Sale.id).where(Sale.tenant_id == tenant_id)))
    refunds = set(s.scalars(select(Refund.id).where(Refund.tenant_id == tenant_id)))
    seen = Counter()
    for row in s.scalars(select(DeviceInbox).where(DeviceInbox.tenant_id == tenant_id)):
        f.checked += 1
        if row.status in ("accepted", "resolved"):
            pool = refunds if row.entry_type == "refund" else sales
            if row.sale_id is None or row.sale_id not in pool:
                f.fail(device_id=row.device_id, seq=row.seq, entry_id=row.entry_id, document=row.sale_id,
                       reason="accepted entry without its document")
            else:
                seen[row.sale_id] += 1
        elif row.status == "quarantined":
            f.wait(device_id=row.device_id, seq=row.seq, entry_id=row.entry_id, reason=row.reason)
    for doc, n in seen.items():
        if n > 1:
            f.fail(document=doc, entries=n, reason="one document created by several till entries")
    return f


# ---------------------------------------------------------------------- trace


def trace_sale(s: Session, tenant_id: str, sale_id: str) -> dict:
    """Every register one sale touched, in causal order."""
    sale = s.scalar(select(Sale).where(Sale.tenant_id == tenant_id, Sale.id == sale_id))
    if sale is None:
        raise services.NotFound("sale not found")
    refunds = list(s.scalars(select(Refund).where(Refund.tenant_id == tenant_id, Refund.original_sale_id == sale_id)
                             .order_by(Refund.refund_no)))
    order = s.scalar(select(Order).where(Order.tenant_id == tenant_id, Order.sale_id == sale_id))
    tickets = list(s.scalars(select(KitchenTicket).where(KitchenTicket.tenant_id == tenant_id,
                                                         KitchenTicket.order_id == order.id)
                             .order_by(KitchenTicket.ticket_no))) if order else []
    pays = list(s.scalars(select(Payment).where(Payment.tenant_id == tenant_id, Payment.order_id == order.id)
                          .order_by(Payment.created_at))) if order else []
    invoices = list(s.scalars(select(CustomerInvoice).where(CustomerInvoice.tenant_id == tenant_id,
                                                            CustomerInvoice.sale_id == sale_id)))
    doc_ids = [sale_id, *(r.id for r in refunds)] + ([order.id] if order else []) + [t.id for t in tickets]
    evs = list(s.scalars(select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id,
                                                   OutboxEvent.aggregate_id.in_(doc_ids))
                         .order_by(OutboxEvent.recorded_seq)))
    applied = defaultdict(list)
    for e_id, consumer, at in s.execute(select(InboxEntry.event_id, InboxEntry.consumer, InboxEntry.processed_at)
                                        .where(InboxEntry.tenant_id == tenant_id,
                                               InboxEntry.event_id.in_([e.id for e in evs]))):
        applied[e_id].append({"consumer": consumer, "at": at.isoformat()})
    moves = list(s.scalars(select(StockMovement).where(StockMovement.tenant_id == tenant_id,
                                                       StockMovement.ref_id.in_([sale_id, *(r.id for r in refunds)]))
                           .order_by(StockMovement.id)))
    exports = list(s.scalars(select(ExportQueue).where(ExportQueue.tenant_id == tenant_id,
                                                       ExportQueue.event_id.in_([e.id for e in evs]))))
    journal = list(s.scalars(select(DeviceJournalEntry).where(DeviceJournalEntry.tenant_id == tenant_id,
                                                              DeviceJournalEntry.entity_id.in_(doc_ids))))
    inbox = list(s.scalars(select(DeviceInbox).where(DeviceInbox.tenant_id == tenant_id,
                                                     DeviceInbox.sale_id.in_([sale_id, *(r.id for r in refunds)]))))
    reg = s.get(Register, sale.register_id)
    return {
        "sale": {"id": sale.id, "receipt_no": sale.receipt_no, "receipt_label": sale.receipt_label,
                 "register": reg.code if reg else None, "drawer_session_id": sale.drawer_session_id,
                 "business_date": sale.business_date.isoformat(), "payment_method": sale.payment_method,
                 "total_cents": sale.total_cents, "idempotency_key": sale.idempotency_key,
                 "correlation_id": sale.correlation_id,
                 "lines": [{"line_no": ln.line_no, "sku": ln.sku, "quantity": ln.quantity,
                            "gross_cents": ln.gross_cents} for ln in sale.lines]},
        "order": ({"id": order.id, "status": order.status, "table_id": order.table_id,
                   "lines": [{"product_id": ln.product_id, "quantity": ln.quantity, "sent": ln.sent_quantity}
                             for ln in order.lines]} if order else None),
        "kitchen_tickets": [{"id": t.id, "ticket_no": t.ticket_no, "status": t.status,
                             "lines": [{"product_id": ln.product_id, "quantity": ln.quantity} for ln in t.lines]}
                            for t in tickets],
        "payments": [{"id": p.id, "method": p.method, "status": p.status, "amount_cents": p.amount_cents,
                      "rounding_cents": p.rounding_cents, "provider_ref": p.provider_ref,
                      "idempotency_key": p.idempotency_key} for p in pays],
        "receivables": [{"invoice_no": i.invoice_no, "reference_no": i.reference_no, "amount_cents": i.amount_cents,
                         "paid_cents": i.paid_cents, "status": i.status} for i in invoices],
        "refunds": [{"id": r.id, "refund_no": r.refund_no, "total_cents": r.total_cents,
                     "cash_paid_cents": r.cash_paid_cents, "approved_by": r.approved_by,
                     "idempotency_key": r.idempotency_key,
                     "lines": [{"sale_line_id": ln.sale_line_id, "quantity": ln.quantity} for ln in r.lines]}
                    for r in refunds],
        "events": [{"seq": e.recorded_seq, "type": e.type, "version": e.version, "aggregate": e.aggregate_type,
                    "aggregate_id": e.aggregate_id, "correlation_id": e.correlation_id,
                    "applied_by": sorted(applied.get(e.id, []), key=lambda x: x["consumer"])} for e in evs],
        "stock_movements": [{"kind": m.kind, "item_id": m.item_id, "warehouse_id": m.warehouse_id,
                             "qty_micro": m.qty_micro, "value_cents": m.value_cents, "cogs_cents": m.cogs_cents,
                             "ref_id": m.ref_id, "ref_line": m.ref_line} for m in moves],
        "accounting_export": [{"event_id": x.event_id, "event_type": x.event_type, "status": x.status,
                               "receipt_id": x.receipt_id} for x in exports],
        "device_journal": [{"stream": j.stream_id, "seq": j.seq, "entry_id": j.id, "type": j.entry_type}
                           for j in journal],
        "sync_inbox": [{"device_id": i.device_id, "seq": i.seq, "entry_id": i.entry_id, "status": i.status}
                       for i in inbox],
    }


# Every invariant, in report order. Tests and screens count rules from here.
RULES = (
    _drawers,
    _sale_totals,
    _tenders,
    _refunds,
    _numbering,
    _events_per_document,
    _event_pipeline,
    _books_export,
    _stock_consumption,
    _stock_ledger,
    _orders_and_kitchen,
    _receivables,
    _sync_inbox,
    _sale_prices,
)
