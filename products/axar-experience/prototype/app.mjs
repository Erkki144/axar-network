import {cartSummary, cashChange, formatEuro, assertCents} from './money.mjs';

// All data below is fictional demo data. It is not fetched from AXAR POS.
const CATEGORIES = ['Kõik', 'Lemmikud', 'Kohv', 'Toit', 'Joogid', 'Muud'];
const PRODUCTS = [
  {id:'kohv',name:'Kohv',priceCents:250,rateBp:2400,category:'Kohv',icon:'☕',tone:'sand',popular:true},
  {id:'latte',name:'Caffè latte',priceCents:380,rateBp:2400,category:'Kohv',icon:'🥛',tone:'mint',popular:true},
  {id:'cappuccino',name:'Cappuccino',priceCents:330,rateBp:2400,category:'Kohv',icon:'☕',tone:'rose',popular:true},
  {id:'saiake',name:'Kaneelisaiake',priceCents:295,rateBp:2400,category:'Toit',icon:'🥐',tone:'rose',popular:true},
  {id:'vesi',name:'Mineraalvesi',priceCents:180,rateBp:2400,category:'Joogid',icon:'💧',tone:'sky',popular:true},
  {id:'croissant',name:'Võicroissant',priceCents:290,rateBp:2400,category:'Toit',icon:'🥨',tone:'sand',popular:false},
  {id:'mahl',name:'Õunamahl',priceCents:270,rateBp:2400,category:'Joogid',icon:'🍎',tone:'rose',popular:true},
  {id:'supp',name:'Päevasupp',priceCents:590,rateBp:2400,category:'Toit',icon:'🥣',tone:'mint',popular:false},
  {id:'võileib',name:'Võileib',priceCents:490,rateBp:2400,category:'Toit',icon:'🥪',tone:'sand',popular:true},
  {id:'tee',name:'Tee',priceCents:200,rateBp:2400,category:'Kohv',icon:'🍵',tone:'mint',popular:false},
  {id:'jäätis',name:'Jäätis',priceCents:340,rateBp:2400,category:'Toit',icon:'🍦',tone:'lilac',popular:false},
  {id:'raamat',name:'Väike raamat',priceCents:1090,rateBp:900,category:'Muud',icon:'📖',tone:'sky',popular:false}
];
const tableNames=['Laud 01','Laud 02','Laud 03','Laud 04','Terrass 01','Terrass 02','Terrass 03','Terrass 04'];
let mode='express';
let category='Kõik';
let selectedTable=0;
let carts=new Map([['walkin',new Map()]]);
let kitchenOrders=[{id:1,table:'Laud 04',items:[{name:'Päevasupp',quantity:2},{name:'Võileib',quantity:1}],status:'valmib',demo:true}];
let nextTicket=2;
// Table checks remain payable after items are sent; only unsent additions fire.
const kitchenSent = new Map();
const $=(id)=>document.getElementById(id);
const cartKey=()=>mode==='tables'?`table:${selectedTable}`:'walkin';
const currentCart=()=>{const k=cartKey();if(!carts.has(k))carts.set(k,new Map());return carts.get(k)};
const announce=(s)=>{$('announcer').textContent=s};
const cartLines=(cart)=>[...cart.entries()].map(([id,quantity])=>{const p=PRODUCTS.find(q=>q.id===id);return {...p,quantity}});
const setText=(id,text)=>{$(id).textContent=text};

function addProduct(id){
 const p=PRODUCTS.find(p=>p.id===id);if(!p)return;
 const c=currentCart();const n=(c.get(id)||0)+1;if(n>999)return;
 c.set(id,n);renderBasket();announce(`${p.name} lisatud`);
}
function changeQuantity(id,delta){const c=currentCart();const n=(c.get(id)||0)+delta;
 if(n<=0)c.delete(id);else if(n<=999)c.set(id,n);renderBasket();}
