"""Inventory and costing (#14): supplier documents → stock → recipe/production
→ sale consumption → COGS, with reconciliation.

Rules (each has a test in ``tests/test_inventory.py``):
- Quantities are integer MICRO base units (``QTY_SCALE``); values are integer
  cents, net of recoverable VAT. No floats anywhere.
- Costing policy: moving weighted average (accepted by the owner 25 Nov, ADR-0011).
  Consuming ``q`` of ``Q`` on hand with value ``V`` costs
  ``half_up(V * q / Q)``; consuming everything costs exactly ``V`` (no
  rounding residue is ever left on an empty balance).
- Stock movements are append-only; a balance row is the locked running total
  of its movements (``reconcile`` proves it). Balances are locked FOR UPDATE
  in a fixed order (item id, warehouse id) to avoid deadlocks.
- A supplier document is posted once: unique (supplier, type, number) plus a
  draft -> posted compare-and-swap. A late invoice never re-posts quantities;
  it posts the price difference, split between the part still in stock and
  the part already consumed (COGS adjustment) — history is never rewritten.
- A menu offer consumes EITHER its recipe ingredients (made to order) OR a
  finished/resale item (e.g. batch-produced) — never both.
- Sales consume stock asynchronously via the ``stock.consume`` event consumer
  (block policy): a sale is never refused because of stock, and stock is
  never consumed twice (inbox + unique movement reference). Negative stock is
  allowed but flagged for reconciliation.
- Refunds do NOT restock automatically (food); explicit disposition later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    QTY_SCALE,
    Item,
    Location,
    Product,
    ProductionBatch,
    Recipe,
    RecipeLine,
    Register,
    StockBalance,
    StockMovement,
    Supplier,
    SupplierDocument,
    SupplierDocumentLine,
    Warehouse,
)
from .services import Conflict, DomainError, NotFound

ITEM_KINDS = ("ingredient", "finished", "resale", "packaging")
UOMS = ("kg", "l", "pcs")


def muldiv(a: int, b: int, c: int) -> int:
    """``a * b / c`` rounded half away from zero; exact integers, any sign."""
    if c <= 0:
        raise ValueError("divisor must be positive")
    n = a * b
    q, r = divmod(abs(n), c)
    if 2 * r >= c:
        q += 1
    return q if n >= 0 else -q


class InvalidInventory(DomainError):
    status_code = 422
    code = "invalid_inventory"


class DuplicateDocument(Conflict):
    code = "duplicate_document"


class AlreadyPosted(Conflict):
    code = "already_posted"


# ------------------------------------------------------------- master data


def create_item(s: Session, tenant_id: str, *, sku: str, name: str, kind: str, uom: str) -> Item:
    if kind not in ITEM_KINDS or uom not in UOMS:
        raise InvalidInventory("unknown item kind or unit")
    it = Item(tenant_id=tenant_id, sku=sku, name=name, kind=kind, uom=uom)
    s.add(it)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise Conflict("an item with this SKU exists") from None
    return it


def create_warehouse(s: Session, tenant_id: str, *, location_id: int, code: str, name: str,
                     is_default: bool) -> Warehouse:
    if s.scalar(select(Location.id).where(Location.id == location_id, Location.tenant_id == tenant_id)) is None:
        raise NotFound("location not found")
    if is_default:
        s.execute(update(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.location_id == location_id)
                  .values(is_default=False))
    w = Warehouse(tenant_id=tenant_id, location_id=location_id, code=code, name=name, is_default=is_default)
    s.add(w)
    s.commit()
    return w


def create_supplier(s: Session, tenant_id: str, *, code: str, name: str) -> Supplier:
    sp = Supplier(tenant_id=tenant_id, code=code, name=name)
    s.add(sp)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise Conflict("a supplier with this code exists") from None
    return sp


def _item(s: Session, tenant_id: str, item_id: int) -> Item:
    it = s.scalar(select(Item).where(Item.id == item_id, Item.tenant_id == tenant_id))
    if it is None:
        raise NotFound(f"item {item_id} not found")
    return it


def _warehouse(s: Session, tenant_id: str, warehouse_id: int) -> Warehouse:
    w = s.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == tenant_id))
    if w is None:
        raise NotFound("warehouse not found")
    return w


# ---------------------------------------------------------- balances (core)


def _lock_balances(s: Session, tenant_id: str, keys: list[tuple[int, int]]) -> dict[tuple[int, int], StockBalance]:
    """Get-or-create and lock balance rows, always in (item, warehouse) order."""
    out = {}
    ins = (postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert)(StockBalance)
    for item_id, wh_id in sorted(set(keys)):
        s.execute(ins.values(tenant_id=tenant_id, item_id=item_id, warehouse_id=wh_id, qty_micro=0, value_cents=0,
                             last_unit_cost_micro=0).on_conflict_do_nothing())
        out[(item_id, wh_id)] = s.scalar(
            select(StockBalance).where(StockBalance.item_id == item_id, StockBalance.warehouse_id == wh_id,
                                       StockBalance.tenant_id == tenant_id)
            .with_for_update().execution_options(populate_existing=True))
    return out


def _unit_cost_micro(value_cents: int, qty_micro: int) -> int:
    """Average cost in cents per BASE unit, scaled by 1e6 (e.g. 5 €/kg -> 500_000_000).
    cents/unit = value / (qty_micro / 1e6); scaled by 1e6 -> value * 1e12 / qty_micro."""
    return muldiv(value_cents * QTY_SCALE * QTY_SCALE, 1, qty_micro) if qty_micro > 0 else 0


def _move(s: Session, bal: StockBalance, *, kind: str, qty: int, value: int, cogs: int = 0, ref_type: str,
          ref_id: str, ref_line: int, bdate: date, now: datetime, negative: bool = False) -> StockMovement:
    bal.qty_micro += qty
    bal.value_cents += value
    if bal.qty_micro > 0:
        bal.last_unit_cost_micro = _unit_cost_micro(bal.value_cents, bal.qty_micro)
    m = StockMovement(tenant_id=bal.tenant_id, item_id=bal.item_id, warehouse_id=bal.warehouse_id, kind=kind,
                      qty_micro=qty, value_cents=value, cogs_cents=cogs, ref_type=ref_type, ref_id=ref_id,
                      ref_line=ref_line, negative_stock=negative, business_date=bdate, created_at=now)
    s.add(m)
    return m


def _consume(s: Session, bal: StockBalance, qty: int, *, kind: str, ref_type: str, ref_id: str, ref_line: int,
             bdate: date, now: datetime, to_cogs: bool) -> int:
    """Remove ``qty`` at moving-average cost; returns the cost (cents)."""
    if qty <= 0:
        raise InvalidInventory("quantity must be positive")
    on_hand, value = bal.qty_micro, bal.value_cents
    if on_hand >= qty:
        cost = value if on_hand == qty else muldiv(value, qty, on_hand)
        negative = False
    else:  # allowed, flagged: the available part at average, the rest at last known cost
        cost = _negative_cost(bal, qty)
        negative = True
    _move(s, bal, kind=kind, qty=-qty, value=-cost, cogs=cost if to_cogs else 0, ref_type=ref_type, ref_id=ref_id,
          ref_line=ref_line, bdate=bdate, now=now, negative=negative)
    return cost


def _negative_cost(bal: StockBalance, qty: int) -> int:
    avail = max(bal.qty_micro, 0)
    cost = bal.value_cents if avail > 0 else 0
    short = qty - avail
    # last known unit cost (cents per base unit * 1e6) * short (micro units) / 1e12
    cost += muldiv(bal.last_unit_cost_micro, short, QTY_SCALE * QTY_SCALE)
    return cost


def _stock_changed(s: Session, tenant_id: str, warehouse_id: int, item_ids, ref_type: str, ref_id: str,
                   now: datetime) -> None:
    """Realtime invalidation (same transaction as the movements): screens
    showing these items re-read them through the API."""
    from . import events

    wh = s.get(Warehouse, warehouse_id)
    events.emit(s, tenant_id=tenant_id, type="stock.changed", payload={
        "warehouse_id": warehouse_id, "item_ids": sorted(set(item_ids)), "ref_type": ref_type, "ref_id": ref_id},
        aggregate_type="stock_change", aggregate_id=f"{ref_type}:{ref_id}"[:36], aggregate_version=1, now=now,
        location_id=wh.location_id if wh else None)


# ------------------------------------------------------- supplier documents


@dataclass
class DocLine:
    item_id: int
    qty_micro: int
    net_cents: int
    vat_rate_bp: int
    vat_recoverable: bool = True


def create_document(s: Session, tenant_id: str, *, supplier_id: int, doc_type: str, doc_no: str, doc_date: date,
                    warehouse_id: int, lines: list[DocLine], freight_cents: int, actor: str, now: datetime,
                    matches_document_id: str | None = None) -> SupplierDocument:
    if doc_type not in ("delivery_note", "invoice"):
        raise InvalidInventory("doc_type must be delivery_note or invoice")
    if s.scalar(select(Supplier.id).where(Supplier.id == supplier_id, Supplier.tenant_id == tenant_id)) is None:
        raise NotFound("supplier not found")
    _warehouse(s, tenant_id, warehouse_id)
    if not lines or freight_cents < 0:
        raise InvalidInventory("a document needs lines; freight cannot be negative")
    for ln in lines:
        _item(s, tenant_id, ln.item_id)
        if ln.qty_micro <= 0 or ln.net_cents < 0 or not 0 <= ln.vat_rate_bp <= 10000:
            raise InvalidInventory("invalid line quantity, amount or VAT rate")
    if matches_document_id is not None:
        base = s.scalar(select(SupplierDocument).where(SupplierDocument.id == matches_document_id,
                                                       SupplierDocument.tenant_id == tenant_id))
        if base is None or base.doc_type != "delivery_note" or base.supplier_id != supplier_id:
            raise InvalidInventory("an invoice can only match a delivery note of the same supplier")
        if s.scalar(select(SupplierDocument.id).where(SupplierDocument.tenant_id == tenant_id,
                                                      SupplierDocument.matches_document_id == matches_document_id)):
            raise DuplicateDocument("this delivery note already has an invoice")
    doc = SupplierDocument(id=str(uuid.uuid4()), tenant_id=tenant_id, supplier_id=supplier_id, doc_type=doc_type,
                           doc_no=doc_no.strip(), doc_date=doc_date, warehouse_id=warehouse_id, status="draft",
                           freight_cents=freight_cents, created_by=actor, created_at=now,
                           matches_document_id=matches_document_id)
    doc.lines = [SupplierDocumentLine(tenant_id=tenant_id, line_no=i, item_id=ln.item_id, qty_micro=ln.qty_micro,
                                      net_cents=ln.net_cents, vat_rate_bp=ln.vat_rate_bp,
                                      vat_recoverable=ln.vat_recoverable)
                 for i, ln in enumerate(lines, start=1)]
    s.add(doc)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise DuplicateDocument("this supplier document number is already registered") from None
    return doc


def _line_costs(doc: SupplierDocument) -> list[int]:
    """Stock cost per line: net (+ non-recoverable VAT) + freight allocated by
    net value with the largest-remainder method (sums exactly)."""
    base = []
    for ln in doc.lines:
        cost = ln.net_cents
        if not ln.vat_recoverable:
            cost += muldiv(ln.net_cents, ln.vat_rate_bp, 10000)
        base.append(cost)
    total = sum(ln.net_cents for ln in doc.lines)
    if not doc.freight_cents or total == 0:
        return base
    shares = [doc.freight_cents * ln.net_cents // total for ln in doc.lines]
    rema = sorted(range(len(shares)), key=lambda i: -(doc.freight_cents * doc.lines[i].net_cents % total))
    for i in rema[: doc.freight_cents - sum(shares)]:
        shares[i] += 1
    return [b + f for b, f in zip(base, shares)]


def post_document(s: Session, tenant_id: str, document_id: str, *, actor: str, now: datetime) -> SupplierDocument:
    """draft -> posted exactly once (compare-and-swap), then stock effects."""
    doc = s.scalar(select(SupplierDocument).where(SupplierDocument.id == document_id,
                                                  SupplierDocument.tenant_id == tenant_id))
    if doc is None:
        raise NotFound("document not found")
    res = s.execute(update(SupplierDocument).where(SupplierDocument.id == document_id, SupplierDocument.status == "draft")
                    .values(status="posted", posted_by=actor, posted_at=now)
                    .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        raise AlreadyPosted("this document is already posted")
    bdate = doc.doc_date
    costs = _line_costs(doc)
    # Invoice-based flow (owner decision): an invoice WITHOUT a delivery note is
    # the receipt itself. A delivery note is optional; when an invoice matches
    # a posted delivery note, only the price difference is posted.
    if doc.doc_type == "delivery_note" or doc.matches_document_id is None:
        bals = _lock_balances(s, tenant_id, [(ln.item_id, doc.warehouse_id) for ln in doc.lines])
        for ln, cost in zip(doc.lines, costs):
            _move(s, bals[(ln.item_id, doc.warehouse_id)], kind="receipt", qty=ln.qty_micro, value=cost,
                  ref_type="supplier_document", ref_id=doc.id, ref_line=ln.line_no, bdate=bdate, now=now)
    else:
        _post_invoice(s, tenant_id, doc, costs, bdate, now)
    _stock_changed(s, tenant_id, doc.warehouse_id, [ln.item_id for ln in doc.lines], "supplier_document", doc.id, now)
    s.commit()
    return s.scalar(select(SupplierDocument).where(SupplierDocument.id == document_id)
                    .execution_options(populate_existing=True))


def _post_invoice(s: Session, tenant_id: str, inv: SupplierDocument, costs: list[int], bdate: date,
                  now: datetime) -> None:
    """An invoice for an already received delivery note: quantities are NOT
    posted again. The cost difference per item is split by the share of the
    received quantity still on hand (stock) vs already consumed (COGS)."""
    note = s.scalar(select(SupplierDocument).where(SupplierDocument.id == inv.matches_document_id))
    if note.status != "posted":
        raise InvalidInventory("post the delivery note first")
    received = {}
    for ln, cost in zip(note.lines, _line_costs(note)):
        q, c = received.get(ln.item_id, (0, 0))
        received[ln.item_id] = (q + ln.qty_micro, c + cost)
    invoiced = {}
    for ln, cost in zip(inv.lines, costs):
        q, c = invoiced.get(ln.item_id, (0, 0))
        invoiced[ln.item_id] = (q + ln.qty_micro, c + cost)
    if {k: v[0] for k, v in invoiced.items()} != {k: v[0] for k, v in received.items()}:
        raise InvalidInventory("invoice quantities differ from the delivery note (credit note flow not built yet)")
    bals = _lock_balances(s, tenant_id, [(i, inv.warehouse_id) for i in invoiced])
    for item_id in sorted(invoiced):
        diff = invoiced[item_id][1] - received[item_id][1]
        if diff == 0:
            continue
        bal = bals[(item_id, inv.warehouse_id)]
        rq = received[item_id][0]
        still = min(max(bal.qty_micro, 0), rq)
        to_stock = muldiv(diff, still, rq) if still < rq else diff
        to_cogs = diff - to_stock
        if to_stock:
            _move(s, bal, kind="invoice_adjustment", qty=0, value=to_stock, ref_type="supplier_document",
                  ref_id=inv.id, ref_line=0, bdate=bdate, now=now)
        if to_cogs:
            _move(s, bal, kind="cogs_adjustment", qty=0, value=0, cogs=to_cogs, ref_type="supplier_document",
                  ref_id=inv.id, ref_line=1, bdate=bdate, now=now)


def return_to_supplier(s: Session, tenant_id: str, document_id: str, *, item_id: int, qty_micro: int,
                       return_no: str, now: datetime) -> StockMovement:
    """Partial return of received goods at current average cost."""
    doc = s.scalar(select(SupplierDocument).where(SupplierDocument.id == document_id,
                                                  SupplierDocument.tenant_id == tenant_id))
    if doc is None or doc.status != "posted" or (doc.doc_type == "invoice" and doc.matches_document_id):
        raise InvalidInventory("returns are made against the posted receipt document")
    received = sum(ln.qty_micro for ln in doc.lines if ln.item_id == item_id)
    returned = s.scalar(select(func.coalesce(func.sum(-StockMovement.qty_micro), 0)).where(
        StockMovement.tenant_id == tenant_id, StockMovement.kind == "supplier_return",
        StockMovement.ref_type == "supplier_document", StockMovement.ref_id == document_id,
        StockMovement.item_id == item_id)) or 0
    if qty_micro <= 0 or returned + qty_micro > received:
        raise InvalidInventory("cannot return more than was received on this document")
    bal = _lock_balances(s, tenant_id, [(item_id, doc.warehouse_id)])[(item_id, doc.warehouse_id)]
    n = s.scalar(select(func.count()).select_from(StockMovement).where(
        StockMovement.tenant_id == tenant_id, StockMovement.kind == "supplier_return",
        StockMovement.ref_id == document_id)) or 0
    _consume(s, bal, qty_micro, kind="supplier_return", ref_type="supplier_document", ref_id=document_id,
             ref_line=1000 + n, bdate=now.date(), now=now, to_cogs=False)
    _stock_changed(s, tenant_id, doc.warehouse_id, [item_id], "supplier_return", document_id, now)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise Conflict("concurrent return; retry") from None
    return s.scalar(select(StockMovement).where(StockMovement.ref_id == document_id,
                                                StockMovement.ref_line == 1000 + n))


# ------------------------------------------------------------- recipes


def set_recipe(s: Session, tenant_id: str, *, product_id: int | None, output_item_id: int | None,
               lines: list[tuple[int, int]], yield_micro: int, valid_from: date, now: datetime) -> Recipe:
    """New version of a menu-offer recipe (per portion) or a batch recipe."""
    if (product_id is None) == (output_item_id is None):
        raise InvalidInventory("a recipe is either for a menu offer or produces an item")
    if not lines or yield_micro <= 0 or any(q <= 0 for _, q in lines):
        raise InvalidInventory("recipe lines and yield must be positive")
    for item_id, _ in lines:
        _item(s, tenant_id, item_id)
        if output_item_id is not None and item_id == output_item_id:
            raise InvalidInventory("a recipe cannot consume its own output")
    if product_id is not None:
        p = s.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
        if p is None:
            raise NotFound("product not found")
        if p.stock_mode == "item":
            raise InvalidInventory("this offer sells a stocked item; it cannot also consume a recipe")
        p.stock_mode = "recipe"
        key = Recipe.product_id == product_id
    else:
        _item(s, tenant_id, output_item_id)
        key = Recipe.output_item_id == output_item_id
    version = (s.scalar(select(func.coalesce(func.max(Recipe.version), 0)).where(
        Recipe.tenant_id == tenant_id, key)) or 0) + 1
    r = Recipe(tenant_id=tenant_id, product_id=product_id, output_item_id=output_item_id, version=version,
               valid_from=valid_from, yield_micro=yield_micro, created_at=now)
    r.lines = [RecipeLine(tenant_id=tenant_id, item_id=i, qty_micro=q) for i, q in lines]
    s.add(r)
    s.commit()
    return r


def sell_item(s: Session, tenant_id: str, *, product_id: int, item_id: int) -> Product:
    """The offer consumes ONE unit of a finished/resale item per sale unit."""
    p = s.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    if p is None:
        raise NotFound("product not found")
    it = _item(s, tenant_id, item_id)
    if it.kind not in ("finished", "resale"):
        raise InvalidInventory("only finished or resale items can be sold directly")
    p.stock_mode, p.stock_item_id = "item", item_id
    s.commit()
    return p


def recipe_for(s: Session, tenant_id: str, *, product_id: int | None = None, output_item_id: int | None = None,
               on: date) -> Recipe | None:
    key = Recipe.product_id == product_id if product_id is not None else Recipe.output_item_id == output_item_id
    return s.scalar(select(Recipe).where(Recipe.tenant_id == tenant_id, key, Recipe.valid_from <= on)
                    .order_by(Recipe.valid_from.desc(), Recipe.version.desc()).limit(1))


def recipe_cost(s: Session, tenant_id: str, recipe: Recipe, warehouse_id: int) -> int:
    """Theoretical cost of one recipe batch/portion at current average costs."""
    total = 0
    for ln in recipe.lines:
        bal = s.get(StockBalance, (ln.item_id, warehouse_id))
        unit = bal.last_unit_cost_micro if bal else 0
        total += muldiv(unit, ln.qty_micro, QTY_SCALE * QTY_SCALE)
    return total


# ----------------------------------------------------------- production


def produce(s: Session, tenant_id: str, *, output_item_id: int, warehouse_id: int, batches: int,
            idempotency_key: str, actor: str, now: datetime) -> ProductionBatch:
    """Consume ingredients and book the output at exactly their cost, in one
    transaction. Idempotent per key."""
    existing = s.scalar(select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id,
                                                      ProductionBatch.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    if batches <= 0:
        raise InvalidInventory("batches must be positive")
    _warehouse(s, tenant_id, warehouse_id)
    recipe = recipe_for(s, tenant_id, output_item_id=output_item_id, on=now.date())
    if recipe is None:
        raise InvalidInventory("no batch recipe for this item")
    batch_id = str(uuid.uuid4())
    keys = [(ln.item_id, warehouse_id) for ln in recipe.lines] + [(output_item_id, warehouse_id)]
    bals = _lock_balances(s, tenant_id, keys)
    cost = 0
    for i, ln in enumerate(recipe.lines, start=1):
        cost += _consume(s, bals[(ln.item_id, warehouse_id)], ln.qty_micro * batches, kind="production_consumption",
                         ref_type="production", ref_id=batch_id, ref_line=i, bdate=now.date(), now=now,
                         to_cogs=False)
    out_qty = recipe.yield_micro * batches
    _move(s, bals[(output_item_id, warehouse_id)], kind="production_output", qty=out_qty, value=cost,
          ref_type="production", ref_id=batch_id, ref_line=0, bdate=now.date(), now=now)
    b = ProductionBatch(id=batch_id, tenant_id=tenant_id, recipe_id=recipe.id, warehouse_id=warehouse_id,
                        output_item_id=output_item_id, output_qty_micro=out_qty, cost_cents=cost,
                        idempotency_key=idempotency_key, created_by=actor, created_at=now)
    s.add(b)
    _stock_changed(s, tenant_id, warehouse_id, [k[0] for k in keys], "production", batch_id, now)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        again = s.scalar(select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id,
                                                       ProductionBatch.idempotency_key == idempotency_key))
        if again is None:
            raise
        return again
    return b


# ------------------------------------------------- sale consumption (consumer)


def consume_for_sale(s: Session, tenant_id: str, *, sale_id: str, register_id: int, business_date: date,
                     lines: list[tuple[str, int]], now: datetime) -> int:
    """Called by the ``stock.consume`` consumer inside its transaction.
    ``lines`` = [(sku, quantity)]. Returns the COGS posted (cents)."""
    reg = s.scalar(select(Register).where(Register.id == register_id, Register.tenant_id == tenant_id))
    wh = s.scalar(select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.location_id == reg.location_id,
                                          Warehouse.is_default.is_(True))) if reg else None
    plan: list[tuple[int, int, int]] = []  # (line_no, item_id, qty_micro)
    for line_no, (sku, qty) in enumerate(lines, start=1):
        p = s.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.sku == sku))
        if p is None or p.stock_mode == "none":
            continue
        if wh is None:
            raise InvalidInventory(f"no default warehouse for the location of register {register_id}")
        if p.stock_mode == "item":
            plan.append((line_no * 100, p.stock_item_id, qty * QTY_SCALE))
        else:
            r = recipe_for(s, tenant_id, product_id=p.id, on=business_date)
            if r is None:
                raise InvalidInventory(f"no recipe valid on {business_date} for {sku}")
            for j, rl in enumerate(r.lines, start=1):
                plan.append((line_no * 100 + j, rl.item_id, rl.qty_micro * qty))
    if not plan:
        return 0
    bals = _lock_balances(s, tenant_id, [(item_id, wh.id) for _, item_id, _ in plan])
    total = 0
    for ref_line, item_id, q in plan:
        total += _consume(s, bals[(item_id, wh.id)], q, kind="sale_consumption", ref_type="sale", ref_id=sale_id,
                          ref_line=ref_line, bdate=business_date, now=now, to_cogs=True)
    _stock_changed(s, tenant_id, wh.id, [i for _, i, _ in plan], "sale", sale_id, now)
    return total


# ------------------------------------------------------ reports & reconciliation


def stock(s: Session, tenant_id: str) -> list[dict]:
    rows = s.execute(select(StockBalance, Item).join(Item, Item.id == StockBalance.item_id)
                     .where(StockBalance.tenant_id == tenant_id).order_by(Item.sku, StockBalance.warehouse_id))
    return [{"item_id": it.id, "sku": it.sku, "name": it.name, "uom": it.uom, "warehouse_id": b.warehouse_id,
             "qty_micro": b.qty_micro, "value_cents": b.value_cents,
             "unit_cost_micro": b.last_unit_cost_micro, "negative": b.qty_micro < 0} for b, it in rows]


def stock_card(s: Session, tenant_id: str, item_id: int) -> list[dict]:
    _item(s, tenant_id, item_id)
    rows = s.scalars(select(StockMovement).where(StockMovement.tenant_id == tenant_id,
                                                 StockMovement.item_id == item_id).order_by(StockMovement.id))
    return [{"id": m.id, "kind": m.kind, "qty_micro": m.qty_micro, "value_cents": m.value_cents,
             "cogs_cents": m.cogs_cents, "ref_type": m.ref_type, "ref_id": m.ref_id, "ref_line": m.ref_line,
             "negative_stock": m.negative_stock, "business_date": m.business_date.isoformat(),
             "warehouse_id": m.warehouse_id} for m in rows]


def cogs(s: Session, tenant_id: str, business_date: date | None = None) -> int:
    q = select(func.coalesce(func.sum(StockMovement.cogs_cents), 0)).where(StockMovement.tenant_id == tenant_id)
    if business_date is not None:
        q = q.where(StockMovement.business_date == business_date)
    return s.scalar(q) or 0


def reconcile(s: Session, tenant_id: str) -> dict:
    """Every balance must equal the sum of its movements; flags negative stock."""
    sums = {(i, w): (q, v) for i, w, q, v in s.execute(
        select(StockMovement.item_id, StockMovement.warehouse_id, func.sum(StockMovement.qty_micro),
               func.sum(StockMovement.value_cents))
        .where(StockMovement.tenant_id == tenant_id).group_by(StockMovement.item_id, StockMovement.warehouse_id))}
    mismatches, negative = [], []
    for b in s.scalars(select(StockBalance).where(StockBalance.tenant_id == tenant_id)):
        q, v = sums.get((b.item_id, b.warehouse_id), (0, 0))
        if (int(q), int(v)) != (b.qty_micro, b.value_cents):
            mismatches.append({"item_id": b.item_id, "warehouse_id": b.warehouse_id,
                               "balance": [b.qty_micro, b.value_cents], "movements": [int(q), int(v)]})
        if b.qty_micro < 0:
            negative.append({"item_id": b.item_id, "warehouse_id": b.warehouse_id, "qty_micro": b.qty_micro})
    value = s.scalar(select(func.coalesce(func.sum(StockBalance.value_cents), 0))
                     .where(StockBalance.tenant_id == tenant_id)) or 0
    return {"ok": not mismatches, "mismatches": mismatches, "negative_stock": negative,
            "stock_value_cents": value, "cogs_cents": cogs(s, tenant_id)}


# ------------------------------------------------------------- read models for the UI


def document_total(doc: SupplierDocument) -> dict:
    net = sum(ln.net_cents for ln in doc.lines)
    vat = sum(muldiv(ln.net_cents, ln.vat_rate_bp, 10000) for ln in doc.lines)
    return {"net_cents": net, "vat_cents": vat, "gross_cents": net + vat, "freight_cents": doc.freight_cents}


def list_documents(s: Session, tenant_id: str, status: str | None = None) -> list[SupplierDocument]:
    q = select(SupplierDocument).where(SupplierDocument.tenant_id == tenant_id)
    if status:
        q = q.where(SupplierDocument.status == status)
    return list(s.scalars(q.order_by(SupplierDocument.status, SupplierDocument.created_at.desc()).limit(200)))


def offer_gross_price(s: Session, tenant_id: str, product: Product, on: date) -> int:
    """Base price valid at the end of ``on`` (catalog.resolve, P1-1a); 0 if none is approved yet."""
    from datetime import datetime, time, timezone

    from . import catalog

    at = datetime.combine(on, time(23, 59, 59), tzinfo=timezone.utc)
    return catalog.resolve(s, tenant_id, product, None, at)[0] or 0


def offer_net_price(s: Session, tenant_id: str, product: Product, on: date) -> int:
    from .money import vat_from_gross
    from .services import tax_rate_for

    gross = offer_gross_price(s, tenant_id, product, on)
    return gross - vat_from_gross(gross, tax_rate_for(s, product.tax_category, on))


def preview_recipe(s: Session, tenant_id: str, *, product_id: int | None, lines: list[tuple[int, int]],
                   yield_micro: int, on: date) -> dict:
    """Live cost while editing: cost per portion at current average costs,
    food cost % of the NET selling price, and contribution margin."""
    if yield_micro <= 0:
        raise InvalidInventory("yield must be positive")
    wh_costs = {}
    for b in s.scalars(select(StockBalance).where(StockBalance.tenant_id == tenant_id)):
        if b.last_unit_cost_micro:
            wh_costs.setdefault(b.item_id, b.last_unit_cost_micro)  # first warehouse with a cost
    out_lines, total = [], 0
    for item_id, qty in lines:
        it = _item(s, tenant_id, item_id)
        unit = wh_costs.get(item_id, 0)
        cost = muldiv(unit, qty, QTY_SCALE * QTY_SCALE)
        total += cost
        out_lines.append({"item_id": item_id, "name": it.name, "uom": it.uom, "qty_micro": qty,
                          "unit_cost_micro": unit, "cost_cents": cost, "no_cost_yet": unit == 0})
    per_unit = muldiv(total, QTY_SCALE, yield_micro)
    res = {"lines": out_lines, "batch_cost_cents": total, "cost_per_unit_cents": per_unit}
    if product_id is not None:
        p = s.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
        if p is None:
            raise NotFound("product not found")
        net = offer_net_price(s, tenant_id, p, on)
        res.update({"price_gross_cents": offer_gross_price(s, tenant_id, p, on), "price_net_cents": net,
                    "food_cost_bp": muldiv(per_unit, 10000, net) if net else None,
                    "margin_cents": net - per_unit})
    return res
