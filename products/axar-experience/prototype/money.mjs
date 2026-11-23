/** AXAR EXPERIENCE demo money model. This is not the POS financial authority. */
export const assertCents = (v) => {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0 || v > 100_000_000) {
    throw new TypeError('Expected a safe, non-negative integer amount in cents');
  }
  return v;
};
export const assertQuantity = (v) => {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v <= 0 || v > 999) {
    throw new TypeError('Quantity must be an integer between 1 and 999');
  }
  return v;
};
export function vatIncluded(grossCents, rateBasisPoints) {
  assertCents(grossCents);
  if (!Number.isSafeInteger(rateBasisPoints) || rateBasisPoints < 0 || rateBasisPoints > 10000) {
    throw new TypeError('Invalid VAT rate');
  }
  const n = BigInt(grossCents) * BigInt(rateBasisPoints);
  const d = BigInt(10000 + rateBasisPoints);
  return Number((n + (d / 2n)) / d);
}
export function cashRounded(totalCents, step = 5) {
  assertCents(totalCents);
  if (!Number.isSafeInteger(step) || step < 1 || step > 100) throw new TypeError('Invalid rounding step');
  const total = BigInt(totalCents), s = BigInt(step);
  const rounded = ((2n * total + s) / (2n * s)) * s;
  return Number(rounded);
}
export function cartSummary(lines) {
  if (!Array.isArray(lines)) throw new TypeError('lines must be array');
  let total = 0;
  let vat = 0;
  const byRate = new Map();
  for (const line of lines) {
    const unit = assertCents(line.priceCents);
    const quantity = assertQuantity(line.quantity);
    const amount = unit * quantity;
    assertCents(amount);
    const v = vatIncluded(amount, line.rateBp);
    total = assertCents(total + amount);
    vat += v;
    const prev = byRate.get(line.rateBp) || {grossCents: 0, vatCents: 0};
    byRate.set(line.rateBp, {grossCents: prev.grossCents + amount, vatCents: prev.vatCents + v});
  }
  return {totalCents: total, netCents: total - vat, vatCents: vat, cashDueCents: cashRounded(total), cashRoundingCents: cashRounded(total) - total, byRate: Object.fromEntries(byRate)};
}
export function cashChange(totalCents, tenderedCents) {
  assertCents(tenderedCents);
  const due = cashRounded(totalCents);
  if (tenderedCents < due) throw new RangeError('Not enough cash tendered');
  return tenderedCents - due;
}
export function formatEuro(cents) {
  assertCents(cents);
  return `${Math.trunc(cents / 100)},${String(cents % 100).padStart(2, '0')} €`;
}