function renderBasket(){
 const panel=$('checkout-panel');panel.hidden=mode==='kitchen'||mode==='manager';
 document.querySelector('.app-shell').classList.toggle('no-checkout',panel.hidden);
 if(panel.hidden)return;
 const tableMode=mode==='tables';const lines=cartLines(currentCart());
 setText('basket-title',tableMode?tableNames[selectedTable]:'Kassamüük');setText('basket-count',String(lines.reduce((n,x)=>n+x.quantity,0)));
 const box=$('basket-items');box.replaceChildren();
 if(!lines.length){const empty=document.createElement('div');empty.className='empty-basket';empty.innerHTML='<span aria-hidden="true">◇</span><strong>Alusta tellimust</strong><small>Puuduta toodet, et see ostukorvi lisada.</small>';box.append(empty)}
 else for(const item of lines){
  const row=document.createElement('div');row.className='cart-row';
  const icon=document.createElement('span');icon.className='cart-icon';icon.textContent=item.icon;
  const body=document.createElement('div');body.className='cart-body';
  const name=document.createElement('div');name.className='cart-name';name.textContent=item.name;
  const description=document.createElement('div');description.className='cart-detail';description.textContent=`${formatEuro(item.priceCents)} / tk`;
  const ctr=document.createElement('div');ctr.className='cart-controls';
  const minus=document.createElement('button');minus.className='qty-btn';minus.type='button';minus.textContent='−';minus.setAttribute('aria-label',`Vähenda: ${item.name}`);minus.addEventListener('click',()=>changeQuantity(item.id,-1));
  const number=document.createElement('span');number.className='qty-count';number.textContent=String(item.quantity);
  const plus=document.createElement('button');plus.className='qty-btn';plus.type='button';plus.textContent='+';plus.setAttribute('aria-label',`Lisa: ${item.name}`);plus.addEventListener('click',()=>changeQuantity(item.id,1));
  ctr.append(minus,number,plus);body.append(name,description,ctr);
  const price=document.createElement('strong');price.className='cart-price';price.textContent=formatEuro(item.priceCents*item.quantity);
  row.append(icon,body,price);box.append(row);
 }
 const s=cartSummary(lines);
 setText('subtotal',formatEuro(s.totalCents));setText('vat',formatEuro(s.vatCents));
 const round=s.cashRoundingCents;setText('rounding',`${round>0?'+':round<0?'−':''}${formatEuro(Math.abs(round))}`);
 setText('total',formatEuro(s.cashDueCents));
 $('pay-button').disabled=!lines.length;$('send-button').hidden=!tableMode;
 if(tableMode){const sent=kitchenSent.get(cartKey())||new Map();const hasUnsent=lines.some(x=>x.quantity>(sent.get(x.id)||0));$('send-button').disabled=!hasUnsent;$('send-button').textContent=hasUnsent?'Saada kööki ↗':'Kõik tooted kööki saadetud ✓'}
}
function activateMode(next){
 if(!['express','tables','kitchen','manager'].includes(next))return;
 mode=next;category='Kõik';
 document.querySelectorAll('.nav-item').forEach(btn=>{const on=btn.dataset.mode===next;btn.classList.toggle('active',on);if(on)btn.setAttribute('aria-current','page');else btn.removeAttribute('aria-current')});
 const copy={
  express:['MÜÜGI TÖÖLAUD','Kiire teenindus.<br><em>Vähem samme.</em>','Tooted, tellimused ja maksmine ühes vaates.','Kõige kiiremini valitavad kaubad on esimesena.'],
  tables:['RESTORANI TÖÖLAUD','Laudade teenindus.<br><em>Üks selge voog.</em>','Vali laud, lisa tooted ja saada tellimus kööki.','Lauapõhine ostukorv säilib vahetamise ajal.'],
  kitchen:['KÖÖGI TÖÖLAUD','Kõik tellimused.<br><em>Õiges järjekorras.</em>','Tellimuse olek ja sisu on kohe nähtavad.','Köögivaade ei vaja kassapidaja maksenuppe.'],
  manager:['JUHI TÖÖLAUD','Ülevaade ärist.<br><em>Ilma mürata.</em>','Olulised erandid ja põhitegevused ühes kohas.','Näita juhile erandeid enne keerulisi graafikuid.']
 }[next];
 setText('section-eyebrow',copy[0]);$('section-title').innerHTML=copy[1];setText('section-subtitle',copy[2]);setText('hint-message',copy[3]);
 if(next==='kitchen')renderKitchen();else if(next==='manager')renderManager();else if(next==='tables')renderTables();else renderExpress();
 renderBasket();
}
function renderProductGrid(root){
 const wrapper=document.createElement('section');wrapper.id='product-module';
 wrapper.innerHTML=`<div class="toolbar"><div><div class="section-label">Menüü</div><div class="section-caption">Vali toode ühe puudutusega</div></div><label class="search-wrap"><span aria-hidden="true">⌕</span><input id="product-search" type="search" placeholder="Otsi toodet... (/)" aria-label="Otsi toodet" autocomplete="off"></label></div><div class="category-bar" id="category-bar"></div><div class="product-grid" id="product-grid"></div>`;
 root.append(wrapper);
 const cats=$('category-bar');for(const c of CATEGORIES){const b=document.createElement('button');b.type='button';b.className=`category-btn${c===category?' active':''}`;b.textContent=c;b.addEventListener('click',()=>{category=c;cats.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.textContent===c));filterProducts()});cats.append(b)};
 const grid=$('product-grid');for(const p of PRODUCTS){const b=document.createElement('button');b.className='product';b.type='button';b.dataset.product=p.id;b.dataset.category=p.category;b.dataset.popular=p.popular?'yes':'no';b.dataset.name=p.name.toLocaleLowerCase('et');b.setAttribute('aria-label',`Lisa ostukorvi: ${p.name}, ${formatEuro(p.priceCents)}`);
 const media=document.createElement('div');media.className=`product-media ${p.tone}`;media.setAttribute('aria-hidden','true');media.textContent=p.icon;
 const name=document.createElement('span');name.className='product-name';name.textContent=p.name;
 const price=document.createElement('span');price.className='product-price';price.textContent=formatEuro(p.priceCents);
 b.append(media,name,price);b.addEventListener('click',()=>addProduct(p.id));grid.append(b)}
 $('product-search').addEventListener('input',filterProducts);filterProducts();
}
function filterProducts(){const input=$('product-search');if(!input)return;const query=input.value.toLocaleLowerCase('et').trim();let visible=0;document.querySelectorAll('#product-grid .product').forEach(b=>{const okCat=category==='Kõik'||category==='Lemmikud'&&b.dataset.popular==='yes'||category===b.dataset.category;const okText=b.dataset.name.includes(query);b.hidden=!(okCat&&okText);if(!b.hidden)visible++});
 let empty=$('no-products');if(visible===0&&!empty){empty=document.createElement('p');empty.id='no-products';empty.className='muted-p';empty.textContent='Tooteid ei leitud. Proovi teist otsingut.';$('product-module').append(empty)}if(empty)empty.hidden=visible!==0;}
