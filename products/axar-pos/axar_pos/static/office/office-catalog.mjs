// AXAR ONE — office: products and prices (P1-1a, docs/discovery/catalog.md §8).
//
// - Prices are never "saved": a manager PROPOSES a price; a SECOND person
//   approves it (four eyes, enforced by the server). Only approved prices reach
//   the tills, from their valid-from moment; past sales never change.
// - An outlet's price list overrides the base price; "ei müüda" on a list
//   means the product is not sold at those outlets.
// - Deactivating a product never changes stock.
// This module only shows server answers and sends the manager's choices.
import { centsToInput, formatEuro, parseEuroInput } from '../one/format.mjs';

const STATUS_ET = { draft: 'Ootab kinnitust', approved: 'Kinnitatud', rejected: 'Tagasi lükatud', withdrawn: 'Tagasi võetud' };
const TAX_ET = { standard: 'Tavamäär', reduced: 'Vähendatud määr', zero: '0 %' };

export function createCatalogOffice({ call, el, stateBox, announce, button, field, errText }) {
  const st = { q: '', product: null, groups: [], lists: [] };

  const priceText = (c) => (c == null ? 'ei müüda' : formatEuro(c));
  const when = (iso) => new Date(iso).toLocaleString('et-EE');
  function select(id, options, value) {
    const s = el('select'); if (id) s.id = id;
    for (const [v, label] of options) { const o = el('option', null, label); o.value = v; s.append(o); }
    s.value = value; return s;
  }
  const groupOptions = () => [['', '— rühmata —'], ...st.groups.map((g) => [String(g.id), g.name])];

  async function render(body) {
    body.replaceChildren(el('p', 'muted-p', 'Laadin…'));
    try {
      const [groups, lists, pending, outlets] = await Promise.all([
        call('/api/v1/catalog/groups'), call('/api/v1/pricing/lists'),
        call('/api/v1/pricing/prices/pending'), call('/api/v1/pricing/outlets')]);
      st.groups = groups.data; st.lists = lists.data;
      body.replaceChildren(pendingBox(body, pending.data), productsBox(body), newProductBox(body),
        groupsBox(body), listsBox(body, outlets.data));
      if (st.product) await openProduct(body, st.product);
    } catch (e) { body.replaceChildren(stateBox('error', errText(e))); }
  }

  // ------------------------------------------------------- approvals (four eyes)

  function pendingBox(body, rows) {
    const box = el('section', 'cat-box'); box.id = 'cat-pending';
    box.append(el('div', 'section-label', `Kinnitamist ootavad hinnad (${rows.length})`),
      el('p', 'muted', 'Hinna kinnitab alati teine inimene kui see, kes selle esitas. Kinnitatud hind kehtib kassades alates kehtivuse algusest; varasemad müügid ei muutu.'));
    if (!rows.length) { box.append(stateBox('ok', 'Kinnitamist ootavaid hindu pole.')); return box; }
    const list = el('ul', 'recon-list');
    for (const v of rows) {
      const li = el('li', 'recon-row warning'); li.dataset.version = v.id;
      const top = el('div', 'recon-top');
      top.append(el('strong', null, `${v.product_name} · ${v.price_list_name}: ${priceText(v.price_cents)}`),
        el('small', null, `esitas ${v.created_by} · ${when(v.created_at)} · kehtib alates ${when(v.valid_from)}`));
      li.append(top);
      if (v.reason) li.append(el('p', 'muted', `Põhjus: ${v.reason}`));
      const out = el('div'); out.setAttribute('role', 'status');
      const note = el('input', 'q-note'); note.placeholder = 'märkus (valikuline)';
      const preview = button('Eelvaade', 'secondary-btn cat-preview', null, async () => {
        try {
          const { data } = await call(`/api/v1/pricing/prices/${v.id}/preview`);
          const ul = el('ul', 'trace-list');
          for (const o of data.outlets) {
            ul.append(el('li', null, `${o.outlet}: ${priceText(o.before_cents)} → ${priceText(o.after_cents)}${o.affected ? '' : ' (ei muutu)'}`));
          }
          if (data.open_orders) ul.append(el('li', null, `Avatud arveid selle tootega: ${data.open_orders}`));
          out.replaceChildren(ul);
        } catch (e) { out.replaceChildren(stateBox('error', errText(e))); }
      });
      const approve = button('Kinnita', 'primary-btn cat-approve', null, async () => {
        try {
          await call(`/api/v1/pricing/prices/${v.id}/approve`, { method: 'POST', body: { note: note.value.trim() || null } });
          announce('Hind kinnitati'); await render(body);
        } catch (e) { out.replaceChildren(stateBox('error', fourEyes(e))); }
      });
      const reject = button('Lükka tagasi', 'secondary-btn cat-reject', null, async () => {
        try {
          await call(`/api/v1/pricing/prices/${v.id}/reject`, { method: 'POST', body: { note: note.value.trim() || null } });
          await render(body);
        } catch (e) { out.replaceChildren(stateBox('error', fourEyes(e))); }
      });
      const withdraw = button('Võta tagasi', 'secondary-btn cat-withdraw', null, async () => {
        try {
          await call(`/api/v1/pricing/prices/${v.id}/withdraw`, { method: 'POST', body: { note: null } });
          await render(body);
        } catch (e) { out.replaceChildren(stateBox('error', errText(e))); }
      });
      const actions = el('div', 'q-actions'); actions.append(preview, approve, note, reject, withdraw);
      li.append(actions, out); list.append(li);
    }
    box.append(list);
    return box;
  }

  function fourEyes(e) {
    return /four eyes/i.test(e.message || '') ? 'Enda esitatud hinda ei saa kinnitada: selle kinnitab teine inimene.' : errText(e);
  }

  // --------------------------------------------------------------- products

  function productsBox(body) {
    const box = el('section', 'cat-box'); box.id = 'cat-products';
    const q = el('input'); q.id = 'cat-search'; q.placeholder = 'otsi nime või tootenumbri järgi'; q.value = st.q;
    const out = el('div'); out.id = 'cat-product-list';
    const card = el('div'); card.id = 'cat-card';
    const search = async () => {
      st.q = q.value.trim();
      try {
        const { data } = await call(`/api/v1/catalog/products?include_inactive=true${st.q ? `&q=${encodeURIComponent(st.q)}` : ''}`);
        const ul = el('ul', 'trace-list');
        for (const p of data) {
          const li = el('li');
          li.append(button(`${p.sku} · ${p.name}${p.active ? '' : ' (mitteaktiivne)'}`, 'link-btn cat-open', null, () => openProduct(body, p.id)));
          ul.append(li);
        }
        out.replaceChildren(data.length ? ul : el('p', 'muted-p', 'Tooteid ei leitud.'));
      } catch (e) { out.replaceChildren(stateBox('error', errText(e))); }
    };
    q.addEventListener('input', () => { clearTimeout(st.t); st.t = setTimeout(search, 250); });
    box.append(el('div', 'section-label', 'Tooted'), field('Otsing', q), out, card);
    search();
    return box;
  }

  async function openProduct(body, id) {
    st.product = id;
    const card = body.querySelector('#cat-card');
    card.replaceChildren(el('p', 'muted-p', 'Laadin tootekaarti…'));
    let p;
    try { p = (await call(`/api/v1/catalog/products/${id}`)).data; } catch (e) { card.replaceChildren(stateBox('error', errText(e))); return; }
    const name = el('input'); name.id = 'card-name'; name.value = p.name;
    const short = el('input'); short.id = 'card-short'; short.value = p.short_name || '';
    const group = select('card-group', groupOptions(), p.group_id ? String(p.group_id) : '');
    const tax = select('card-tax', Object.entries(TAX_ET), p.tax_category);
    const hidden = el('input'); hidden.type = 'checkbox'; hidden.id = 'card-hidden'; hidden.checked = p.hidden_at_till;
    const active = el('input'); active.type = 'checkbox'; active.id = 'card-active'; active.checked = p.active;
    const codes = el('input'); codes.id = 'card-barcodes'; codes.value = p.barcodes.join(', ');
    const msg = el('div'); msg.id = 'card-msg'; msg.setAttribute('role', 'status');
    const save = button('Salvesta tootekaart', 'primary-btn', 'card-save', async () => {
      if (!active.checked && p.active) {
        try {
          const { data } = await call(`/api/v1/catalog/products/${p.id}/impact`);
          if (data.open_orders && !window.confirm(`Toode on ${data.open_orders} avatud arvel. Deaktiveerida? Laoseis ei muutu.`)) return;
        } catch { /* the save below reports errors */ }
      }
      try {
        await call(`/api/v1/catalog/products/${p.id}`, { method: 'PUT', body: {
          version: p.version, name: name.value.trim(), short_name: short.value.trim() || null,
          group_id: group.value ? Number(group.value) : null, tax_category: tax.value,
          hidden_at_till: hidden.checked, active: active.checked,
          barcodes: codes.value.split(',').map((x) => x.trim()).filter(Boolean) } });
        announce('Tootekaart salvestati'); await openProduct(body, p.id);
      } catch (e) { msg.replaceChildren(stateBox('error', e.status === 409 ? `${e.message}` : errText(e))); }
    });
    const head = el('div', 'section-label', `Tootekaart: ${p.sku} · ${p.name}`);
    const flags = el('div', 'cat-flags');
    const lab = (input, text) => { const l = el('label', 'cat-check'); l.append(input, el('span', null, text)); return l; };
    flags.append(lab(active, 'Aktiivne'), lab(hidden, 'Peida kassast'));
    card.replaceChildren(head, field('Nimi', name), field('Lühinimi', short), field('Rühm', group),
      field('Käibemaks', tax), field('Vöötkoodid (komaga)', codes), flags, save, msg, pricesBox(body, p));
  }

  function pricesBox(body, p) {
    const box = el('div', 'cat-prices'); box.id = 'cat-prices';
    box.append(el('div', 'section-label', 'Hinnad'),
      el('p', 'muted', 'Uus hind on ettepanek: see hakkab kehtima alles siis, kui teine inimene selle kinnitab.'));
    const table = el('table', 'cat-table');
    const thead = el('tr'); for (const h of ['Hinnakiri', 'Kehtiv hind', 'Uus hind', '']) thead.append(el('th', null, h));
    table.append(thead);
    for (const row of p.prices) {
      const tr = el('tr'); tr.dataset.list = row.price_list_id == null ? 'base' : String(row.price_list_id);
      const cur = row.price_list_id == null ? priceText(row.price_cents) : row.override ? priceText(row.price_cents) : 'põhihind';
      const input = el('input', 'cat-new-price'); input.placeholder = row.price_list_id == null ? '0,00' : '0,00 või tühi = ei müüda';
      if (row.price_cents != null) input.value = centsToInput(row.price_cents);
      const notSold = el('input'); notSold.type = 'checkbox'; notSold.className = 'cat-not-sold';
      const td = el('td');
      if (row.price_list_id != null) { const l = el('label', 'cat-check'); l.append(notSold, el('span', null, 'ei müüda')); td.append(l); }
      tr.append(el('td', null, row.name), el('td', null, cur), el('td'), td);
      tr.children[2].append(input);
      table.append(tr);
    }
    const reason = el('input'); reason.id = 'price-reason'; reason.placeholder = 'nt tarnija hind tõusis';
    const from = el('input'); from.id = 'price-from'; from.type = 'datetime-local';
    const msg = el('div'); msg.id = 'price-msg'; msg.setAttribute('role', 'status');
    const submit = button('Esita hinnamuudatus kinnitamiseks', 'primary-btn', 'price-submit', async () => {
      const proposals = [];
      for (const tr of table.querySelectorAll('tr[data-list]')) {
        const row = p.prices.find((r) => (r.price_list_id == null ? 'base' : String(r.price_list_id)) === tr.dataset.list);
        const notSold = tr.querySelector('.cat-not-sold')?.checked;
        const raw = tr.querySelector('.cat-new-price').value;
        const cents = notSold ? null : parseEuroInput(raw);
        if (!notSold && cents === null) { if (raw.trim()) { msg.replaceChildren(stateBox('error', `Vigane hind: ${raw}`)); return; } continue; }
        if (!notSold && cents === row.price_cents) continue;            // unchanged
        if (notSold && row.override && row.price_cents == null) continue;
        proposals.push({ product_id: p.id, price_list_id: row.price_list_id, price_cents: cents });
      }
      if (!proposals.length) { msg.replaceChildren(stateBox('error', 'Ühtegi hinda ei muudetud.')); return; }
      const valid = from.value ? new Date(from.value).toISOString() : null;
      try {
        for (const x of proposals) {
          await call('/api/v1/pricing/prices', { method: 'POST', body: { ...x, valid_from: valid, reason: reason.value.trim() || null } });
        }
        msg.replaceChildren(stateBox('ok', `Esitatud ${proposals.length} hinda. Need kehtivad pärast teise inimese kinnitust.`));
        announce('Hinnamuudatus esitati kinnitamiseks');
        await render(body);
      } catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
    });
    box.append(table, field('Põhjus', reason), field('Kehtib alates (tühi = kinnitamise hetkest)', from), submit, msg);
    if (p.history.length) {
      const det = el('details'); det.append(el('summary', null, `Hinnaajalugu (${p.history.length})`));
      const ul = el('ul', 'trace-list');
      for (const h of p.history) {
        const list = h.price_list_id == null ? 'Põhihind' : (st.lists.find((l) => l.id === h.price_list_id)?.name || h.price_list_id);
        ul.append(el('li', null, `${list}: ${priceText(h.price_cents)} · ${STATUS_ET[h.status]} · alates ${when(h.valid_from)} · esitas ${h.created_by}${h.decided_by ? `, otsustas ${h.decided_by}` : ''}`));
      }
      det.append(ul); box.append(det);
    }
    return box;
  }

  function newProductBox(body) {
    const box = el('section', 'cat-box'); box.id = 'cat-new';
    const sku = el('input'); sku.id = 'new-sku';
    const name = el('input'); name.id = 'new-name';
    const price = el('input'); price.id = 'new-price'; price.placeholder = '0,00'; price.inputMode = 'decimal';
    const group = select('new-group', groupOptions(), '');
    const tax = select('new-tax', Object.entries(TAX_ET), 'standard');
    const msg = el('div'); msg.id = 'new-msg'; msg.setAttribute('role', 'status');
    const go = button('Lisa toode', 'primary-btn', 'new-submit', async () => {
      const cents = parseEuroInput(price.value);
      if (!sku.value.trim() || !name.value.trim() || cents === null) { msg.replaceChildren(stateBox('error', 'Täida tootenumber, nimi ja hind.')); return; }
      try {
        const { data } = await call('/api/v1/catalog/products', { method: 'POST', body: {
          sku: sku.value.trim(), name: name.value.trim(), base_price_cents: cents,
          group_id: group.value ? Number(group.value) : null, tax_category: tax.value } });
        msg.replaceChildren(stateBox('ok', `Toode ${data.product.sku} lisati. Hind ${formatEuro(cents)} ootab teise inimese kinnitust; enne seda kassas seda ei müüda.`));
        st.product = data.product.id; announce('Toode lisati'); await render(body);
      } catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
    });
    box.append(el('div', 'section-label', 'Uus toode'), field('Tootenumber', sku), field('Nimi', name),
      field('Põhihind (KM-ga)', price), field('Rühm', group), field('Käibemaks', tax), go, msg);
    return box;
  }

  function groupsBox(body) {
    const box = el('section', 'cat-box'); box.id = 'cat-groups';
    const name = el('input'); name.id = 'group-name';
    const parent = select('group-parent', [['', '— ülemine tase —'], ...st.groups.map((g) => [String(g.id), g.name])], '');
    const msg = el('div'); msg.setAttribute('role', 'status');
    const go = button('Lisa rühm', 'secondary-btn', 'group-submit', async () => {
      try {
        await call('/api/v1/catalog/groups', { method: 'POST', body: { name: name.value.trim(), parent_id: parent.value ? Number(parent.value) : null } });
        await render(body);
      } catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
    });
    const ul = el('ul', 'trace-list');
    const byParent = (pid) => st.groups.filter((g) => (g.parent_id ?? null) === pid);
    const walk = (pid, depth) => { for (const g of byParent(pid)) { ul.append(el('li', null, `${'— '.repeat(depth)}${g.name} (${g.product_count})`)); walk(g.id, depth + 1); } };
    walk(null, 0);
    box.append(el('div', 'section-label', 'Tooterühmad'), ul, field('Nimi', name), field('Ülemrühm', parent), go, msg);
    return box;
  }

  function listsBox(body, outlets) {
    const box = el('section', 'cat-box'); box.id = 'cat-lists';
    box.append(el('div', 'section-label', 'Hinnakirjad ja müügipunktid'),
      el('p', 'muted', 'Müügipunkt kasutab oma hinnakirja hindu; kui seal tootele hinda pole, kehtib põhihind. Hinnakirja vahetus mõjutab ainult kinnitatud hindu.'));
    const name = el('input'); name.id = 'list-name';
    const msg = el('div'); msg.setAttribute('role', 'status');
    const add = button('Lisa hinnakiri', 'secondary-btn', 'list-submit', async () => {
      try { await call('/api/v1/pricing/lists', { method: 'POST', body: { name: name.value.trim() } }); await render(body); }
      catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
    });
    const table = el('table', 'cat-table');
    for (const o of outlets) {
      const tr = el('tr'); tr.dataset.outlet = String(o.id);
      const s = select(null, [['', 'Põhihinnad'], ...st.lists.map((l) => [String(l.id), l.name])], o.price_list_id ? String(o.price_list_id) : '');
      s.className = 'outlet-list';
      s.addEventListener('change', async () => {
        try {
          await call(`/api/v1/pricing/outlets/${o.id}`, { method: 'PUT', body: { price_list_id: s.value ? Number(s.value) : null } });
          announce(`${o.name}: hinnakiri muudetud`);
        } catch (e) { msg.replaceChildren(stateBox('error', errText(e))); s.value = o.price_list_id ? String(o.price_list_id) : ''; }
      });
      const td = el('td'); td.append(s);
      tr.append(el('td', null, o.name), td);
      table.append(tr);
    }
    box.append(table, field('Uue hinnakirja nimi', name), add, msg);
    return box;
  }

  return { render };
}
