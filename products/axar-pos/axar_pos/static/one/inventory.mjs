// AXAR ONE — inventory screens (#14): invoice inbox, stock & stock card,
// recipe editor with live cost. All amounts and costs come from the server;
// the browser only parses what the user typed and formats what it received.
import { formatEuro, parseEuroInput } from './format.mjs';

const SCALE = 1_000_000n;
const UOM_ET = { kg: 'kg', l: 'l', pcs: 'tk' };
const KIND_ET = {
  receipt: 'Vastuvõtt', supplier_return: 'Tagastus tarnijale', sale_consumption: 'Müük',
  production_consumption: 'Tootmisse', production_output: 'Toodang',
  invoice_adjustment: 'Arve hinnavahe (laos)', cogs_adjustment: 'Arve hinnavahe (kulu)',
};
const VAT_RATES = [[2400, '24 %'], [1300, '13 %'], [900, '9 %'], [0, '0 %']];

/** "0,2" / "10" / "1.25" -> integer micro units (exact, no floats); null if invalid. */
export function parseQty(text) {
  const t = String(text).trim().replace(/\s/g, '').replace(',', '.');
  const m = /^(\d{1,9})(?:\.(\d{1,6}))?$/.exec(t);
  if (!m) return null;
  const micro = BigInt(m[1]) * SCALE + BigInt((m[2] || '').padEnd(6, '0'));
  return micro > 0n ? Number(micro) : null;
}

/** micro units -> "9,6 kg" (up to 3 decimals, Estonian format). */
export function formatQty(micro, uom) {
  const neg = micro < 0; const v = BigInt(Math.abs(micro));
  const whole = v / SCALE; const frac = (v % SCALE).toString().padStart(6, '0').slice(0, 3).replace(/0+$/, '');
  return `${neg ? '−' : ''}${whole.toLocaleString('et-EE')}${frac ? `,${frac}` : ''} ${UOM_ET[uom] || uom}`;
}

/** cents per base unit scaled by 1e6 -> "5,00 €/kg". */
const unitCost = (scaled, uom) => `${formatEuro(Math.round(scaled / 1e6))}/${UOM_ET[uom] || uom}`;
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Tallinn' }).format(new Date());
const slug = (s) => s.normalize('NFKD').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toUpperCase().slice(0, 24) || 'ITEM';

