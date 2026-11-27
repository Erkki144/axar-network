/* AXAR ATLAS v0.1: static, sourced architecture map, not live system monitoring. */
(() => {
  "use strict";
  const d=window.AXAR_ATLAS_DATA;
  if(!d||!Array.isArray(d.nodes)||!Array.isArray(d.edges)){document.body.textContent="AXAR ATLAS: arhitektuuri lähteandmed puuduvad.";return;}
  const $=id=>document.getElementById(id);
  const url="https://github.com/Erkki144/axar-network";
  const ns="http://www.w3.org/2000/svg";
  const index=new Map(d.nodes.map(n=>[n.id,n]));
  const state={view:"architecture",search:"",filter:"all",selected:null,flow:null,zoom:1,panX:0,panY:0};
  const NW=191,NH=84;
  function elem(tag,cls,txt){const a=document.createElement(tag);if(cls)a.className=cls;if(txt!=null)a.textContent=txt;return a;}
  function shape(tag,attrs,txt){const a=document.createElementNS(ns,tag);Object.entries(attrs||{}).forEach(([k,v])=>a.setAttribute(k,String(v)));if(txt!=null)a.textContent=txt;return a;}
  function aLink(txt,href){const a=elem("a",null,txt);a.href=href;a.target="_blank";a.rel="noopener noreferrer";return a;}
  function ghPath(path){return url+(path.endsWith("/terminals")?"/tree/main/":"/blob/main/")+path.split("/").map(encodeURIComponent).join("/");}
  function createNodes(){
    const root=$("graph-nodes");root.replaceChildren();
    d.nodes.forEach(n=>{
      const label=n.title+": "+n.subtitle+"; "+d.statuses[n.status].label;
      const g=shape("g",{class:"node",transform:"translate("+n.x+","+n.y+")","data-id":n.id,"data-status":n.status,tabindex:"0",role:"button","aria-label":label});
      g.append(shape("rect",{class:"node-outer",width:NW,height:NH,rx:10}));
      g.append(shape("rect",{class:"node-accent",x:0,y:0,width:4,height:NH,rx:2}));
      g.append(shape("text",{class:"node-kind",x:16,y:19},n.kind.toUpperCase()));
      g.append(shape("text",{class:"node-title",x:16,y:43},n.title));
      g.append(shape("text",{class:"node-desc",x:16,y:64},n.subtitle.length>27?n.subtitle.slice(0,27)+"…":n.subtitle));
      g.append(shape("circle",{class:"node-stub",cx:NW-12,cy:12,r:3}));
      g.addEventListener("click",()=>selectNode(n.id));
      g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();selectNode(n.id);}});
      root.append(g);
    });
  }
  function createEdges(){
    const root=$("graph-edges");root.replaceChildren();
    d.edges.forEach((edge,i)=>{
      const x=index.get(edge.from),y=index.get(edge.to);if(!x||!y)return;
      let ax,ay,bx,by;
      if(y.x>=x.x+NW-40){ax=x.x+NW;ay=x.y+NH/2;bx=y.x;by=y.y+NH/2;}
      else if(x.x>=y.x+NW-40){ax=x.x;ay=x.y+NH/2;bx=y.x+NW;by=y.y+NH/2;}
      else{ax=x.x+NW/2;ay=x.y+NH;bx=y.x+NW/2;by=y.y;}
      const dx=Math.max(35,Math.abs(ax-bx)*.46),sign=bx>ax?1:-1;
      const path="M "+ax+" "+ay+" C "+(ax+sign*dx)+" "+ay+", "+(bx-sign*dx)+" "+by+", "+bx+" "+by;
      const p=shape("path",{class:"edge"+(edge.status==="planned"?" plan":""),"data-index":i,d:path,"aria-label":edge.label});
      p.addEventListener("click",()=>selectNode(edge.to));
      root.append(p);
    });
  }
  function showDetails(n){
    const root=$("inspector-body");root.replaceChildren();
    root.append(elem("div","detail-label",n.kind.toUpperCase()+" / "+n.id.toUpperCase()));
    root.append(elem("h2","detail-title",n.title),elem("p","detail-subtitle",n.subtitle));
    root.append(elem("span","status-pill "+n.status,d.statuses[n.status].label));
    const description=elem("div","detail-section");
    description.append(elem("h3",null,"MILLE EEST VASTUTAB"),elem("p",null,n.description));root.append(description);
    const own=elem("div","detail-section");own.append(elem("h3",null,"VASTUTAJA"),elem("span","pill",n.owner));root.append(own);
    const related=d.edges.filter(e=>e.from===n.id||e.to===n.id);
    const connections=elem("div","detail-section");connections.append(elem("h3",null,"ÜHENDUSED ("+related.length+")"));
    related.forEach(e=>{
      const t=index.get(e.from===n.id?e.to:e.from),row=elem("div","connection");
      row.append(elem("span",null,e.from===n.id?"→ ":"← "),document.createTextNode((t?t.title:"?")+" · "+e.label));
      connections.append(row);
    });root.append(connections);
    const src=elem("div","detail-section");src.append(elem("h3",null,"LÄHTEKOOD ("+n.sources.length+")"));
    const files=elem("div","source-list");
    if(!n.sources.length)files.append(elem("span","pill","Veel kavandatud"));
    n.sources.forEach(p=>files.append(aLink(p,ghPath(p))));src.append(files);root.append(src);
    const issues=elem("div","detail-section");issues.append(elem("h3",null,"TÖÖÜLESANDED"));
    const tags=elem("div","issue-tags");n.issues.forEach(i=>tags.append(aLink("#"+i,url+"/issues/"+i)));issues.append(tags);root.append(issues);
    const truth=elem("div","detail-section");truth.append(elem("h3",null,"TÕENDITE OLEK"),elem("p",null,"Arhitektuuri hetkpilt, mitte käituse mõõtmine. Roheline koodistaatus ei tähenda tootmisvalmis teenust."));root.append(truth);
  }
  function selectNode(id){const n=index.get(id);if(!n)return;state.selected=id;showDetails(n);focus();}
  function currentFlow(){return d.flows.find(x=>x.id===state.flow)||null;}
  function focus(){
    const q=state.search.trim().toLocaleLowerCase("et"),flow=currentFlow(),matches=new Set(),active=new Set(flow?flow.nodes:(state.selected?[state.selected]:[]));
    d.nodes.forEach(n=>{if(!q||[n.title,n.subtitle,n.owner,n.description].join(" ").toLocaleLowerCase("et").includes(q))matches.add(n.id);});
    if(state.selected&&!flow)d.edges.forEach(e=>{if(e.from===state.selected||e.to===state.selected){active.add(e.from);active.add(e.to);}});
    document.querySelectorAll(".node").forEach(g=>{
      const id=g.getAttribute("data-id");
      g.classList.toggle("dim",!matches.has(id)||(!!flow&&!active.has(id))||(!!state.selected&&!flow&&!active.has(id)));
      g.classList.toggle("selected",id===state.selected);
    });
    document.querySelectorAll(".edge").forEach(p=>{
      const e=d.edges[Number(p.getAttribute("data-index"))],ok=state.filter==="all"||e.type===state.filter;
      const selected=flow?active.has(e.from)&&active.has(e.to):state.selected&&(e.from===state.selected||e.to===state.selected);
      const visible=ok&&matches.has(e.from)&&matches.has(e.to)&&(!flow||selected)&&(!state.selected||flow||selected);
      p.classList.toggle("dim",!visible);p.classList.toggle("focus",!!selected&&visible);
    });
    document.querySelectorAll(".flow-btn").forEach(b=>b.classList.toggle("active",b.dataset.flow===state.flow));
  }
  function setView(view){
    if(!["architecture","journeys","registers","gates"].includes(view))return;
    state.view=view;
    ["architecture","journeys","registers","gates"].forEach(id=>$(id+"-view").hidden=id!==view);
    const titles={
      architecture:["Kogu süsteem. Üks vaade.","Moodulid, andmete autoriteedid, sündmused ja sõltuvused ühes kontrollitavas struktuuris."],
      journeys:["Teekond läbi süsteemi.","Milliseid mooduleid tegevus läbima peab ja kus on kinnitamata ühendused."],
      registers:["Iga registri eest vastutaja.","Ühendused päris lähtefailidega. Ei ole käituse automaatne audit."],
      gates:["Valvurid, mitte lubadused.","Töökindlus peab põhinema läbitud testidel ja eraldi tõenditel."]
    };
    $("view-title").textContent=titles[view][0];$("view-description").textContent=titles[view][1];
    document.querySelectorAll(".nav-btn").forEach(b=>{
      const isActive=b.dataset.view===view;b.classList.toggle("active",isActive);
      if(isActive)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
    });
  }
  function setFlow(id){
    state.flow=state.flow===id?null:id;state.selected=null;
    if(state.flow){
      setView("architecture");
      const f=currentFlow(),root=$("inspector-body");root.replaceChildren();
      root.append(elem("div","detail-label","TEEKOND / ARHITEKTUUR"),elem("h2","detail-title",f.label),elem("p","detail-subtitle",f.subtitle),elem("span","status-pill planned","MITTE LIVE TEHING"));
      const intro=elem("div","detail-section");intro.append(elem("h3",null,"TÕENDI ULATUS"),elem("p",null,f.note));root.append(intro);
      const steps=elem("div","detail-section");steps.append(elem("h3",null,"TEEKONNA MOODULID"));
      f.nodes.forEach((nodeId,i)=>{const n=index.get(nodeId),b=elem("button","flow-step",(i+1)+". "+n.title);b.type="button";b.addEventListener("click",()=>selectNode(n.id));steps.append(b);});
      root.append(steps);
    }
    focus();
  }
  function fillLists(){
    $("metric-nodes").textContent=d.nodes.length;$("metric-edges").textContent=d.edges.length;
    $("metric-pending").textContent=d.nodes.filter(n=>n.status!=="code").length;
    $("graph-count").textContent=d.nodes.length+" sõlme / "+d.edges.length+" sidet";
    d.flows.forEach(f=>{
      const b=elem("button","flow-btn",f.label);b.type="button";b.dataset.flow=f.id;b.addEventListener("click",()=>setFlow(f.id));$("flow-nav").append(b);
      const card=elem("article","journey-card");card.append(elem("small",null,f.subtitle.toUpperCase()),elem("h3",null,f.label),elem("p",null,f.note));
      const steps=elem("div","journey-steps");f.nodes.forEach(id=>steps.append(elem("span",null,index.get(id)?.title||id)));card.append(steps);
      const open=elem("button",null,"Ava arhitektuurikaardil");open.type="button";open.addEventListener("click",()=>{state.flow=null;setFlow(f.id);});
      card.append(open);$("journeys-list").append(card);
    });
    d.nodes.forEach(n=>{
      const row=elem("tr"),name=elem("td"),b=elem("button",null,n.title);
      b.type="button";b.addEventListener("click",()=>{setView("architecture");selectNode(n.id);});name.append(b);
      row.append(name,elem("td",null,n.owner),elem("td",null,d.statuses[n.status].label),elem("td",null,n.sources[0]||"Kavandatud / mustand"));
      $("registry-table").append(row);
    });
    const gates=[
      ["G01 / SÜNDMUSTE LEPING","Iga sündmus peab vastama versioonitud skeemile.","Koodis test: event contracts","/blob/main/products/axar-pos/tests/test_event_contracts.py"],
      ["G02 / TABELI VASTUTUS","Igal tabelil peab olema autoriteet ja äriregistrite kontroll.","Mustand: PR #74 guard","/pull/74"],
      ["G03 / MIGRATSIOONID","Alembic graaf peab toimima ka paralleelsete PR-ide ühendamisel.","Mustand: #74 / #75","/pull/75"],
      ["G04 / IDMPOTENTSUS","Sama müügikäsku ei tohi arvestada kaks korda.","Koodis testid, päris riistvara eraldi","/tree/main/products/axar-pos/tests"],
      ["G05 / OFFLINE","Füüsiline voolukatkestus, krüptimine, taastamine.","Tõend puudu: #72","/issues/72"],
      ["G06 / PÄRIS MAKSED","Terminali tundmatu olek, päris kinnitused ja tagastused.","Pakkuja HIL test puudu: #25","/issues/25"],
      ["G07 / REAALAJAS SEIRE","Sündmuse jälg, vead, järjekordade vanus, alarmid.","Kavandatud: #42","/issues/42"],
      ["G08 / ERALDI TÖÖKESKKONNAD","Kassa sessioon ei tohi olla kontori sessioon ega vastupidi.","Kavandatud: #76","/issues/76"]
    ];
    gates.forEach(([title,body,state,path])=>{
      const card=elem("article","gate-card");
      card.append(elem("strong",null,title),elem("p",null,body),elem("small",null,state),aLink("Vaata tõendit ↗",url+path));
      $("gates-list").append(card);
    });
  }
  function transform(){
    $("graph-pan").setAttribute("transform","translate("+state.panX+" "+state.panY+") scale("+state.zoom+")");
    $("zoom-fit").textContent=Math.round(state.zoom*100)+"%";
  }
  function zoom(num){state.zoom=Math.min(2.4,Math.max(.5,Math.round(num*100)/100));transform();}
  function interactions(){
    document.querySelectorAll(".nav-btn").forEach(b=>b.addEventListener("click",()=>setView(b.dataset.view)));
    $("graph-search").addEventListener("input",e=>{state.search=e.target.value;focus();});
    $("edge-filter").addEventListener("change",e=>{state.filter=e.target.value;focus();});
    $("zoom-in").addEventListener("click",()=>zoom(state.zoom+.2));
    $("zoom-out").addEventListener("click",()=>zoom(state.zoom-.2));
    $("zoom-fit").addEventListener("click",()=>{state.zoom=1;state.panX=0;state.panY=0;transform();});
    const canvas=$("network");let drag=null;
    canvas.addEventListener("pointerdown",e=>{
      if(e.target.closest(".node"))return;
      drag={x:e.clientX,y:e.clientY,px:state.panX,py:state.panY,id:e.pointerId};
      canvas.setPointerCapture(e.pointerId);canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove",e=>{
      if(!drag||e.pointerId!==drag.id)return;
      const k=1490/Math.max(300,canvas.clientWidth);
      state.panX=drag.px+(e.clientX-drag.x)*k;state.panY=drag.py+(e.clientY-drag.y)*k;transform();
    });
    const stop=()=>{drag=null;canvas.classList.remove("dragging");};
    canvas.addEventListener("pointerup",stop);canvas.addEventListener("pointercancel",stop);
    canvas.addEventListener("wheel",e=>{e.preventDefault();zoom(state.zoom*(e.deltaY<0?1.1:.9));},{passive:false});
    window.addEventListener("keydown",e=>{if(e.key==="Escape"&&state.flow){state.flow=null;focus();}});
  }
  createEdges();createNodes();fillLists();interactions();transform();focus();
  Object.defineProperty(window,"AXAR_ATLAS_TEST",{value:Object.freeze({nodes:d.nodes.length,edges:d.edges.length,hasLiveData:d.meta.runtimeConnected}),writable:false});
})();