function renderExpress(){const root=$('view-root');root.replaceChildren();renderProductGrid(root)}
function renderTables(){const root=$('view-root');root.replaceChildren();const section=document.createElement('section');section.innerHTML='<div class="toolbar"><div><div class="section-label">Saali plaan</div><div class="section-caption">Näidis – 8 lauda · ei ole seotud päris restoraniga</div></div></div><div class="table-grid" id="table-grid"></div><div class="table-actions"><button type="button" class="outline-button" id="jump-to-menu">↓ Vali tooted</button></div>';root.append(section);
 const grid=$('table-grid');tableNames.forEach((name,i)=>{const occupied=carts.get(`table:${i}`)?.size>0||kitchenOrders.some(o=>o.table===name&&o.status!=='valmis');const b=document.createElement('button');b.type='button';b.className=`table-card${i===selectedTable?' selected':''}${occupied?' occupied':''}`;b.innerHTML=`<strong></strong><small></small>`;b.querySelector('strong').textContent=name;b.querySelector('small').textContent=occupied?'Tellimus käimas':'Vaba';b.addEventListener('click',()=>{selectedTable=i;renderTables();renderBasket();announce(`Valitud ${name}`)});grid.append(b)});
 renderProductGrid(root);$('jump-to-menu').addEventListener('click',()=>{$('product-module').scrollIntoView({behavior:'smooth',block:'start'})});}
