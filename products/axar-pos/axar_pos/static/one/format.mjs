/** Display helpers only. No VAT, rounding or totals here: the server owns money. */

export function formatEuro(cents) {
  if (!Number.isSafeInteger(cents)) return '—';
  const sign = cents < 0 ? '−' : '';
  const abs = Math.abs(cents);
  return `${sign}${Math.trunc(abs / 100)},${String(abs % 100).padStart(2, '0')} €`;
}

/** "12,30" / "12.3" / "12" -> 1230 integer cents; anything else -> null. */
export function parseEuroInput(text) {
  const m = String(text ?? '').trim().replace(',', '.').match(/^(\d{1,7})(?:\.(\d{0,2}))?$/);
  if (!m) return null;
  return Number.parseInt(m[1], 10) * 100 + Number.parseInt((m[2] || '').padEnd(2, '0'), 10);
}

export function centsToInput(cents) {
  return `${Math.trunc(cents / 100)},${String(cents % 100).padStart(2, '0')}`;
}