export function createInventory({ api, el, stateBox, announce, ApiError }) {
  const st = { tab: 'invoices', items: [], suppliers: [], warehouses: [], offers: [], recipeOffer: null, card: null };
  let root = null;

  async function loadMasters() {
    const [items, suppliers, warehouses, offers] = await Promise.all([
      api('/api/v1/inventory/items'), api('/api/v1/inventory/suppliers'),
      api('/api/v1/inventory/warehouses'), api('/api/v1/inventory/offers')]);
    st.items = items.data; st.suppliers = suppliers.data; st.warehouses = warehouses.data; st.offers = offers.data;
  }
  const item = (id) => st.items.find((x) => x.id === id);
  const errText = (e) => (e instanceof ApiError && e.code === 'duplicate_document' ? 'Selle tarnija sama numbriga arve on juba olemas.'
    : e instanceof ApiError && e.code === 'already_posted' ? 'See arve on juba kinnitatud.' : e.message);

  function option(value, text, selected) { const o = el('option', null, text); o.value = String(value); if (selected) o.selected = true; return o; }
  function field(label, input) { const w = el('label', 'inv-field'); w.append(el('span', null, label), input); return w; }

  async function render(container) {
    root = container; root.replaceChildren();
    const tabs = el('div', 'inv-tabs'); tabs.setAttribute('role', 'tablist');
    for (const [k, label] of [['invoices', 'Arved'], ['stock', 'Laoseis'], ['recipes', 'Retseptid']]) {
      const b = el('button', `category-btn${st.tab === k ? ' active' : ''}`, label); b.type = 'button';
      b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(st.tab === k)); b.dataset.tab = k;
      b.addEventListener('click', () => { st.tab = k; st.card = null; render(root); });
      tabs.append(b);
    }
    const body = el('section', 'inv-body'); body.id = 'inv-body';
    root.append(tabs, body);
    body.append(el('p', 'muted-p', 'Laadin…'));
    try { await loadMasters(); } catch (e) { body.replaceChildren(stateBox('error', `Laoandmeid ei saanud laadida: ${e.message}`)); return; }
    if (!st.warehouses.length) { body.replaceChildren(stateBox('error', 'Ladu puudub. Loo kõigepealt ladu (juhi seadetes).')); return; }
    if (st.tab === 'invoices') await renderInvoices(body);
    else if (st.tab === 'stock') await (st.card ? renderCard(body) : renderStock(body));
    else await renderRecipes(body);
  }

  // ---------------------------------------------------------------- invoices

  async function renderInvoices(body) {
    body.replaceChildren();
    const head = el('div', 'inv-head');
    const add = el('button', 'primary-btn inv-add', 'Uus ostuarve'); add.type = 'button'; add.id = 'new-invoice';
    add.addEventListener('click', () => renderInvoiceForm(body));
    head.append(el('div', 'section-label', 'Ostuarved'), add);
    body.append(head, el('p', 'muted', 'Kaup võetakse lattu ostuarve alusel. Mustand ei muuda laoseisu; „Kinnita“ võtab kauba lattu.'));
    const { data: docs } = await api('/api/v1/inventory/documents');
    if (!docs.length) { body.append(el('p', 'muted-p', 'Arveid veel pole.')); return; }
    const table = el('table', 'sales-table inv-docs'); const tb = el('tbody');
    const hr = el('tr'); for (const h of ['Tarnija', 'Nr', 'Kuupäev', 'Netto', 'Kokku', 'Olek', '']) hr.append(el('th', null, h));
    const th = el('thead'); th.append(hr);
    for (const d of docs) {
      const tr = el('tr'); tr.dataset.doc = d.doc_no;
      const sup = st.suppliers.find((x) => x.id === d.supplier_id);
      const td = el('td');
      if (d.status === 'draft') {
        const post = el('button', 'secondary-btn inv-post', 'Kinnita ja võta lattu'); post.type = 'button';
        post.addEventListener('click', async () => {
          post.disabled = true;
          try { await api(`/api/v1/inventory/documents/${d.id}/post`, { method: 'POST' }); announce(`Arve ${d.doc_no} kinnitatud`); renderInvoices(body); } catch (e) { post.disabled = false; td.append(stateBox('error', errText(e))); }
        });
        td.append(post);
      }
      tr.append(el('td', null, sup ? sup.name : '?'), el('td', null, d.doc_no), el('td', null, d.doc_date),
        el('td', 'num', formatEuro(d.net_cents)), el('td', 'num', formatEuro(d.gross_cents)),
        el('td', null, d.status === 'draft' ? 'Mustand' : 'Kinnitatud'), td);
      tb.append(tr);
    }
    table.append(th, tb); body.append(table);
  }

  function renderInvoiceForm(body) {
    body.replaceChildren();
    const form = el('form', 'inv-form'); form.noValidate = true;
    const title = el('div', 'section-label', 'Uus ostuarve');
    const supSel = el('select'); supSel.id = 'inv-supplier';
    supSel.append(option('', '– vali tarnija –'), ...st.suppliers.map((x) => option(x.id, x.name)), option('new', '+ uus tarnija…'));
    const newSup = el('input'); newSup.id = 'inv-new-supplier'; newSup.placeholder = 'Tarnija nimi'; newSup.hidden = true;
    supSel.addEventListener('change', () => { newSup.hidden = supSel.value !== 'new'; if (!newSup.hidden) newSup.focus(); });
    const no = el('input'); no.id = 'inv-no'; no.autocomplete = 'off';
    const dt = el('input'); dt.id = 'inv-date'; dt.type = 'date'; dt.value = today();
    const whSel = el('select'); whSel.id = 'inv-warehouse';
    whSel.append(...st.warehouses.map((w) => option(w.id, w.name, w.is_default)));
    const freight = el('input'); freight.id = 'inv-freight'; freight.inputMode = 'decimal'; freight.placeholder = '0,00';
    const lines = el('div', 'inv-lines'); lines.id = 'inv-lines';
    const addLine = el('button', 'secondary-btn', '+ rida'); addLine.type = 'button'; addLine.id = 'inv-add-line';
    addLine.addEventListener('click', () => lines.append(lineRow()));
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const save = el('button', 'primary-btn', 'Salvesta mustandina'); save.type = 'submit'; save.id = 'inv-save';
    const cancel = el('button', 'secondary-btn', 'Tühista'); cancel.type = 'button'; cancel.addEventListener('click', () => renderInvoices(body));
    const grid = el('div', 'inv-grid');
    grid.append(field('Tarnija', supSel), field('Uus tarnija', newSup), field('Arve number', no), field('Arve kuupäev', dt),
      field('Ladu', whSel), field('Transport (€, jagatakse ridadele)', freight));
    newSup.parentElement.hidden = true;
    supSel.addEventListener('change', () => { newSup.parentElement.hidden = supSel.value !== 'new'; });
    form.append(title, grid, el('div', 'section-label', 'Read'), lines, addLine, msg, save, cancel);
    lines.append(lineRow());
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault(); msg.textContent = '';
      try {
        let supplierId = Number(supSel.value);
        if (supSel.value === 'new') {
          if (!newSup.value.trim()) throw new Error('Sisesta tarnija nimi.');
          const { data } = await api('/api/v1/inventory/suppliers', { method: 'POST', body: { code: slug(newSup.value), name: newSup.value.trim() } });
          supplierId = data.id;
        }
        if (!supplierId) throw new Error('Vali tarnija.');
        if (!no.value.trim()) throw new Error('Sisesta arve number.');
        const fc = freight.value.trim() ? parseEuroInput(freight.value) : 0;
        if (fc === null) throw new Error('Transpordi summa on vigane.');
        const body2 = { supplier_id: supplierId, doc_type: 'invoice', doc_no: no.value.trim(), doc_date: dt.value,
          warehouse_id: Number(whSel.value), freight_cents: fc, lines: [] };
        for (const row of lines.querySelectorAll('.inv-line')) body2.lines.push(await row.read());
        if (!body2.lines.length) throw new Error('Lisa vähemalt üks rida.');
        save.disabled = true;
        await api('/api/v1/inventory/documents', { method: 'POST', body: body2 });
        announce('Arve salvestatud mustandina');
        renderInvoices(body);
      } catch (e) { save.disabled = false; msg.textContent = errText(e); }
    });
    body.append(form);
    supSel.focus();
  }

  function lineRow() {
    const row = el('div', 'inv-line');
    const itSel = el('select', 'inv-item'); itSel.setAttribute('aria-label', 'Kaup');
    itSel.append(option('', '– kaup –'), ...st.items.map((x) => option(x.id, `${x.name} (${UOM_ET[x.uom]})`)), option('new', '+ uus kaup…'));
    const newName = el('input', 'inv-new-item'); newName.placeholder = 'Uue kauba nimi'; newName.hidden = true;
    const newUom = el('select', 'inv-new-uom'); newUom.append(option('kg', 'kg'), option('l', 'l'), option('pcs', 'tk')); newUom.hidden = true;
    itSel.addEventListener('change', () => { newName.hidden = newUom.hidden = itSel.value !== 'new'; });
    const qty = el('input', 'inv-qty'); qty.inputMode = 'decimal'; qty.placeholder = 'Kogus'; qty.setAttribute('aria-label', 'Kogus');
    const net = el('input', 'inv-net'); net.inputMode = 'decimal'; net.placeholder = 'Netosumma €'; net.setAttribute('aria-label', 'Rea netosumma');
    const vat = el('select', 'inv-vat'); vat.setAttribute('aria-label', 'Käibemaks');
    vat.append(...VAT_RATES.map(([bp, t]) => option(bp, t)));
    const nr = el('label', 'inv-nonrec'); const nrc = el('input'); nrc.type = 'checkbox'; nr.append(nrc, el('span', null, 'KM ei ole tagasinõutav'));
    const del = el('button', 'qty-btn', '×'); del.type = 'button'; del.setAttribute('aria-label', 'Eemalda rida');
    del.addEventListener('click', () => row.remove());
    row.append(itSel, newName, newUom, qty, net, vat, nr, del);
    row.read = async () => {
      let itemId = Number(itSel.value);
      if (itSel.value === 'new') {
        if (!newName.value.trim()) throw new Error('Sisesta uue kauba nimi.');
        const { data } = await api('/api/v1/inventory/items', { method: 'POST', body: {
          sku: `${slug(newName.value)}-${Date.now().toString(36).slice(-4).toUpperCase()}`, name: newName.value.trim(),
          kind: 'ingredient', uom: newUom.value } });
        st.items.push(data); itemId = data.id;
        itSel.append(option(data.id, `${data.name} (${UOM_ET[data.uom]})`)); itSel.value = String(data.id);
        newName.hidden = newUom.hidden = true;
      }
      if (!itemId) throw new Error('Vali igale reale kaup.');
      const q = parseQty(qty.value); if (q === null) throw new Error('Kogus on vigane (nt 10 või 0,5).');
      const c = parseEuroInput(net.value); if (c === null) throw new Error('Rea netosumma on vigane (nt 50,00).');
      return { item_id: itemId, qty_micro: q, net_cents: c, vat_rate_bp: Number(vat.value), vat_recoverable: !nrc.checked };
    };
    return row;
  }

  // ------------------------------------------------------------- stock & card

  // The stock list and the stock card are redrawn by clicks AND by live
  // "stock changed" events, so two renders can overlap. Each render fetches
  // first and draws only if it is still the newest one (otherwise both would
  // append their table to the same view).
  let stockGen = 0;

  async function renderStock(body) {
    const gen = ++stockGen;
    const { data: rows } = await api('/api/v1/inventory/stock');
    if (gen !== stockGen) return;
    body.replaceChildren(el('div', 'section-label', 'Laoseis'),
      el('p', 'muted', 'Väärtus on liikuva kaalutud keskmise omahinnaga (km-ta). Müügid kantakse laost maha automaatselt.'));
    if (!rows.length) { body.append(el('p', 'muted-p', 'Laos pole veel midagi. Kinnita esimene ostuarve.')); return; }
    const table = el('table', 'sales-table inv-stock'); const th = el('thead'); const hr = el('tr');
    for (const h of ['Kaup', 'Kogus', 'Väärtus', 'Keskmine hind', '']) hr.append(el('th', null, h));
    th.append(hr); const tb = el('tbody');
    let total = 0;
    for (const r of rows) {
      total += r.value_cents;
      const tr = el('tr'); tr.dataset.item = r.sku; if (r.negative) tr.classList.add('negative');
      const open = el('button', null, 'Laokaart'); open.type = 'button'; open.setAttribute('aria-label', `Laokaart: ${r.name}`);
      open.addEventListener('click', () => { st.card = r; renderCard(body); });
      const td = el('td'); td.append(open);
      tr.append(el('td', null, r.name), el('td', 'num', formatQty(r.qty_micro, r.uom) + (r.negative ? ' ⚠' : '')),
        el('td', 'num', formatEuro(r.value_cents)), el('td', 'num', r.unit_cost_micro ? unitCost(r.unit_cost_micro, r.uom) : '—'), td);
      tb.append(tr);
    }
    table.append(th, tb);
    body.append(table, el('p', 'inv-total', `Laovaru kokku ${formatEuro(total)}`));
  }

  async function renderCard(body) {
    const gen = ++stockGen;
    const r = st.card;
    const { data: moves } = await api(`/api/v1/inventory/items/${r.item_id}/card`);
    if (gen !== stockGen) return;
    body.replaceChildren();
    const back = el('button', 'secondary-btn', '← Laoseis'); back.type = 'button'; back.addEventListener('click', () => { st.card = null; renderStock(body); });
    body.append(back, el('div', 'section-label', `Laokaart: ${r.name}`));
    const table = el('table', 'sales-table inv-card'); const th = el('thead'); const hr = el('tr');
    for (const h of ['Kuupäev', 'Liik', 'Kogus', 'Väärtus', 'Omahinda (kulu)']) hr.append(el('th', null, h));
    th.append(hr); const tb = el('tbody');
    for (const m of moves) {
      const tr = el('tr'); tr.dataset.kind = m.kind;
      tr.append(el('td', null, m.business_date), el('td', null, KIND_ET[m.kind] || m.kind),
        el('td', 'num', m.qty_micro ? formatQty(m.qty_micro, r.uom) : '—'), el('td', 'num', formatEuro(m.value_cents)),
        el('td', 'num', m.cogs_cents ? formatEuro(m.cogs_cents) : ''));
      tb.append(tr);
    }
    table.append(th, tb); body.append(table);
  }

  // ------------------------------------------------------------- recipes

  async function renderRecipes(body) {
    body.replaceChildren();
    const wrap = el('div', 'inv-recipes');
    const list = el('div', 'inv-offer-list'); list.setAttribute('role', 'list');
    for (const o of st.offers) {
      const b = el('button', `inv-offer${st.recipeOffer === o.id ? ' selected' : ''}`); b.type = 'button'; b.dataset.offer = o.name;
      b.append(el('strong', null, o.name), el('small', null, o.stock_mode === 'recipe' ? 'retsept' : o.stock_mode === 'item' ? 'laokaup' : 'laoga sidumata'));
      b.addEventListener('click', () => { st.recipeOffer = o.id; renderRecipes(body); });
      list.append(b);
    }
    const editor = el('div', 'inv-editor'); editor.id = 'recipe-editor';
    wrap.append(list, editor); body.append(wrap);
    if (!st.recipeOffer) { editor.append(el('p', 'muted-p', 'Vali vasakult menüütoode, et selle retsepti muuta.')); return; }
    const offer = st.offers.find((o) => o.id === st.recipeOffer);
    if (offer.stock_mode === 'item') { editor.append(stateBox('unconfirmed', 'See toode müüb valmis laokaupa (nt partiitoodang); retsepti sellel pole.')); return; }
    const { data: current } = await api(`/api/v1/inventory/recipes?product_id=${offer.id}`);
    editor.append(el('div', 'section-label', `Retsept: ${offer.name}`),
      el('p', 'muted', current ? `Kehtiv versioon ${current.version} (alates ${current.valid_from}). Salvestamine loob uue versiooni; vanad müügid ei muutu.` : 'Retsepti veel pole. Koostisosad ühe portsjoni kohta.'));
    const rows = el('div', 'inv-lines'); rows.id = 'recipe-lines';
    const result = el('div', 'inv-cost'); result.id = 'recipe-cost'; result.setAttribute('role', 'status');
    let timer = null;
    const preview = () => { clearTimeout(timer); timer = setTimeout(async () => {
      const { lines, problems } = readRecipeLines(rows);
      if (problems.length) { result.replaceChildren(...problems.map((x) => el('p', 'dialog-error', x))); return; }
      if (!lines.length) { result.replaceChildren(); return; }
      try {
        const { data } = await api('/api/v1/inventory/recipes/preview', { method: 'POST', body: { product_id: offer.id, lines } });
        renderCost(result, data);
      } catch (e) { result.replaceChildren(el('p', 'dialog-error', e.message)); }
    }, 150); };
    const addRow = (itemId = '', qtyMicro = null) => {
      const row = el('div', 'inv-line recipe-line');
      const sel = el('select', 'inv-item'); sel.setAttribute('aria-label', 'Koostisosa');
      sel.append(option('', '– koostisosa –'), ...st.items.map((x) => option(x.id, `${x.name} (${UOM_ET[x.uom]})`, x.id === itemId)));
      const q = el('input', 'inv-qty'); q.inputMode = 'decimal'; q.placeholder = 'Kogus portsjonis'; q.setAttribute('aria-label', 'Kogus portsjonis');
      if (qtyMicro) q.value = formatQty(qtyMicro, '').trim();
      const del = el('button', 'qty-btn', '×'); del.type = 'button'; del.setAttribute('aria-label', 'Eemalda koostisosa');
      del.addEventListener('click', () => { row.remove(); preview(); });
      sel.addEventListener('change', preview); q.addEventListener('input', preview);
      row.append(sel, q, del); rows.append(row);
    };
    for (const ln of current?.lines || []) addRow(ln.item_id, ln.qty_micro);
    if (!current) addRow();
    const add = el('button', 'secondary-btn', '+ koostisosa'); add.type = 'button'; add.id = 'recipe-add';
    add.addEventListener('click', () => addRow());
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const save = el('button', 'primary-btn', 'Salvesta uus versioon'); save.type = 'button'; save.id = 'recipe-save';
    save.addEventListener('click', async () => {
      const { lines, problems } = readRecipeLines(rows);
      if (problems.length) { msg.textContent = `Salvestamata: ${problems.join(' ')}`; return; }
      if (!lines.length) { msg.textContent = 'Lisa vähemalt üks koostisosa koos kogusega.'; return; }
      msg.textContent = '';
      save.disabled = true;
      try {
        await api('/api/v1/inventory/recipes', { method: 'POST', body: { product_id: offer.id, lines, valid_from: today() } });
        announce('Retsept salvestatud'); await loadMasters(); renderRecipes(body);
      } catch (e) { msg.textContent = e.message; save.disabled = false; }
    });
    editor.append(rows, add, result, msg, save);
    preview();
  }

  /** Recipe rows -> {lines, problems}. Every problem names its ingredient and
   * marks its field, so the user sees exactly which row to fix. */
  function readRecipeLines(rows) {
    const lines = []; const problems = [];
    for (const row of rows.querySelectorAll('.recipe-line')) {
      const sel = row.querySelector('select'); const input = row.querySelector('input');
      const id = Number(sel.value); const raw = input.value.trim();
      input.classList.remove('invalid'); input.removeAttribute('aria-invalid');
      sel.classList.remove('invalid'); sel.removeAttribute('aria-invalid');
      if (!id && !raw) continue;
      const name = id ? (item(id)?.name || 'Koostisosa') : 'Koostisosa';
      if (!id) { problems.push(`Vali koostisosa kogusele „${raw}“.`); sel.classList.add('invalid'); sel.setAttribute('aria-invalid', 'true'); continue; }
      const q = parseQty(raw);
      if (q === null) {
        problems.push(raw ? `${name}: „${raw}“ ei ole kogus. Kirjuta näiteks 0,5.` : `${name}: kogus puudub.`);
        input.classList.add('invalid'); input.setAttribute('aria-invalid', 'true'); continue;
      }
      lines.push({ item_id: id, qty_micro: q });
    }
    return { lines, problems };
  }

  function renderCost(box, d) {
    box.replaceChildren();
    const tbl = el('table', 'sales-table'); const tb = el('tbody');
    for (const ln of d.lines) {
      const tr = el('tr');
      tr.append(el('td', null, ln.name), el('td', 'num', formatQty(ln.qty_micro, ln.uom)),
        el('td', 'num', ln.no_cost_yet ? 'hind puudub' : formatEuro(ln.cost_cents)));
      tb.append(tr);
    }
    tbl.append(tb);
    const sum = el('div', 'inv-summary');
    const pct = d.food_cost_bp == null ? '—' : `${(d.food_cost_bp / 100).toLocaleString('et-EE', { maximumFractionDigits: 1 })} %`;
    for (const [k, v, id] of [['Omahind portsjonis', formatEuro(d.cost_per_unit_cents), 'cost-per-portion'],
      ['Müügihind (km-ta)', formatEuro(d.price_net_cents), 'price-net'], ['Toorainekulu %', pct, 'food-cost'],
      ['Marginaal portsjonilt', formatEuro(d.margin_cents), 'margin']]) {
      const row = el('div', 'summary-line'); const val = el('strong', null, v); val.id = id;
      row.append(el('span', null, k), val); sum.append(row);
    }
    box.append(tbl, sum);
    if (d.lines.some((x) => x.no_cost_yet)) box.append(el('p', 'muted', 'Osal koostisosadel pole veel ostuhinda – kinnita nende ostuarve.'));
  }

  /** Live update (stock.changed): re-read the stock list or the open card;
   * never touches a form the user is typing in. */
  async function refresh() {
    const body = root && root.querySelector('#inv-body');
    if (!body || st.tab !== 'stock') return;
    try { await (st.card ? renderCard(body) : renderStock(body)); } catch { /* next event or reopen */ }
  }

  return { render, refresh };
}