function renderKitchen(){const root=$('view-root');root.replaceChildren();const title=document.createElement('div');title.className='toolbar';title.innerHTML='<div><div class="section-label">Köögi tellimused</div><div class="section-caption">Simuleeritud tellimused, ilma päris köögiühenduseta</div></div>';root.append(title);
 const grid=document.createElement('div');grid.className='kds-grid';for(const order of kitchenOrders){const ticket=document.createElement('article');ticket.className='ticket';const head=document.createElement('div');head.className='ticket-head';const name=document.createElement('strong');name.textContent=order.table;const tag=document.createElement('small');tag.textContent=order.status==='valmis'?'VALMIS':'VALMIB';head.append(name,tag);const list=document.createElement('ul');list.className='ticket-list';for(const item of order.items){const li=document.createElement('li');li.textContent=`${item.quantity} × ${item.name}`;list.append(li)}const button=document.createElement('button');button.className=`ticket-ready${order.status==='valmis'?' done':''}`;button.textContent=order.status==='valmis'?'Valmis ✓':'Märgi valmis';button.disabled=order.status==='valmis';button.addEventListener('click',()=>{order.status='valmis';renderKitchen();announce('Tellimus valmis')});ticket.append(head,list,button);grid.append(ticket)}root.append(grid)}
function renderManager(){const root=$('view-root');root.replaceChildren();const heading=document.createElement('div');heading.className='toolbar';heading.innerHTML='<div><div class="section-label">Päeva ülevaade</div><div class="section-caption">KÕIK ALLJÄRGNEVAD NUMBRID ON VÄLJAMÕELDUD NÄIDISED</div></div>';root.append(heading);
 const grid=document.createElement('div');grid.className='stats-grid';for(const [name,val,detail] of [['Näidiskäive','1 240 €','Ei pärine AXAR POS-ilt'],['Näidistellimused','86','Simuleeritud, ilma makseteta'],['Avatud juhtumid','3','Näidiserandid, mitte päris probleemid']]){const div=document.createElement('div');div.className='stat';const label=document.createElement('small');label.textContent=name;const strong=document.createElement('strong');strong.textContent=val;const p=document.createElement('p');p.textContent=detail;div.append(label,strong,p);grid.append(div)}root.append(grid);
 const note=document.createElement('div');note.className='manager-note';note.textContent='Päris juhipaneel näitaks siin eelkõige erandeid: kassavahed, laekumata maksed, laoseisu ohud, ühenduste katkestused ja kinnitamist vajavad AI-soovitused. Need väärtused saabuvad alles turvalise backend-integratsiooni järel.';root.append(note)}
function sendToKitchen(){if(mode!=='tables')return;const lines=cartLines(currentCart());if(!lines.length)return;
 const key=cartKey();const sent=kitchenSent.get(key)||new Map();const additions=lines.filter(x=>x.quantity>(sent.get(x.id)||0)).map(x=>({id:x.id,name:x.name,quantity:x.quantity-(sent.get(x.id)||0)}));
 if(!additions.length)return;for(const x of lines)sent.set(x.id,x.quantity);kitchenSent.set(key,sent);
 kitchenOrders.unshift({id:nextTicket++,table:tableNames[selectedTable],items:additions.map(x=>({name:x.name,quantity:x.quantity})),status:'valmib',demo:true});
 renderTables();renderBasket();announce('Näidistellimus saadetud köögivaatesse; arve jäi avatuks');}
