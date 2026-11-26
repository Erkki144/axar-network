"""Catalog and pricing (P1-1a, ADR-0015, docs/discovery/catalog.md §8).

Registers owned here (single writer):
- product groups (tree), the product card and barcodes;
- price lists (one per outlet: ``Location.price_list_id``);
- price versions: every price is a row with ``valid_from``; a change is a
  new row, never an edit. draft -> approved | rejected | withdrawn.

Price resolution (``price_for``), the ONLY place a sale price comes from:
1. the outlet's price list: latest approved version valid at ``at``.
   ``price_cents`` NULL there = "not sold at this outlet" (NotSoldHere);
2. otherwise the product's base price: latest approved base version;
3. otherwise, only for a product that has never had any price version, the
   legacy ``Product.price_cents`` (data created before P1-1a / demo seed).
A product whose only versions are undecided drafts has no price yet and is
not sold anywhere.

Four eyes (owner answer 26.11.2026): the person who proposes a price can
never approve it. Approving needs ``pricing.approve``.

Every change emits an event in the same transaction:
``catalog.group.changed``, ``catalog.product.changed``, ``pricing.price.published``,
``pricing.outlet.changed``. Tills re-read the catalog on these (SSE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events
from .models import (
    Location,
    Order,
    OrderLine,
    PriceList,
    PriceVersion,
    Product,
    ProductBarcode,
    ProductGroup,
)
from .services import Conflict, DomainError, Forbidden, NotFound, tax_rate_for

PRODUCT_KINDS = ("standard", "system", "gift_card")
STOCK_MODES = ("none", "item", "recipe")
TAX_CATEGORIES = ("standard", "reduced", "zero")
MAX_PRICE_CENTS = 10_000_000  # 100 000 € per unit: a typo guard, not a business rule


class NotSoldHere(DomainError):
    status_code = 409


class InvalidCatalog(DomainError):
    status_code = 422


class FourEyes(Forbidden):
    """The proposer of a price may not approve it."""


# ------------------------------------------------------------------ pricing


def _latest_approved(s: Session, tenant_id: str, product_id: int, price_list_id: int | None,
                     at: datetime) -> PriceVersion | None:
    q = select(PriceVersion).where(
        PriceVersion.tenant_id == tenant_id, PriceVersion.product_id == product_id,
        PriceVersion.status == "approved", PriceVersion.valid_from <= at,
        PriceVersion.price_list_id.is_(None) if price_list_id is None
        else PriceVersion.price_list_id == price_list_id,
    ).order_by(PriceVersion.valid_from.desc(), PriceVersion.decided_at.desc()).limit(1)
    return s.scalar(q)


LEGACY_FROM = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _legacy_applies(s: Session, tenant_id: str, product: Product) -> bool:
    """A product from before P1-1a (seeded / migrated) sells at its legacy
    ``price_cents`` until its first base version is approved. Products made
    through the catalog start at 0 with a draft and never use this path."""
    return product.price_cents > 0 and s.scalar(select(PriceVersion.id).where(
        PriceVersion.tenant_id == tenant_id, PriceVersion.product_id == product.id,
        PriceVersion.price_list_id.is_(None), PriceVersion.status == "approved").limit(1)) is None


def _outlet_list(s: Session, tenant_id: str, location_id: int | None) -> int | None:
    if location_id is None:
        return None
    return s.scalar(select(Location.price_list_id).where(Location.tenant_id == tenant_id, Location.id == location_id))


def resolve(s: Session, tenant_id: str, product: Product, location_id: int | None,
            at: datetime) -> tuple[int | None, str, str | None]:
    """``(price_cents | None, source, version_id)``; source = list | base | legacy | none.
    ``None`` price = not sold at this outlet (or no approved price yet)."""
    list_id = _outlet_list(s, tenant_id, location_id)
    if list_id is not None:
        v = _latest_approved(s, tenant_id, product.id, list_id, at)
        if v is not None:
            return v.price_cents, "list", v.id
    v = _latest_approved(s, tenant_id, product.id, None, at)
    if v is not None:
        return v.price_cents, "base", v.id
    if _legacy_applies(s, tenant_id, product):
        return product.price_cents, "legacy", None
    return None, "none", None


def price_for(s: Session, tenant_id: str, product: Product, location_id: int | None, at: datetime) -> int:
    price, _src, _vid = resolve(s, tenant_id, product, location_id, at)
    if price is None:
        raise NotSoldHere(f"product {product.sku} is not sold here (no approved price)")
    return price


def till_catalog(s: Session, tenant_id: str, location_id: int | None, at: datetime) -> list[dict]:
    """What a till may sell: active, not hidden, with an approved price here."""
    groups = {g.id: g.name for g in s.scalars(select(ProductGroup).where(ProductGroup.tenant_id == tenant_id))}
    out = []
    for p in s.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.active.is_(True),
                                             Product.hidden_at_till.is_(False)).order_by(Product.name)):
        price, _src, _vid = resolve(s, tenant_id, p, location_id, at)
        if price is None:
            continue
        out.append({"id": p.id, "sku": p.sku, "name": p.name, "short_name": p.short_name,
                    "price_cents": price, "tax_category": p.tax_category,
                    "category": groups.get(p.group_id) or p.category, "version": p.version})
    return out


# ------------------------------------------------------------------ groups


def _group(s: Session, tenant_id: str, group_id: int) -> ProductGroup:
    g = s.scalar(select(ProductGroup).where(ProductGroup.tenant_id == tenant_id, ProductGroup.id == group_id))
    if g is None:
        raise NotFound(f"product group {group_id} not found")
    return g


def list_groups(s: Session, tenant_id: str) -> list[dict]:
    counts = dict(s.execute(select(Product.group_id, func.count(Product.id))
                            .where(Product.tenant_id == tenant_id, Product.active.is_(True))
                            .group_by(Product.group_id)).all())
    return [{"id": g.id, "parent_id": g.parent_id, "name": g.name, "sort": g.sort, "active": g.active,
             "product_count": counts.get(g.id, 0)}
            for g in s.scalars(select(ProductGroup).where(ProductGroup.tenant_id == tenant_id)
                               .order_by(ProductGroup.sort, ProductGroup.name))]


def _emit_group(s: Session, g: ProductGroup, now: datetime, change: str) -> None:
    events.emit(s, tenant_id=g.tenant_id, type="catalog.group.changed",
                payload={"group_id": g.id, "parent_id": g.parent_id, "name": g.name, "active": g.active,
                         "change": change},
                aggregate_type="product_group", aggregate_id=str(g.id), aggregate_version=1, now=now)


def save_group(s: Session, tenant_id: str, *, now: datetime, name: str, parent_id: int | None = None,
               sort: int = 0, active: bool = True, group_id: int | None = None) -> ProductGroup:
    name = (name or "").strip()
    if not name:
        raise InvalidCatalog("group name is required")
    if parent_id is not None:
        parent = _group(s, tenant_id, parent_id)
        # No cycles: walk up from the new parent; the group itself must not appear.
        node = parent
        while node is not None:
            if group_id is not None and node.id == group_id:
                raise InvalidCatalog("a group cannot be moved under itself")
            node = s.get(ProductGroup, node.parent_id) if node.parent_id else None
    if group_id is None:
        g = ProductGroup(tenant_id=tenant_id, name=name[:100], parent_id=parent_id, sort=sort, active=active)
        s.add(g)
        s.flush()
        change = "created"
    else:
        g = _group(s, tenant_id, group_id)
        if not active and g.active:
            n = s.scalar(select(func.count(Product.id)).where(Product.tenant_id == tenant_id,
                                                              Product.group_id == g.id, Product.active.is_(True)))
            if n:
                raise Conflict(f"the group still has {n} active products; move or deactivate them first")
        g.name, g.parent_id, g.sort, g.active = name[:100], parent_id, sort, active
        change = "updated"
    _emit_group(s, g, now, change)
    return g


# ------------------------------------------------------------------ products


def _product(s: Session, tenant_id: str, product_id: int) -> Product:
    p = s.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.id == product_id))
    if p is None:
        raise NotFound(f"product {product_id} not found")
    return p


def _barcodes(s: Session, tenant_id: str, product_id: int) -> list[str]:
    return list(s.scalars(select(ProductBarcode.code).where(ProductBarcode.tenant_id == tenant_id,
                                                           ProductBarcode.product_id == product_id)
                          .order_by(ProductBarcode.id)))


def product_card(s: Session, tenant_id: str, product_id: int, now: datetime) -> dict:
    p = _product(s, tenant_id, product_id)
    lists = list(s.scalars(select(PriceList).where(PriceList.tenant_id == tenant_id).order_by(PriceList.name)))
    base = _latest_approved(s, tenant_id, p.id, None, now)
    prices = [{"price_list_id": None, "name": "Põhihind", "price_cents": base.price_cents if base else (
        p.price_cents if _legacy_applies(s, tenant_id, p) else None),
               "version_id": base.id if base else None}]
    for pl in lists:
        v = _latest_approved(s, tenant_id, p.id, pl.id, now)
        prices.append({"price_list_id": pl.id, "name": pl.name, "override": v is not None,
                       "price_cents": v.price_cents if v else None, "version_id": v.id if v else None})
    return {**_product_out(p), "barcodes": _barcodes(s, tenant_id, p.id), "prices": prices,
            "pending": [version_out(v) for v in s.scalars(
                select(PriceVersion).where(PriceVersion.tenant_id == tenant_id, PriceVersion.product_id == p.id,
                                           PriceVersion.status == "draft").order_by(PriceVersion.created_at))],
            "history": [version_out(v) for v in s.scalars(
                select(PriceVersion).where(PriceVersion.tenant_id == tenant_id, PriceVersion.product_id == p.id,
                                           PriceVersion.status != "draft")
                .order_by(PriceVersion.valid_from.desc(), PriceVersion.decided_at.desc()).limit(50))]}


def _product_out(p: Product) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "short_name": p.short_name, "group_id": p.group_id,
            "kind": p.kind, "tax_category": p.tax_category, "stock_mode": p.stock_mode, "active": p.active,
            "hidden_at_till": p.hidden_at_till, "description": p.description, "version": p.version}


def list_products(s: Session, tenant_id: str, *, group_id: int | None = None, include_inactive: bool = False,
                  query: str | None = None) -> list[dict]:
    q = select(Product).where(Product.tenant_id == tenant_id)
    if not include_inactive:
        q = q.where(Product.active.is_(True))
    if group_id is not None:
        q = q.where(Product.group_id == group_id)
    if query:
        like = f"%{query.strip().lower()}%"
        q = q.where(func.lower(Product.name).like(like) | func.lower(Product.sku).like(like))
    return [_product_out(p) for p in s.scalars(q.order_by(Product.name).limit(500))]


def _validate(fields: dict) -> None:
    if "name" in fields and not (fields["name"] or "").strip():
        raise InvalidCatalog("product name is required")
    if "kind" in fields and fields["kind"] not in PRODUCT_KINDS:
        raise InvalidCatalog(f"kind must be one of {PRODUCT_KINDS}")
    if "stock_mode" in fields and fields["stock_mode"] not in STOCK_MODES:
        raise InvalidCatalog(f"stock_mode must be one of {STOCK_MODES}")
    if "tax_category" in fields and fields["tax_category"] not in TAX_CATEGORIES:
        raise InvalidCatalog(f"tax_category must be one of {TAX_CATEGORIES}")


def _set_barcodes(s: Session, tenant_id: str, product_id: int, codes: list[str]) -> None:
    clean = []
    for c in codes:
        c = (c or "").strip()
        if not c:
            continue
        if not c.isdigit() or len(c) > 32:
            raise InvalidCatalog(f"barcode {c!r} must be digits only (max 32)")
        if c not in clean:
            clean.append(c)
    taken = s.scalar(select(ProductBarcode.product_id).where(
        ProductBarcode.tenant_id == tenant_id, ProductBarcode.code.in_(clean) if clean else False,
        ProductBarcode.product_id != product_id).limit(1)) if clean else None
    if taken is not None:
        raise Conflict(f"a barcode is already used by product {taken}")
    for row in s.scalars(select(ProductBarcode).where(ProductBarcode.tenant_id == tenant_id,
                                                     ProductBarcode.product_id == product_id)):
        s.delete(row)
    s.flush()
    for c in clean:
        s.add(ProductBarcode(tenant_id=tenant_id, product_id=product_id, code=c))


def _emit_product(s: Session, p: Product, now: datetime, change: str) -> None:
    events.emit(s, tenant_id=p.tenant_id, type="catalog.product.changed",
                payload={"product_id": p.id, "sku": p.sku, "name": p.name, "group_id": p.group_id, "kind": p.kind,
                         "active": p.active, "hidden_at_till": p.hidden_at_till, "tax_category": p.tax_category,
                         "version": p.version, "change": change},
                aggregate_type="product", aggregate_id=str(p.id), aggregate_version=p.version, now=now)


def create_product(s: Session, tenant_id: str, *, actor: str, now: datetime, sku: str, name: str,
                   base_price_cents: int, group_id: int | None = None, tax_category: str = "standard",
                   kind: str = "standard", short_name: str | None = None, description: str | None = None,
                   hidden_at_till: bool = False, stock_mode: str = "none", barcodes: list[str] | None = None,
                   reason: str | None = None) -> tuple[Product, PriceVersion]:
    """A new product starts with a base-price DRAFT: it is sold nowhere until a
    second person approves that price."""
    sku = (sku or "").strip().upper()
    if not sku or len(sku) > 64:
        raise InvalidCatalog("product number (sku) is required, max 64 characters")
    _validate({"name": name, "kind": kind, "tax_category": tax_category, "stock_mode": stock_mode})
    if group_id is not None:
        _group(s, tenant_id, group_id)
    if s.scalar(select(Product.id).where(Product.tenant_id == tenant_id, Product.sku == sku)) is not None:
        raise Conflict(f"product number {sku} is already used")
    p = Product(tenant_id=tenant_id, sku=sku, name=name.strip()[:200], price_cents=0, tax_category=tax_category,
                category="Muud", group_id=group_id, kind=kind, short_name=(short_name or None),
                description=(description or None), hidden_at_till=hidden_at_till, stock_mode=stock_mode, version=1)
    s.add(p)
    s.flush()
    _set_barcodes(s, tenant_id, p.id, barcodes or [])
    v = propose_price(s, tenant_id, actor=actor, now=now, product_id=p.id, price_list_id=None,
                      price_cents=base_price_cents, reason=reason or "uus toode")
    _emit_product(s, p, now, "created")
    return p, v


def preview_product_change(s: Session, tenant_id: str, product_id: int) -> dict:
    """Open bills that hold the product (shown before deactivating it)."""
    n = s.scalar(select(func.count(func.distinct(Order.id))).join(OrderLine, OrderLine.order_id == Order.id)
                 .where(Order.tenant_id == tenant_id, Order.status.in_(("open", "partially_paid")),
                        OrderLine.product_id == product_id, OrderLine.quantity > 0))
    return {"open_orders": int(n or 0)}


def update_product(s: Session, tenant_id: str, product_id: int, *, now: datetime, expected_version: int,
                   fields: dict, barcodes: list[str] | None = None) -> Product:
    """Card fields only; prices never change here (propose_price). Optimistic
    lock on ``version``. Deactivating never touches stock (discovery L2)."""
    p = _product(s, tenant_id, product_id)
    if p.version != expected_version:
        raise Conflict("someone else changed this product; reload it")
    allowed = {"name", "short_name", "group_id", "kind", "tax_category", "description", "hidden_at_till",
               "active", "stock_mode"}
    unknown = set(fields) - allowed
    if unknown:
        raise InvalidCatalog(f"unknown fields: {sorted(unknown)}")
    _validate(fields)
    if fields.get("group_id") is not None:
        _group(s, tenant_id, fields["group_id"])
    was_active = p.active
    for k, v in fields.items():
        if k == "name":
            v = v.strip()[:200]
        setattr(p, k, v)
    if barcodes is not None:
        _set_barcodes(s, tenant_id, p.id, barcodes)
    p.version += 1
    change = "deactivated" if was_active and not p.active else "activated" if not was_active and p.active else "updated"
    _emit_product(s, p, now, change)
    return p


# ------------------------------------------------------------------ price lists


def list_price_lists(s: Session, tenant_id: str) -> list[dict]:
    outlets: dict[int, list[dict]] = {}
    for loc in s.scalars(select(Location).where(Location.tenant_id == tenant_id).order_by(Location.name)):
        if loc.price_list_id is not None:
            outlets.setdefault(loc.price_list_id, []).append({"id": loc.id, "name": loc.name})
    return [{"id": pl.id, "name": pl.name, "active": pl.active, "outlets": outlets.get(pl.id, [])}
            for pl in s.scalars(select(PriceList).where(PriceList.tenant_id == tenant_id).order_by(PriceList.name))]


def create_price_list(s: Session, tenant_id: str, *, name: str) -> PriceList:
    name = (name or "").strip()
    if not name:
        raise InvalidCatalog("price list name is required")
    if s.scalar(select(PriceList.id).where(PriceList.tenant_id == tenant_id, PriceList.name == name)) is not None:
        raise Conflict(f"price list {name} already exists")
    pl = PriceList(tenant_id=tenant_id, name=name[:100], active=True)
    s.add(pl)
    s.flush()
    return pl


def assign_outlet(s: Session, tenant_id: str, *, location_id: int, price_list_id: int | None, now: datetime,
                  actor: str) -> Location:
    """Point an outlet at a price list (or back to base prices). Only approved
    versions are ever effective, so this switches between approved prices."""
    loc = s.scalar(select(Location).where(Location.tenant_id == tenant_id, Location.id == location_id))
    if loc is None:
        raise NotFound(f"outlet {location_id} not found")
    if price_list_id is not None and s.scalar(select(PriceList.id).where(
            PriceList.tenant_id == tenant_id, PriceList.id == price_list_id)) is None:
        raise NotFound(f"price list {price_list_id} not found")
    loc.price_list_id = price_list_id
    events.emit(s, tenant_id=tenant_id, type="pricing.outlet.changed",
                payload={"location_id": loc.id, "price_list_id": price_list_id, "changed_by": actor},
                aggregate_type="location", aggregate_id=str(loc.id), aggregate_version=1, now=now,
                location_id=loc.id)
    return loc


# ------------------------------------------------------------------ price versions


def version_out(v: PriceVersion) -> dict:
    return {"id": v.id, "product_id": v.product_id, "price_list_id": v.price_list_id, "price_cents": v.price_cents,
            "valid_from": v.valid_from.isoformat(), "status": v.status, "reason": v.reason,
            "created_by": v.created_by, "created_at": v.created_at.isoformat(), "decided_by": v.decided_by,
            "decided_at": v.decided_at.isoformat() if v.decided_at else None, "decision_note": v.decision_note}


def propose_price(s: Session, tenant_id: str, *, actor: str, now: datetime, product_id: int,
                  price_list_id: int | None, price_cents: int | None, valid_from: datetime | None = None,
                  reason: str | None = None) -> PriceVersion:
    p = _product(s, tenant_id, product_id)
    if price_list_id is None and price_cents is None:
        raise InvalidCatalog("the base price cannot be 'not sold'; deactivate the product instead")
    if price_list_id is not None and s.scalar(select(PriceList.id).where(
            PriceList.tenant_id == tenant_id, PriceList.id == price_list_id)) is None:
        raise NotFound(f"price list {price_list_id} not found")
    if price_cents is not None and not (0 <= price_cents <= MAX_PRICE_CENTS):
        raise InvalidCatalog("price must be between 0 and 100 000 €")
    v = PriceVersion(id=str(uuid.uuid4()), tenant_id=tenant_id, product_id=p.id, price_list_id=price_list_id,
                     price_cents=price_cents, valid_from=valid_from or now, status="draft",
                     reason=(reason or None) and reason[:200], created_by=actor, created_at=now)
    s.add(v)
    s.flush()
    return v


def _draft(s: Session, tenant_id: str, version_id: str) -> PriceVersion:
    v = s.scalar(select(PriceVersion).where(PriceVersion.tenant_id == tenant_id, PriceVersion.id == version_id)
                 .with_for_update())
    if v is None:
        raise NotFound(f"price version {version_id} not found")
    if v.status != "draft":
        raise Conflict(f"this price was already {v.status}")
    return v


def preview_price(s: Session, tenant_id: str, version_id: str, now: datetime) -> dict:
    """Impact before approving: the price each outlet charges now vs after."""
    v = s.scalar(select(PriceVersion).where(PriceVersion.tenant_id == tenant_id, PriceVersion.id == version_id))
    if v is None:
        raise NotFound(f"price version {version_id} not found")
    p = _product(s, tenant_id, v.product_id)
    effective = max(v.valid_from, now)
    rows = []
    for loc in s.scalars(select(Location).where(Location.tenant_id == tenant_id).order_by(Location.name)):
        before, src, _ = resolve(s, tenant_id, p, loc.id, effective)
        affected = (v.price_list_id is None and src in ("base", "legacy", "none")) or \
            (v.price_list_id is not None and loc.price_list_id == v.price_list_id)
        rows.append({"location_id": loc.id, "outlet": loc.name, "before_cents": before,
                     "after_cents": v.price_cents if affected else before, "affected": affected})
    rate = tax_rate_for(s, p.tax_category, effective.date())
    return {"version": version_out(v), "product": _product_out(p), "effective_from": effective.isoformat(),
            "vat_rate_bp": rate, "outlets": rows, **preview_product_change(s, tenant_id, p.id)}


def approve_price(s: Session, tenant_id: str, version_id: str, *, approver: str, now: datetime,
                  note: str | None = None) -> PriceVersion:
    v = _draft(s, tenant_id, version_id)
    if approver == v.created_by:
        raise FourEyes("four eyes: the person who proposed a price cannot approve it")
    if v.valid_from < now:  # never back-date: history already sold at the old price
        v.valid_from = now
    p = _product(s, tenant_id, v.product_id)
    if v.price_list_id is None and _legacy_applies(s, tenant_id, p):
        # First base price of a pre-P1-1a product: write down what it sold at
        # until now, so every past sale still matches a version (R14).
        s.add(PriceVersion(id=str(uuid.uuid4()), tenant_id=tenant_id, product_id=p.id, price_list_id=None,
                           price_cents=p.price_cents, valid_from=LEGACY_FROM, status="approved",
                           reason="hind enne hinnaversioone (P1-1a)", created_by="system:legacy", created_at=now,
                           decided_by="system:legacy", decided_at=now))
        s.flush()
    v.status, v.decided_by, v.decided_at, v.decision_note = "approved", approver, now, (note or None)
    seq = s.scalar(select(func.count(PriceVersion.id)).where(
        PriceVersion.tenant_id == tenant_id, PriceVersion.product_id == p.id, PriceVersion.status == "approved"))
    s.flush()
    events.emit(s, tenant_id=tenant_id, type="pricing.price.published",
                payload={"version_id": v.id, "product_id": p.id, "sku": p.sku, "price_list_id": v.price_list_id,
                         "price_cents": v.price_cents, "valid_from": v.valid_from.isoformat(),
                         "proposed_by": v.created_by, "approved_by": approver},
                aggregate_type="product_price", aggregate_id=str(p.id), aggregate_version=int(seq or 1), now=now)
    return v


def reject_price(s: Session, tenant_id: str, version_id: str, *, actor: str, now: datetime,
                 note: str | None = None, withdraw: bool = False) -> PriceVersion:
    """Reject (a reviewer) or withdraw (the proposer). Nothing is published."""
    v = _draft(s, tenant_id, version_id)
    if withdraw and actor != v.created_by:
        raise Forbidden("only the proposer can withdraw a draft; reject it instead")
    v.status = "withdrawn" if withdraw else "rejected"
    v.decided_by, v.decided_at, v.decision_note = actor, now, (note or None)
    return v


def pending_prices(s: Session, tenant_id: str) -> list[dict]:
    names = dict(s.execute(select(Product.id, Product.name).where(Product.tenant_id == tenant_id)).all())
    lists = dict(s.execute(select(PriceList.id, PriceList.name).where(PriceList.tenant_id == tenant_id)).all())
    return [{**version_out(v), "product_name": names.get(v.product_id),
             "price_list_name": lists.get(v.price_list_id) if v.price_list_id else "Põhihind"}
            for v in s.scalars(select(PriceVersion).where(PriceVersion.tenant_id == tenant_id,
                                                          PriceVersion.status == "draft")
                               .order_by(PriceVersion.created_at))]


def list_outlets(s: Session, tenant_id: str) -> list[dict]:
    return [{"id": loc.id, "code": loc.code, "name": loc.name, "price_list_id": loc.price_list_id}
            for loc in s.scalars(select(Location).where(Location.tenant_id == tenant_id).order_by(Location.name))]