function openCashModal(){const lines=cartLines(currentCart());if(!lines.length)return;const s=cartSummary(lines);const overlay=$('modal');overlay.hidden=false;const content=$('modal-content');content.replaceChildren();
 const title=document.createElement('h2');title.id='dialog-title';title.textContent='Sularahamakse · DEMO';
 const sub=document.createElement('p');sub.className='muted';sub.textContent='See on üksnes kasutajaliidese proov. Päris makset ei toimu.';
 const amount=document.createElement('div');amount.className='amount';amount.textContent=formatEuro(s.cashDueCents);
 const label=document.createElement('label');label.htmlFor='tender-input';label.textContent='Kliendilt saadud (€)';label.className='section-label';
 const input=document.createElement('input');input.type='text';input.inputMode='decimal';input.className='tender-input';input.id='tender-input';input.value=(s.cashDueCents/100).toFixed(2).replace('.',',');
 const quick=document.createElement('div');quick.className='quick-amounts';for(const n of [10,20,50]){const b=document.createElement('button');b.type='button';b.textContent=`${n} €`;b.addEventListener('click',()=>{input.value=`${n},00`;updateTender()});quick.append(b)}
 const msg=document.createElement('div');msg.className='dialog-error';msg.id='payment-error';msg.setAttribute('role','alert');
 const change=document.createElement('div');change.className='summary-line';
 const txt=document.createElement('span');txt.textContent='Tagastusraha';const value=document.createElement('strong');change.append(txt,value);
 const confirm=document.createElement('button');confirm.className='primary-btn';confirm.type='button';confirm.textContent='Kinnita näidismakse';
 function parseCash(){const v=input.value.trim().replace(',','.');if(!/^\d+(?:\.\d{1,2})?$/.test(v))throw new TypeError('Sisesta rahasumma, nt 10,00');const [a,b='']=v.split('.');const cents=Number(a)*100+Number(b.padEnd(2,'0'));return assertCents(cents)}
 function updateTender(){try{const cents=parseCash();const cash=cashChange(s.totalCents,cents);value.textContent=formatEuro(cash);msg.textContent='';confirm.disabled=false;}catch(e){value.textContent='—';msg.textContent=e instanceof RangeError?'Sularahast ei piisa.':'Sisesta korrektne summa.';confirm.disabled=true}}
 input.addEventListener('input',updateTender);
 confirm.addEventListener('click',()=>{try{const tender=parseCash();const changeCents=cashChange(s.totalCents,tender);const key=cartKey();const summary=cartSummary(cartLines(currentCart()));carts.get(key).clear();kitchenSent.delete(key);renderBasket();openReceiptModal(lines,summary,tender,changeCents)}catch(e){updateTender()}});
 content.append(title,sub,amount,label,input,quick,msg,change,confirm);updateTender();input.focus();}
function openReceiptModal(lines,s,tender,change){const content=$('modal-content');content.replaceChildren();const title=document.createElement('h2');title.id='dialog-title';title.textContent='Näidismakse kinnitatud';const subtitle=document.createElement('p');subtitle.className='muted';subtitle.textContent='DEMO – pärismakset, arvet ega makseterminali kinnitust ei tekkinud.';
 const ticket=document.createElement('div');ticket.className='receipt';ticket.textContent=['AXAR ONE · SIMULEERITUD TŠEKK','--------------------------------',...lines.map(x=>`${x.quantity} × ${x.name}    ${formatEuro(x.priceCents*x.quantity)}`),'--------------------------------',`Kokku: ${formatEuro(s.totalCents)}`,`Käibemaks: ${formatEuro(s.vatCents)}`,`Sularaha kokku: ${formatEuro(s.cashDueCents)}`,`Antud: ${formatEuro(tender)}`,`Tagasi: ${formatEuro(change)}`,'KÕIK ANDMED ON NÄIDISANDMED'].join('\n');
 const done=document.createElement('button');done.className='primary-btn';done.textContent='Järgmine klient';done.addEventListener('click',closeModal);content.append(title,subtitle,ticket,done);announce('Näidismakse tehtud');}
function closeModal(){$('modal').hidden=true;$('pay-button').focus()}
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>activateMode(b.dataset.mode)));
$('pay-button').addEventListener('click',openCashModal);
$('send-button').addEventListener('click',sendToKitchen);
$('close-modal').addEventListener('click',closeModal);
$('modal').addEventListener('click',(event)=>{if(event.target===$('modal'))closeModal()});
document.addEventListener('keydown',(event)=>{if(event.key==='Escape'&&!$('modal').hidden)closeModal();if(event.key==='/'&&$('modal').hidden){const input=$('product-search');if(input&&document.activeElement!==input){event.preventDefault();input.focus()}}});
$('local-clock').textContent=new Intl.DateTimeFormat('et-EE',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Tallinn'}).format(new Date());
activateMode('express');
