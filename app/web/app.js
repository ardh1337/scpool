const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Nilai tak tepercaya di dalam handler inline: JSON memberi literal string JS yang aman, lalu di-escape untuk atribut HTML.
const jq=v=>esc(JSON.stringify(String(v)));
const IC={
  dashboard:'M4 4h7v7H4zM13 4h7v4h-7zM13 11h7v9h-7zM4 14h7v6H4z',
  sms:'M4 5h16v11H9l-5 4z',
  pengaturan:'M12 9a3 3 0 100 6 3 3 0 000-6zM19 12l2-1-2-4-2 1-2-1-1-2h-4l-1 2-2 1-2-1-2 4 2 1v2l-2 1 2 4 2-1 2 1 1 2h4l1-2 2-1 2 1 2-4-2-1z',
  pengguna:'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75'
};
const ic=n=>`<svg class="i" viewBox="0 0 24 24"><path d="${IC[n]||IC.dashboard}"/></svg>`;

/* State Multi-Tenant & Otentikasi */
let currentUser = null;
let activePoolId = null;
let poolsList = [];

function renderNav(){
  const navItems = [
    ['dashboard', 'Dashboard'],
    ['sms', 'Pesan SMS'],
    ['pengaturan', 'Pengaturan']
  ];
  if(currentUser && (currentUser.role === 'superadmin' || currentUser.role === 'admin')){
    navItems.push(['pengguna', 'Kelola Pengguna']);
  }
  $('#nav').innerHTML = navItems.map(([k,l])=>`<a href="#/${k}" data-k="${k}">${ic(k)}${l}</a>`).join('');
}
renderNav();

/* ---------- State & Integrasi Backend Live ---------- */
const ST={active:['Aktif','ok'],empty:['Kosong','info'],offline:['Offline','warn'],error:['Error','err']};
const NOTE={active:'SIM terdeteksi dan terdaftar.',empty:'Tidak ada SIM terpasang.',offline:'Modem tidak merespons pada polling terakhir.',error:'Port melaporkan kesalahan hardware.'};

let slots = [];
let sms = [];
let operatorProfiles = [];
let poolInfo = {
 pool: 'Belum ada modem pool',
 model: 'Modul seluler',
 chipset: 'USB serial'
};

const selectedSlots = new Set();
let isBulkCancelled = false;
let isSelectMode = false;
let currentDrawerSlot = null;
let currentThemeMode = (() => {
 try{ const t = localStorage.getItem('scpool_theme'); return ['auto','dark','light'].includes(t) ? t : 'auto'; }catch(e){ return 'auto'; }
})();
let userCredentials = { username: '' };
let isPasswordVisible = false;
let currentApiLang = 'curl';
let currentDashMode = localStorage.getItem('scpool_dash_mode') || localStorage.getItem('simfleet_dash_mode') || 'grid';
let soundEnabled = (localStorage.getItem('scpool_sound') ?? localStorage.getItem('simfleet_sound')) !== 'false';
let lastSeenOtpIds = new Set();
let fastNumFilterOp = 'all';

const S={sms:{q:'',slot:'all',st:'all',sel:null},labels:{},
 set:{org:'Foxcom 32-Port Pool',tz:'Asia/Jakarta (WIB)',theme:'auto'}};
const v=$('#view'),dlg=$('#dlg'),dr=$('#dr');

const ago=m=>m<1?'Baru saja':m<60?m+' mnt lalu':m<1440?Math.floor(m/60)+' jam lalu':Math.floor(m/1440)+' hari lalu';
const bd=st=>`<span class="sb-pill" style="display:inline-block;">${(ST[st]||ST.empty)[0]}</span>`;
const cnt=()=>slots.reduce((a,s)=>{const k=s.st in a?s.st:'empty';a[k]++;return a},{active:0,empty:0,offline:0,error:0});

/* Deteksi kode OTP cerdas berbasis konteks kata kunci dan filter data non-OTP */
function extractOTP(text){
 if(!text || typeof text !== 'string') return null;
 
 const kwRegex = /\b(otp|kode(?!\s*promo|\s*voucher)|code(?!\s*promo|\s*voucher)|verifikasi|verification|passcode|one-time|rahasia|pin|konfirmasi|confirmation|security\s+code)\b/i;
 if(!kwRegex.test(text)) return null;

 const gMatch = text.match(/\b(?:G-|c-)([0-9]{6})\b/i);
 if(gMatch) return gMatch[1];

 const preMatch = text.match(/\b([0-9]{3}[-\s]?[0-9]{3}|[0-9]{4,8})\b\s*(?:adalah|is\s+(?:your\s+)?[\w\s]{0,25}?(?:code|kode|otp|pin|passcode|verifikasi|verification|password))/i);
 if(preMatch){
  const c = preMatch[1].replace(/[-\s]/g, '');
  if(!/^(?:199\d|20[2-3]\d)$/.test(c)) return c;
 }

 const postMatch = text.match(/(?:otp|kode|code|pin|passcode|verifikasi|verification|security\s+code)[^\d\n:=]{0,35}[:=is|adalah\-]?\s*([0-9]{3}[-\s]?[0-9]{3}|[0-9]{4,8})\b/i);
 if(postMatch){
  const c = postMatch[1].replace(/[-\s]/g, '');
  if(!/^(?:199\d|20[2-3]\d)$/.test(c)) return c;
 }

 if(/\b(otp|verifikasi|verification|rahasia)\b/i.test(text)){
  const candidates = text.match(/\b[0-9]{4,8}\b/g) || [];
  for(const c of candidates){
   if(/^(?:199\d|20[2-3]\d)$/.test(c)) continue;
   if(/^\d000+$/.test(c)) continue;
   if(c.startsWith('08') || c.startsWith('62')) continue;
   if(new RegExp('(?:rp\\.?|idr)\\s*' + c, 'i').test(text)) continue;
   if(new RegExp(c + '\\s*(?:gb|mb|kb|hr|hari|bln|bulan|thn|tahun|menit|mnt|detik|sec)', 'i').test(text)) continue;
   if(new RegExp('(?:sn|ref|trx|id|no)\\s*[:=.]?\\s*' + c, 'i').test(text)) continue;
   return c;
  }
 }

 return null;
}

/* ---------- Pengambilan Data Nyata dari Backend (/api/...) ---------- */
const ALL_POOLS = '__all__';
const domId = id => String(id).replace(/[^\w-]/g, '_');
const slotLabel = s => 'P' + String(s.slot_index).padStart(2, '0');
const poolQuery = (poolId, first = '?') => poolId ? `${first}pool_id=${encodeURIComponent(poolId)}` : '';
const isAdminUser = () => !!currentUser && (currentUser.role === 'superadmin' || currentUser.role === 'admin');

function visiblePools(){
 if(activePoolId !== ALL_POOLS) return poolsList.filter(p => p.id === activePoolId);
 const own = isAdminUser() ? poolsList.filter(p => !p.owner || p.owner === currentUser.username) : poolsList;
 return own.length ? own : poolsList;
}

function mapSlot(s, pool){
 let st = 'empty';
 if(s.status === 'READY') st = 'active';
 else if(s.status === 'SIM_ERROR') st = 'error';
 else if(s.status === 'PORT_ERROR' || s.status === 'OFFLINE') st = 'offline';
 const numDisplay = (st === 'active' && s.phone_number) ? s.phone_number : (st === 'active' ? 'BELUM DIKETAHUI' : '—');
 return {
  id: `${pool.id}:P${String(s.slot_index).padStart(2,'0')}`,
  pool_id: pool.id,
  pool_name: pool.name,
  slot_index: s.slot_index,
  st: st,
  num: numDisplay,
  op: s.operator_name || (st === 'active' ? 'SIM' : '—'),
  dev: s.port_name || 'COM-',
  iccid: s.iccid || s.iccid_masked || '—',
  sms_count: s.sms_count || 0,
  m: 1
 };
}

function mapSms(m, pool){
 const otpCode = extractOTP(m.body || '');
 const minAgo = m.received_at ? Math.max(0, Math.floor((Date.now() - new Date(m.received_at).getTime()) / 60000)) : 0;
 return {
  id: m.id,
  n: m.sender || 'Tanpa Pengirim',
  l: S.labels[m.sender] || '',
  type: otpCode ? 'OTP' : (m.status === 'PESAN BAWAAN' ? 'Bawaan' : 'Notifikasi'),
  otp: otpCode,
  slot: `${pool.id}:P${String(m.slot_index).padStart(2,'0')}`,
  pool_id: pool.id,
  pool_name: pool.name,
  slot_index: m.slot_index,
  port: m.port_name || 'COM-',
  m: minAgo,
  st: m.status === 'PESAN BAWAAN' ? 'Bawaan' : 'Diterima',
  body: m.body || '',
  received_at: m.received_at
 };
}

function syncSidebarHardware(){
 const pools = visiblePools();
 const title = pools.length === 1 ? pools[0].name : (pools.length ? `${pools.length} modem pool` : 'Belum ada modem pool');
 poolInfo.pool = title;
 const t = $('#side-hw-title'), c = $('#side-hw-chip');
 if(t) t.textContent = title;
 if(c) c.textContent = `${poolInfo.model} · ${poolInfo.chipset}`;
}

async function fetchBackendData(){
 try{
  await loadPools();
  const pools = visiblePools();
  const getJson = url => fetch(url).then(r => r.ok ? r.json() : null).catch(() => null);
  const perPool = await Promise.all(pools.map(async pool => {
   const [st, sl, sm] = await Promise.all([
    getJson('/api/status' + poolQuery(pool.id)),
    getJson('/api/slots' + poolQuery(pool.id)),
    getJson('/api/sms?limit=100' + poolQuery(pool.id, '&'))
   ]);
   return {pool, st, sl, sm};
  }));
  const resOps = await getJson('/api/operators');

  const first = perPool.find(x => x.st);
  if(first){
   poolInfo.model = first.st.module_model || poolInfo.model;
   poolInfo.chipset = first.st.chipset || poolInfo.chipset;
  }
  syncSidebarHardware();

  const failed = perPool.some(x => !Array.isArray(x.sl));
  if(!failed || !slots.length){
   slots = perPool.flatMap(x => Array.isArray(x.sl) ? x.sl.map(s => mapSlot(s, x.pool)) : []);
  }
  if(perPool.every(x => Array.isArray(x.sm))){
   sms = perPool.flatMap(x => x.sm.map(m => mapSms(m, x.pool)))
    .sort((a, b) => new Date(b.received_at || 0) - new Date(a.received_at || 0));
  }
  if(resOps && Array.isArray(resOps)){
   operatorProfiles = resOps;
  }

  refreshBell();
  if(currentDrawerSlot){
   loadDrawerSMS(currentDrawerSlot.slot_index, currentDrawerSlot.id, false);
  }
  if(!dr.innerHTML && !dlg.open && !isSelectMode){
   const cur = location.hash.replace('#/','') || 'dashboard';
   if(cur === 'dashboard') pDash();
  }
 }catch(err){
  console.warn('Backend polling sync:', err);
 }
}

setInterval(() => {
  fetchBackendData();
}, 4000);

/* ---------- Util UI ---------- */
function toast(msg,err){const t=document.createElement('div');t.className='ts'+(err?' err':'');t.textContent=msg;$('#toasts').append(t);setTimeout(()=>t.remove(),3500)}
function modal({title,text='',ok='Konfirmasi',danger,field}){return new Promise(res=>{
 dlg.innerHTML=`<form method="dialog"><h3>${title}</h3><p class="mu">${text}</p>${field?`<label class="f">${field.label}<input id="mf" maxlength="30" value="${esc(field.value||'')}"><small id="me"></small></label>`:''}<div class="row end"><button value="x" class="btn" formnovalidate>Batal</button><button value="ok" class="btn ${danger?'danger':'pri'}">${ok}</button></div></form>`;
 $('form',dlg).onsubmit=e=>{if(field&&e.submitter&&e.submitter.value==='ok'&&!$('#mf').value.trim()){e.preventDefault();$('#me').textContent='Kolom tidak boleh kosong.';$('#mf').setAttribute('aria-invalid','true')}};
 dlg.onclose=()=>res(dlg.returnValue==='ok'?(field?$('#mf').value.trim():true):false);
 dlg.returnValue='';dlg.showModal()})}
const goto=h=>{location.hash===h?route():location.hash=h};
function side(o){$('#side')?.classList.toggle('open',o);let s=$('#sc');if(o&&!s){s=document.createElement('div');s.id='sc';s.className='scr';s.style.zIndex=45;s.onclick=()=>side(false);document.body.append(s)}if(!o&&s)s.remove()}
$$('[data-pop]').forEach(b=>{
  b.onclick=e=>{
    e.stopPropagation();
    const m=$('#'+b.dataset.pop);
    if(!m)return;
    const was=m.hidden;
    $$('.pm').forEach(x=>x.hidden=true);
    $$('[data-pop]').forEach(x=>x.setAttribute('aria-expanded','false'));
    m.hidden=!was;
    b.setAttribute('aria-expanded',String(!was));
  };
});
document.addEventListener('click',()=>$$('.pm').forEach(x=>x.hidden=true));
const loEl=$('#lo');
if(loEl) loEl.onclick=()=>toast(`Status sistem: Terhubung ke ${poolInfo.pool}.`);

// Filter pencarian langsung di Dashboard
const gsInp=$('#gs');
if(gsInp){
  gsInp.oninput=e=>{
   const q = e.target.value.toLowerCase().trim();
   if(currentDashMode === 'stream'){
    const fSearch = $('#fnum-search');
    if(fSearch){ fSearch.value = q; updateUnifiedTable(); }
    $$('.otp-card').forEach(el=>{
     const txt = el.textContent.toLowerCase();
     el.style.display = (!q || txt.includes(q)) ? '' : 'none';
    });
   } else {
    $$('.slot-box').forEach(el=>{
     const txt = el.textContent.toLowerCase();
     el.style.opacity = (!q || txt.includes(q)) ? '1' : '0.22';
    });
   }
  };
}

// Topbar Trigger Action
const btnSync=$('#top-sync');
if(btnSync){
  btnSync.onclick=async()=>{
   btnSync.disabled=true;toast('Menyinkronkan port hardware...');
   try{const r=await fetch('/api/sync',{method:'POST'});const j=await r.json().catch(()=>({}));if(!r.ok){toast(j.detail||'Sinkronisasi gagal.');return}await fetchBackendData();toast(j.mode==='agent'?'Perintah rescan dikirim ke agent.':'Sinkronisasi hardware selesai.')}
   catch(e){toast('Gagal sinkronisasi: '+e.message,1)}finally{btnSync.disabled=false}
  };
}

const btnPoll=$('#top-poll');
if(btnPoll){
  btnPoll.onclick=async()=>{
   btnPoll.disabled=true;toast('Membaca SMS masuk...');
   try{const r=await fetch('/api/poll-sms',{method:'POST'});const j=await r.json().catch(()=>({}));if(!r.ok){toast(j.detail||'Pemeriksaan SMS gagal.');return}await fetchBackendData();toast('Pemeriksaan SMS selesai.')}
   catch(e){toast('Gagal baca SMS: '+e.message,1)}finally{btnPoll.disabled=false}
  };
}

// Pool tanpa agent: semua port offline karena satu sebab, cukup satu peringatan per pool, bukan per port.
const agentlessPools=()=>visiblePools().filter(p=>!p.agent_online);
const alerts=()=>{
 const off=new Set(agentlessPools().map(p=>p.id));
 return slots.filter(s=>(s.st==='offline'||s.st==='error')&&!off.has(s.pool_id));
};
const refreshBell=()=>{
 const np=$('#np');
 if(!np)return;
 const a=alerts(), down=agentlessPools();
 const poolItems=down.map(p=>`<a href="#/dashboard">${esc(p.name)} · Agent tidak terhubung<br><span class="mu">Jalankan agent di komputer modem pool. Nomor tampil lagi setelah port terbaca.</span></a>`);
 const slotItems=a.map(s=>`<a href="#/dashboard" onclick="openDrawer(${jq(s.id)})">${esc(s.pool_name)} · P${String(s.slot_index).padStart(2,'0')} · ${ST[s.st][0]}<br><span class="mu">${NOTE[s.st]}</span></a>`);
 const items=poolItems.concat(slotItems);
 np.innerHTML=items.length?items.join(''):'<p class="mu" style="padding:8px">Semua port beroperasi normal.</p>';
 const dot=$('.dot');
 if(dot) dot.hidden=!items.length;
};

/* ---------- Router ---------- */
const R={
  dashboard:['Dashboard',pDash],
  sms:['Pesan SMS',pSms],
  pengaturan:['Pengaturan',pSet],
  pengguna:['Kelola Pengguna',pUsers],
  inventaris:['Dashboard',pDash]
};
function route(){
 const h=location.hash.replace('#/','');
 let k=R[h]?h:'dashboard';
 if(k === 'pengguna' && (!currentUser || (currentUser.role !== 'superadmin' && currentUser.role !== 'admin'))){
  k = 'dashboard';
  location.hash = '#/dashboard';
  showLoginOverlay();
  toast('Silakan login sebagai Administrator untuk mengelola pengguna.', 1);
 }
 const dmt=$('#dash-mode-toggle');
 if(dmt)dmt.style.display=(k==='dashboard')?'inline-flex':'none';
 $('#btn-mode-grid')?.classList.toggle('on',currentDashMode==='grid');
 $('#btn-mode-stream')?.classList.toggle('on',currentDashMode==='stream');
 $('#title').textContent=R[k][0];
 document.title=R[k][0]+' · SCPool · SIM Inventory & SMS Hub';
 $$('#nav a').forEach(a=>{const on=a.dataset.k===k;a.classList.toggle('on',on);on?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current')});
 if(k !== 'dashboard' && isSelectMode) exitSelectMode();
 closeDrawer();side(false);refreshBell();R[k][1]();v.focus({preventScroll:true});scrollTo(0,0);
}
addEventListener('hashchange',route);

/* ---------- Dashboard & Mode Checklist Dial Massal ---------- */
function toggleSelectMode(){
 if(isSelectMode){
  exitSelectMode();
 } else {
  enterSelectMode();
 }
}

function enterSelectMode(){
 isSelectMode = true;
 const container = $('#pools-container');
 if(container) container.classList.add('select-mode-active');
 const btnToggle = $('#btn-bulk-toggle');
 if(btnToggle){
  btnToggle.className = 'btn';
  btnToggle.innerHTML = '✖ Tutup Mode Pilih';
 }
 updateSelectedVisuals();
 toast('Mode checklist aktif: Pilih slot yang ingin didial.');
}

function exitSelectMode(){
 isSelectMode = false;
 selectedSlots.clear();
 const container = $('#pools-container');
 if(container) container.classList.remove('select-mode-active');
 const btnToggle = $('#btn-bulk-toggle');
 if(btnToggle){
  btnToggle.className = 'btn pri';
  btnToggle.innerHTML = '📞 Dial Massal USSD';
 }
 updateSelectedVisuals();
}

function toggleSelectSlot(e, id){
 e.stopPropagation();
 const s = slots.find(x => x.id === id);
 if(s && (s.st === 'empty' || s.st === 'offline')) return;
 if(e.target.checked){
  selectedSlots.add(id);
 } else {
  selectedSlots.delete(id);
 }
 updateSelectedVisuals();
}

function onSlotClick(e, id){
 if(e.target.closest('.slot-chk')) return;
 if(isSelectMode){
  const s = slots.find(x => x.id === id);
  if(s && (s.st === 'empty' || s.st === 'offline')){
   toast(s.st === 'empty' ? 'Slot kosong tidak dapat dipilih.' : 'Port offline tidak dapat dipilih.', 0);
   return;
  }
  if(selectedSlots.has(id)){
   selectedSlots.delete(id);
  } else {
   selectedSlots.add(id);
  }
  updateSelectedVisuals();
  return;
 }
 openDrawer(id);
}

function toggleSelectAll(){
 const active = slots.filter(s => s.st === 'active');
 if(!active.length){
  toast('Tidak ada SIM aktif di pool.', 1);
  return;
 }
 const allSelected = active.every(s => selectedSlots.has(s.id));
 if(allSelected){
  selectedSlots.clear();
 } else {
  active.forEach(s => selectedSlots.add(s.id));
 }
 updateSelectedVisuals();
}

function clearSelectedSlots(){
 selectedSlots.clear();
 updateSelectedVisuals();
}

function updateSelectedVisuals(){
 $$('.slot-box').forEach(el => {
  const id = el.dataset.id;
  const isSel = selectedSlots.has(id);
  el.classList.toggle('selected', isSel);
  const chk = el.querySelector('.slot-chk');
  if(chk && chk.checked !== isSel) chk.checked = isSel;
 });
 updateBulkBar();
}

function updateBulkBar(){
 const bar = $('#bulk-bar');
 if(!bar) return;
 if(!isSelectMode){
  bar.style.display = 'none';
  return;
 }
 bar.style.display = 'flex';
 const active = slots.filter(s => s.st === 'active');
 const allSelected = active.length > 0 && active.every(s => selectedSlots.has(s.id));
 const selCount = selectedSlots.size;
 const selectAllText = allSelected ? '◻ Batal Pilih' : `✓ Pilih Semua (${active.length})`;
 
 bar.innerHTML = `
  <div class="bulk-bar-info">
   <span class="bulk-count-badge">${selCount}</span>
   <span>slot dipilih</span>
  </div>
  <div class="row" style="gap:8px;align-items:center;">
   <button type="button" class="btn" onclick="toggleSelectAll()">${selectAllText}</button>
   <button type="button" class="btn pri" ${selCount===0?'disabled':''} onclick="openBulkDialModal(true)">📞 Dial USSD (${selCount})</button>
   <button type="button" class="btn danger" onclick="exitSelectMode()">✖ Selesai</button>
  </div>
 `;
}

function renderSlotBox(s){
 const stLabel = (ST[s.st]||ST.empty)[0];
 const known = s.num && s.num !== '—' && s.num !== 'BELUM DIKETAHUI';
 const numDisplay = known ? s.num : (s.st === 'active' ? 'Nomor belum terbaca' : '—');
 const provDisplay = (s.st === 'active' && s.op && s.op !== '—') ? s.op : (s.st === 'empty' ? 'Kosong' : 'Offline');
 const isSelected = selectedSlots.has(s.id);
 const idq = jq(s.id);
 return `
  <div class="slot-box ${s.st}${isSelected ? ' selected' : ''}" data-id="${esc(s.id)}" onclick="onSlotClick(event, ${idq})" tabindex="0" title="${esc(s.pool_name)} · Port ${s.slot_index} · ${stLabel}${isSelectMode ? ' (klik untuk pilih)' : ''}">
   <span class="slot-chk-wrap"><input type="checkbox" class="custom-chk slot-chk" ${(s.st==='empty'||s.st==='offline')?'disabled':''} ${isSelected ? 'checked' : ''} onclick="toggleSelectSlot(event, ${idq})" title="Pilih slot"></span>
   <span class="sb-dot"></span>
   <span class="sb-id">${String(s.slot_index).padStart(2,'0')}</span>
   <span class="sb-num mono">${esc(numDisplay)}</span>
   <span class="sb-prov">${esc(provDisplay)}</span>
   ${s.sms_count ? `<span class="sb-sms" title="${s.sms_count} SMS">${s.sms_count}</span>` : '<span class="sb-sms nil"></span>'}
  </div>
 `;
}

function chassisColumns(count){
 const cols = Math.max(1, Math.ceil(count / 16));
 return Array.from({length: cols}, (_, i) => [i * 16, Math.min(count, (i + 1) * 16)]);
}

function renderChassis(pool, list){
 const cols = chassisColumns(list.length);
 const online = pool.agent_online;
 const bad = list.filter(x => x.st === 'offline' || x.st === 'error').length;
 const act = list.filter(x => x.st === 'active').length;
 return `
  <section class="chassis" style="--cols:${cols.length}" data-pool="${esc(pool.id)}">
   <header class="chassis-head">
    <div class="chassis-title">
     <span class="agent-led ${online ? 'on' : 'off'}" title="${online ? 'Agent terhubung' : 'Agent tidak terhubung'}"></span>
     <b class="chassis-name" title="${esc(pool.name)}">${esc(pool.name)}</b>
    </div>
    <div class="chassis-meta" title="${list.length} port, ${act} aktif, ${bad} bermasalah">${act}/${list.length} aktif${bad ? ` · <span class="warn-t">${bad} ⚠</span>` : ''}</div>
   </header>
   <div class="chassis-body">
    ${cols.map(([from, to]) => `
     <div class="chassis-col">
      <div class="chassis-col-title">${list[from].slot_index}–${list[to - 1].slot_index}</div>
      ${list.slice(from, to).map(renderSlotBox).join('')}
     </div>`).join('')}
   </div>
  </section>`;
}

function renderPools(){
 const pools = visiblePools();
 if(!pools.length){
  return `<section class="pool-card"><div class="pool-header"><h3>Modem pool</h3></div>
   <div class="note" style="margin:12px 0 0;">Belum ada modem pool. Jalankan agent di komputer yang terhubung ke modem pool: unit dan jumlah port terdeteksi otomatis, lalu Anda bisa memberi nama di Pengaturan.</div></section>`;
 }
 const boxes = pools.map(pool => {
  const list = slots.filter(x => x.pool_id === pool.id);
  if(!list.length){
   return `<section class="chassis" style="--cols:1" data-pool="${esc(pool.id)}"><header class="chassis-head"><div class="chassis-title"><span class="agent-led ${pool.agent_online ? 'on' : 'off'}"></span><b class="chassis-name">${esc(pool.name)}</b></div></header>
    <div class="chassis-empty">Belum ada port. Port muncul otomatis begitu agent mendeteksinya.</div></section>`;
  }
  return renderChassis(pool, list);
 });
 const totalCols = pools.reduce((n, pool) => n + chassisColumns(slots.filter(x => x.pool_id === pool.id).length || 1).length, 0);
 return `<div class="pools-wrapper chassis-rack${isSelectMode ? ' select-mode-active' : ''}" id="pools-container" style="--total-cols:${totalCols}">${boxes.join('')}</div>`;
}

function pDash(){
 if(!currentUser || !currentUser.logged_in){
  renderLoggedOutState();
  return;
 }
 if(currentDashMode === 'stream'){
  pStreamDash();
  return;
 }
 const c = cnt(), prob = c.offline + c.error;
 const isClient = currentUser && currentUser.role === 'operator';

 v.innerHTML = `
  ${isClient ? `
  <div class="card" style="margin-bottom:18px;border-left:4px solid var(--ac);background:var(--pn);padding:18px 20px;border-radius:10px;border:1px solid var(--ln);box-shadow:0 4px 12px rgba(0,0,0,0.15);">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap;">
      <div style="flex:1;min-width:260px;">
        <div style="font-size:15px;font-weight:700;color:var(--tx);display:flex;align-items:center;gap:8px;">
          🚀 Hubungkan Modem Pool Anda
        </div>
        <p class="mu" style="font-size:12.5px;margin-top:4px;line-height:1.5;">
          File agen sudah dilengkapi token akun Anda. Unduh, jalankan di komputer yang terhubung ke modem pool: jumlah unit dan port terdeteksi otomatis, dan nama tiap modem pool bisa Anda atur di Pengaturan.
        </p>
        <div style="margin-top:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="font-size:12px;font-weight:600;color:var(--mu);">Token Agent:</span>
          <code class="mono" style="background:var(--p2);padding:3px 8px;border-radius:6px;border:1px solid var(--ln);font-size:11.5px;color:var(--ac);font-weight:700;">${esc(currentUser.agent_token || '—')}</code>
          <button type="button" class="btn" style="padding:3px 8px;font-size:11px;" onclick="copyToClipboard(${jq(currentUser.agent_token || '')}, 'Token agent disalin.')">📋 Salin</button>
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <a href="/api/download/agent?file_type=bat" download="scpool_agent.bat" class="btn pri" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;padding:8px 12px;">
          ⬇ Unduh Agen Siap Pakai (scpool_agent.bat)
        </a>
        <a href="/api/download/agent?file_type=py" download="scpool_agent.py" class="btn" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:8px 12px;">
          ⬇ Versi Python (.py)
        </a>
      </div>
    </div>
  </div>
  ` : ''}

  <div class="note" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
    <div><b>Modem Pool:</b> ${esc(poolInfo.pool)}${visiblePools().length === 1 ? '' : ' <span class="mu">· ditampilkan bersama</span>'}</div>
    <div class="row" style="gap:8px;">
      <button type="button" class="btn ${isSelectMode ? '' : 'pri'}" id="btn-bulk-toggle" onclick="toggleSelectMode()">
        ${isSelectMode ? 'Tutup Pilihan' : 'Dial USSD Massal'}
      </button>
      <button type="button" class="btn" onclick="exportCsv()">Ekspor CSV</button>
    </div>
  </div>
  
  <div class="stat-cards-grid">
    <div class="stat-live-card blue">
      <span class="stat-label">Total Slot</span>
      <b class="stat-num">${slots.length}</b>
    </div>
    <div class="stat-live-card green">
      <span class="stat-label">SIM Aktif</span>
      <b class="stat-num">${c.active}</b>
    </div>
    <div class="stat-live-card purple">
      <span class="stat-label">Slot Kosong</span>
      <b class="stat-num">${c.empty}</b>
    </div>
    <div class="stat-live-card red">
      <span class="stat-label">Perangkat Bermasalah</span>
      <b class="stat-num">${prob}</b>
    </div>
  </div>

  ${renderPools()}
 `;
 updateBulkBar();
}

function openBulkDialModal(onlySelected = false){
 const activeSlots = slots.filter(s => s.st === 'active');
 if(!activeSlots.length){
  toast('Tidak ada slot dengan status aktif untuk didial.', 1);
  return;
 }
 
 const selActive = activeSlots.filter(s => selectedSlots.has(s.id));
 const defaultTarget = (onlySelected && selActive.length) ? 'selected' : 'all';

 dlg.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
   <h3 style="display:flex;align-items:center;gap:8px;">
    <svg class="i" viewBox="0 0 24 24"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z"/></svg>
    Dial USSD Massal
   </h3>
   <button class="btn ic" onclick="dlg.close()">×</button>
  </div>

  <div style="display:grid;gap:12px;">
   <div>
    <label style="font-weight:600;font-size:12px;color:var(--mu);display:block;margin-bottom:6px;">Target Port Dial:</label>
    <div style="display:grid;gap:8px;background:var(--p2);padding:10px 14px;border-radius:8px;border:1px solid var(--ln);">
     <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
      <input type="radio" name="bulk_target" value="all" ${defaultTarget==='all'?'checked':''}>
      <span>Semua SIM Aktif di Pool <b>(${activeSlots.length} slot)</b></span>
     </label>
     <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
      <input type="radio" name="bulk_target" value="selected" ${defaultTarget==='selected'?'checked':''}>
      <span>Hanya Slot Terpilih <b>(${selActive.length} slot)</b></span>
     </label>
    </div>
   </div>

   <div>
    <label style="font-weight:600;font-size:12px;color:var(--mu);display:block;margin-bottom:6px;">Kode USSD:</label>
    <input id="bulk-in-ussd" value="*808*7*8#" style="width:100%;font-family:monospace;font-size:14px;" placeholder="*808#">
    <div class="chips" style="margin-top:6px;">
     ${['*808*7*8#','*808#','*123#','*111#','*999#'].map(c=>`<button type="button" onclick="$('#bulk-in-ussd').value='${c}'">${c}</button>`).join('')}
    </div>
   </div>

   <div class="note" style="margin:0;font-size:12px;">
    Proses dial akan dijalankan secara berurutan pada setiap modem serial untuk mengekstrak nomor telepon dan status SIM.
   </div>

   <div class="row end" style="margin-top:4px;">
    <button type="button" class="btn" onclick="dlg.close()">Batal</button>
    <button type="button" class="btn pri" id="btn-start-bulk">🚀 Mulai Dial Massal</button>
   </div>
  </div>
 `;

 dlg.showModal();

 $('#btn-start-bulk').onclick = () => {
  const targetMode = $('input[name="bulk_target"]:checked', dlg)?.value || 'all';
  const ussdCode = $('#bulk-in-ussd').value.trim() || '*808*7*8#';
  
  let targetSlots = [];
  if(targetMode === 'selected'){
   targetSlots = activeSlots.filter(s => selectedSlots.has(s.id));
   if(!targetSlots.length){
    toast('Pilih minimal 1 slot aktif untuk dial.', 1);
    return;
   }
  } else {
   targetSlots = activeSlots;
  }

  runBulkDialExecution(targetSlots, ussdCode);
 };
}

async function runBulkDialExecution(targetSlots, ussdCode){
 isBulkCancelled = false;
 dlg.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
   <h3 style="display:flex;align-items:center;gap:8px;">
    <svg class="i" viewBox="0 0 24 24"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z"/></svg>
    Proses Dial Massal Berjalan
   </h3>
  </div>

  <p class="mu" style="font-size:13px;">Menghubungi operator pada ${targetSlots.length} slot dengan kode <b class="mono" style="color:var(--tx);">${esc(ussdCode)}</b>...</p>

  <div style="background:var(--p2);border:1px solid var(--ln);border-radius:6px;height:12px;overflow:hidden;margin:12px 0;">
   <div id="bulk-fill" style="background:var(--ac);height:100%;width:0%;transition:width 0.25s ease;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:12px;" class="mu mono">
   <span id="bulk-status-txt">Menyiapkan...</span>
   <span id="bulk-percent-txt">0%</span>
  </div>

  <div id="bulk-logs" style="max-height:220px;overflow-y:auto;background:var(--bg);border:1px solid var(--ln);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:5px;font-family:monospace;font-size:11.5px;margin:14px 0;">
  </div>

  <div class="row end" id="bulk-action-footer">
   <button type="button" class="btn danger" id="btn-cancel-bulk">⏹️ Hentikan Proses</button>
  </div>
 `;

 const fill = $('#bulk-fill');
 const statusTxt = $('#bulk-status-txt');
 const percentTxt = $('#bulk-percent-txt');
 const logBox = $('#bulk-logs');
 const cancelBtn = $('#btn-cancel-bulk');

 cancelBtn.onclick = () => {
  isBulkCancelled = true;
  cancelBtn.disabled = true;
  cancelBtn.textContent = 'Menghentikan...';
 };

 let successCount = 0;
 for(let i = 0; i < targetSlots.length; i++){
  if(isBulkCancelled){
   const div = document.createElement('div');
   div.style.color = 'var(--wn)';
   div.textContent = '⚠️ Proses dial dihentikan oleh pengguna.';
   logBox.appendChild(div);
   break;
  }

  const s = targetSlots[i];
  const pct = Math.round(((i) / targetSlots.length) * 100);
  fill.style.width = pct + '%';
  percentTxt.textContent = pct + '%';
  statusTxt.textContent = `Mendial ${s.pool_name} · Port ${s.slot_index}... [${i+1}/${targetSlots.length}]`;

  const activeLine = document.createElement('div');
  activeLine.style.color = 'var(--mu)';
  activeLine.textContent = `⏳ Port ${String(s.slot_index).padStart(2,'0')} (${s.dev}): Menghubungi...`;
  logBox.appendChild(activeLine);
  logBox.scrollTop = logBox.scrollHeight;

  try{
   const res = await fetch(`/api/slots/${s.slot_index}/ussd?code=${encodeURIComponent(ussdCode)}${poolQuery(s.pool_id, '&')}`, { method: 'POST' });
   const d = await res.json();
   if(d.success){
    successCount++;
    if(d.phone_number){
     s.num = d.phone_number;
    }
    activeLine.style.color = 'var(--ac)';
    activeLine.textContent = `✔ Port ${String(s.slot_index).padStart(2,'0')} (${s.dev}): Sukses ${d.phone_number ? '-> ' + d.phone_number : '(OK)'}`;
   } else {
    activeLine.style.color = 'var(--er)';
    activeLine.textContent = `✖ Port ${String(s.slot_index).padStart(2,'0')} (${s.dev}): Gagal (${d.message || 'Tidak ada respon'})`;
   }
  }catch(err){
   activeLine.style.color = 'var(--er)';
   activeLine.textContent = `✖ Port ${String(s.slot_index).padStart(2,'0')}: Error koneksi (${err.message})`;
  }

  logBox.scrollTop = logBox.scrollHeight;
 }

 fill.style.width = '100%';
 percentTxt.textContent = '100%';
 statusTxt.textContent = `Selesai: ${successCount} dari ${targetSlots.length} berhasil.`;

 $('#bulk-action-footer').innerHTML = `
  <button type="button" class="btn pri" id="btn-done-bulk">Selesai &amp; Perbarui Tampilan</button>
 `;

 $('#btn-done-bulk').onclick = () => {
  dlg.close();
  exitSelectMode();
  pDash();
  fetchBackendData();
  toast(`Dial massal selesai: ${successCount} port berhasil didial.`);
 };
}

function copyToClipboard(text, successMsg = 'Disalin ke clipboard.'){
 if(!text) return;
 if(navigator.clipboard && navigator.clipboard.writeText){
  navigator.clipboard.writeText(text).then(
   () => toast(successMsg),
   () => toast('Gagal menyalin.', 1)
  );
 } else {
  const ta = document.createElement('textarea');
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try {
   document.execCommand('copy');
   toast(successMsg);
  } catch(e) {
   toast('Gagal menyalin.', 1);
  }
  document.body.removeChild(ta);
 }
}

let lastF=null;
function openDrawer(id){
 const x=slots.find(s=>s.id===id);if(!x)return;lastF=document.activeElement;
 currentDrawerSlot = x;

 dr.innerHTML=`<div class="scr" data-x></div><aside class="dw" role="dialog" aria-modal="true" aria-label="Detail port ${x.slot_index} ${esc(x.pool_name)}">
 <div class="dh"><h3>${esc(x.pool_name)} · Port ${x.slot_index}</h3><button class="btn ic" data-x aria-label="Tutup detail">×</button></div>
 <dl>
   <dt>Modem Pool</dt><dd><b>${esc(x.pool_name)}</b></dd>
   <dt>Slot Fisik</dt><dd><b>Port ${x.slot_index}</b></dd>
   <dt>Nomor SIM</dt><dd class="mono"><b style="color:var(--ac);font-size:15px;">${esc(x.num)}</b></dd>
   <dt>Provider</dt><dd><b>${esc(x.op)}</b></dd>
   <dt>Port COM</dt><dd class="mono">${esc(x.dev)}</dd>
   <dt>ICCID</dt><dd class="mono" style="word-break:break-all;font-size:12.5px;">${esc(x.iccid)}</dd>
   <dt>Status</dt><dd>${bd(x.st)}</dd>
   <dt>Modul</dt><dd class="mono">${esc(poolInfo.model)}</dd>
 </dl> 

 <div class="row" style="margin-top:2px;">
   <button type="button" class="btn" id="cp"${x.st==='empty'||x.num==='—'||x.num==='BELUM DIKETAHUI'?' disabled':''}>📋 Salin Nomor</button>
   <button type="button" class="btn" id="btn-refresh-drawer-sms">🔄 Segarkan SMS</button>
 </div>

 <div style="border-top:1px solid var(--ln);margin-top:8px;padding-top:12px;display:flex;flex-direction:column;gap:10px;">
   <div style="display:flex;justify-content:space-between;align-items:center;">
     <h3 style="font-size:14px;margin:0;display:flex;align-items:center;gap:6px;">
       <svg class="i" viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>
       Kotak Masuk SMS
     </h3>
     <span id="drawer-sms-count" class="bulk-count-badge" style="font-size:11px;">0</span>
   </div>
   <div id="drawer-sms-list" style="display:flex;flex-direction:column;gap:10px;max-height:55vh;overflow-y:auto;padding-right:2px;">
     <div class="note" style="margin:0;text-align:center;padding:16px;color:var(--mu);font-size:12.5px;">Memuat pesan masuk...</div>
   </div>
 </div>
 </aside>`;

 $$('[data-x]',dr).forEach(e=>e.onclick=closeDrawer);
 $('.dw .btn',dr).focus();
 $('#cp').onclick=()=>copyToClipboard(x.num, 'Nomor telepon disalin ke clipboard.');
 $('#btn-refresh-drawer-sms').onclick=()=>loadDrawerSMS(x.slot_index, x.id, true);

 loadDrawerSMS(x.slot_index, x.id, false);
}

async function loadDrawerSMS(slotIndex, slotId, showFeedback = false){
 const listEl = $('#drawer-sms-list');
 const countEl = $('#drawer-sms-count');
 if(!listEl) return;
 if(showFeedback) toast(`Menyegarkan SMS Port ${slotIndex}...`);

 try {
  const owner = slots.find(x => x.id === slotId);
  const res = await fetch(`/api/slots/${slotIndex}/sms${owner ? poolQuery(owner.pool_id) : ''}`);
  if(!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  
  if(!listEl || !countEl) return;
  countEl.textContent = data.length;

  if(!data.length){
   listEl.innerHTML = `
    <div class="note" style="margin:0;text-align:center;padding:18px 12px;color:var(--mu);font-size:12.5px;background:var(--p2);border-radius:8px;">
      Belum ada pesan SMS masuk pada slot ini.
    </div>
   `;
   return;
  }

  listEl.innerHTML = data.map((m, idx) => {
   const otpCode = extractOTP(m.body || '');
   const timeStr = m.received_at ? new Date(m.received_at).toLocaleString('id-ID', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}) : 'Baru saja';
   const senderName = m.sender || 'Tanpa Pengirim';
   const safeBody = esc(m.body || '');
   
   return `
    <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
        <div>
          <b style="color:var(--tx);font-size:13px;">${esc(senderName)}</b>
          <span class="mu" style="display:block;font-size:11px;">${timeStr}</span>
        </div>
        <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
          ${otpCode ? `<span class="sb-pill" style="background:#059669;color:#fff;font-size:10.5px;padding:2px 7px;">OTP ${esc(otpCode)}</span>` : ''}
          <span class="sb-pill" style="font-size:10.5px;">${esc(m.status || 'Diterima')}</span>
        </div>
      </div>
      <div class="mono" style="background:var(--bg);border:1px solid var(--ln);border-radius:6px;padding:9px 11px;font-size:12.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word;color:var(--tx);user-select:text;">${safeBody}</div>
      <div class="row end" style="gap:6px;margin-top:2px;">
        ${otpCode ? `<button type="button" class="btn pri copy-otp-btn" data-idx="${idx}" style="padding:4px 8px;font-size:11px;">Salin OTP</button>` : ''}
        <button type="button" class="btn copy-sms-btn" data-idx="${idx}" style="padding:4px 8px;font-size:11px;">Salin SMS</button>
      </div>
    </div>
   `;
  }).join('');

  listEl.querySelectorAll('.copy-sms-btn').forEach(btn => {
   const idx = +btn.dataset.idx;
   btn.onclick = () => copyToClipboard(data[idx].body, 'Isi SMS disalin ke clipboard.');
  });
  listEl.querySelectorAll('.copy-otp-btn').forEach(btn => {
   const idx = +btn.dataset.idx;
   const otpCode = extractOTP(data[idx].body || '');
   if(otpCode) btn.onclick = () => copyToClipboard(otpCode, 'Kode OTP disalin: ' + otpCode);
  });

 } catch(err) {
  if(!listEl) return;
  listEl.innerHTML = `
   <div class="note" style="margin:0;color:var(--er);padding:12px;font-size:12px;">
     Gagal memuat pesan: ${esc(err.message)}
   </div>
  `;
 }
}

function closeDrawer(){
 if(!dr.innerHTML) return;
 dr.innerHTML = '';
 currentDrawerSlot = null;
 lastF && lastF.isConnected && lastF.focus();
 lastF = null;
}
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'){closeDrawer();side(false);hideLoginOverlay();}
 if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){
  e.preventDefault();
  const cur=location.hash.replace('#/','')||'dashboard';
  if(cur==='sms')$('#sq')?.focus();
  else $('#gs')?.focus();
 }
});

function exportCsv(){
 const q=v=>{let t=String(v);if(/^[=+\-@\t\r]/.test(t)&&!/^\+\d{6,18}$/.test(t))t="'"+t;return '"'+t.replace(/"/g,'""')+'"'};
 const headers=['Modem Pool','Port','Nomor SIM','Provider','Port COM','Status','ICCID'];
 const rows=slots.map(s=>[s.pool_name,s.slot_index,s.num,s.op,s.dev,(ST[s.st]||ST.empty)[0],s.iccid].map(q));
 const content="data:text/csv;charset=utf-8,"+headers.join(',')+"\n"+rows.map(r=>r.join(',')).join("\n");
 const a=document.createElement('a');a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(headers.join(',')+'\n'+rows.map(r=>r.join(',')).join('\n'));a.download=`sim_inventory_${new Date().toISOString().slice(0,10)}.csv`;
 document.body.appendChild(a);a.click();document.body.removeChild(a);
 toast('File CSV inventaris berhasil diunduh.');
}

/* ---------- SMS Inbox (Semua Kode & Pesan Ditampilkan Langsung) ---------- */
const lab=n=>S.labels[n]||'';
function pSms(){
 if(!currentUser || !currentUser.logged_in){
  renderLoggedOutState();
  return;
 }
 const s=S.sms,o=(a,cur)=>a.map(x=>`<option value="${esc(x[0])}"${x[0]===cur?' selected':''}>${esc(x[1])}</option>`).join('');
 v.innerHTML=`<div class="ph"><p>Pusat SMS masuk seluruh slot modem pool. Kode OTP dan isi pesan ditampilkan secara langsung tanpa sensor.</p></div>
 <section class="panel sms" id="sms"><div class="list"><div class="tools"><input id="sq" type="search" placeholder="Cari pengirim, nomor, isi pesan" aria-label="Cari SMS" value="${esc(s.q)}">
 <select id="sslot" aria-label="Slot">${o([['all','Semua slot'],...slots.map(x=>[x.id,(poolsList.length>1?x.pool_name+' · ':'')+'P'+String(x.slot_index).padStart(2,'0')+' ('+x.dev+')'])],s.slot)}</select></div><div id="sl"></div></div><div class="detail" id="sd"></div></section>`;
 const m={sq:'q',sslot:'slot'};
 Object.keys(m).forEach(id=>$('#'+id).oninput=e=>{s[m[id]]=e.target.value;smsList()});
 $('#sms').onclick=async e=>{
  const t=e.target.closest('[data-m],#bk,#rl,#cp-sms,#cp-otp');if(!t)return;
  if(t.dataset.m){s.sel=+t.dataset.m;smsList();smsDet()}
  else if(t.id==='bk'){s.sel=null;smsList();smsDet()}
  else if(t.id==='cp-sms'){const x=sms.find(m=>m.id===s.sel);if(x){copyToClipboard(x.body,'Isi SMS disalin ke clipboard.')}}
  else if(t.id==='cp-otp'){const x=sms.find(m=>m.id===s.sel);const otpCode=extractOTP(x?.body||'');if(otpCode){copyToClipboard(otpCode,'Kode OTP disalin: '+otpCode)}}
  else if(t.id==='rl'){const x=sms.find(m=>m.id===s.sel);if(!x)return;const r=await modal({title:'Ubah label pengirim',text:'Label berlaku untuk pengirim '+esc(x.n)+'.',ok:'Simpan label',field:{label:'Label',value:lab(x.n)}});if(r){S.labels[x.n]=r;smsList();smsDet();toast('Label disimpan.')}}
 };
 smsList();smsDet();
}

function smsList(){
 const s=S.sms,q=s.q.toLowerCase(),el=$('#sl');
 const a=sms.filter(x=>(s.slot==='all'||x.slot===s.slot)&&(!q||(x.n+' '+lab(x.n)+' '+x.body).toLowerCase().includes(q)));
 if(!a.length){el.innerHTML=`<div class="empty"><p>${sms.length?'Tidak ada pesan yang cocok dengan filter.':'Belum ada pesan SMS tersimpan.'}</p></div>`;return}
 const G={};a.sort((p,r)=>p.m-r.m).forEach(x=>(G[x.n]=G[x.n]||[]).push(x));
 // Pesan ditampilkan secara langsung tanpa fungsi mask()
 el.innerHTML=Object.entries(G).map(([n,l])=>`<div class="gh"><span>${esc(lab(n)||n)}</span><span class="mu">${l.length}</span></div>${l.map(x=>`<button class="mr${x.id===s.sel?' on':''}" data-m="${x.id}"><span class="pv">${esc(x.body)}</span><small>${ago(x.m)} · ${x.slot} · ${x.type} · ${x.st}</small></button>`).join('')}`).join('');
}

function smsDet(){
 const s=S.sms,x=sms.find(m=>m.id===s.sel),d=$('#sd');$('#sms').classList.toggle('det',!!x);
 if(!x){d.innerHTML='<div class="empty"><p>Pilih pesan untuk melihat detail rincian.</p></div>';return}
 const otpCode=extractOTP(x.body||'');
 // Isi pesan ditampilkan langsung sepenuhnya tanpa tombol samarkan kode
 d.innerHTML=`<button class="btn mob" id="bk">‹ Kembali</button><h3>${esc(lab(x.n)||'Pengirim: '+x.n)}</h3><p class="mu mono">${esc(x.n)}</p>
 <dl style="margin-top:12px"><dt>Diterima</dt><dd>${x.received_at?new Date(x.received_at).toLocaleString('id-ID'):ago(x.m)}</dd><dt>Slot Fisik</dt><dd><b>${esc(x.slot)} (${esc(x.port)})</b></dd><dt>Status</dt><dd>${x.st}</dd></dl>
 ${otpCode?`<div class="note" style="border-left-color:var(--ac);display:flex;justify-content:space-between;align-items:center;margin:12px 0;"><div><span style="font-size:12px;color:var(--ac);display:block;font-weight:600;">KODE OTP TERDETEKSI:</span><b class="mono" style="font-size:24px;letter-spacing:2px;color:var(--tx);">${otpCode}</b></div><button class="btn pri" id="cp-otp">📋 Salin OTP</button></div>`:''}
 <div class="body">${esc(x.body)}</div>
 <div class="row"><button class="btn" id="cp-sms">Salin teks</button><button class="btn" id="rl">Ubah label</button></div>`;
}

/* ---------- Pengaturan & Dokumentasi API ---------- */
function getSystemTheme(){
 return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
}

function setThemeMode(mode){
 currentThemeMode = mode;
 try{ localStorage.setItem('scpool_theme', mode); }catch(e){}
 const activeTheme = mode === 'auto' ? getSystemTheme() : mode;
 document.documentElement.dataset.theme = activeTheme;
 $$('.btn-theme-opt').forEach(b => {
  b.classList.toggle('pri', b.dataset.mode === mode);
 });
}

document.documentElement.dataset.theme = currentThemeMode === 'auto' ? getSystemTheme() : currentThemeMode;

if(window.matchMedia){
 window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if(currentThemeMode === 'auto'){
   setThemeMode('auto');
  }
 });
}

async function loadCredentialsUI(){
 try{
  const res = await fetch('/api/auth/credentials');
  if(res.ok){
   const d = await res.json();
   userCredentials.username = d.username || '';
   const uEl = $('#cred-username');
   if(uEl) uEl.textContent = userCredentials.username;
   updatePasswordDisplay();
  }
 }catch(e){
  console.warn('Gagal memuat kredensial:', e);
 }
}

function togglePasswordVisibility(){
 isPasswordVisible = !isPasswordVisible;
 updatePasswordDisplay();
}

function updatePasswordDisplay(){
 const pEl = $('#cred-password');
 const bEl = $('#btn-toggle-pwd');
 if(!pEl || !bEl) return;
 pEl.textContent = isPasswordVisible ? '•••••••• (tersimpan sebagai hash PBKDF2)' : '••••••••';
 bEl.textContent = isPasswordVisible ? '🙈 Sembunyikan' : '👁️ Tampilkan';
}

function openChangePasswordModal(){
 dlg.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
    <h3 style="display:flex;align-items:center;gap:8px;">
      <svg class="i" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      Ubah Password Admin
    </h3>
    <button type="button" class="btn ic" onclick="dlg.close()">×</button>
  </div>
  <form id="form-change-pwd" style="display:grid;gap:12px;">
    <label class="f">
      Username:
      <input id="cp-user" value="${esc(userCredentials.username)}" placeholder="Username admin">
    </label>
    <label class="f">
      Password Saat Ini:
      <input type="password" id="cp-cur-pwd" autocomplete="current-password" placeholder="Wajib bila diakses dari luar">
    </label>
    <label class="f">
      Password Baru:
      <input type="password" id="cp-new-pwd" minlength="8" required placeholder="Minimal 8 karakter">
    </label>
    <label class="f">
      Konfirmasi Password Baru:
      <input type="password" id="cp-confirm-pwd" minlength="8" required placeholder="Ulangi password baru">
      <small id="cp-err" class="er"></small>
    </label>
    <div class="row end" style="margin-top:6px;">
      <button type="button" class="btn" onclick="dlg.close()">Batal</button>
      <button type="submit" class="btn pri" id="btn-save-pwd">Simpan Password</button>
    </div>
  </form>
 `;

 $('#form-change-pwd').onsubmit = async (e) => {
  e.preventDefault();
  const newU = $('#cp-user').value.trim() || userCredentials.username;
  const curP = $('#cp-cur-pwd').value;
  const newP = $('#cp-new-pwd').value;
  const confP = $('#cp-confirm-pwd').value;
  const errEl = $('#cp-err');

  if(newP !== confP){
   errEl.textContent = 'Konfirmasi password tidak cocok.';
   return;
  }
  if(newP.length < 8){
   errEl.textContent = 'Password minimal 8 karakter.';
   return;
  }

  const btn = $('#btn-save-pwd');
  btn.disabled = true;
  btn.textContent = 'Menyimpan...';

  try{
   const res = await fetch('/api/auth/credentials', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ username: newU, password: newP, current_password: curP })
   });
   const data = await res.json();
   if(res.ok && data.success){
    userCredentials.username = data.username;
    dlg.close();
    toast('Password berhasil diubah.');
    const uEl = $('#cred-username');
    if(uEl) uEl.textContent = userCredentials.username;
    updatePasswordDisplay();
   } else {
    errEl.textContent = data.detail || 'Gagal mengubah password.';
   }
  }catch(err){
   errEl.textContent = 'Error: ' + err.message;
  }finally{
   btn.disabled = false;
   btn.textContent = 'Simpan Password';
  }
 };

 dlg.showModal();
}

const API_SNIPPETS = {
 curl: (origin) => `# 1. Ambil seluruh port modem pool & nomor SIM aktif:
curl -s "${origin}/api/slots"

# 2. Polling pesan SMS masuk pada Port 5 (misal untuk verifikasi akun):
curl -s "${origin}/api/slots/5/sms"

# 3. Ambil feed SMS gabungan dengan pencarian kata kunci:
curl -s "${origin}/api/sms?limit=20&q=WhatsApp"

# 4. Dial USSD mandiri pada Port 5:
curl -X POST "${origin}/api/slots/5/ussd?code=*808*7*8#"

# 5. Dial USSD massal ke beberapa port:
curl -X POST "${origin}/api/ussd/bulk" \\
     -H "Content-Type: application/json" \\
     -d '{"slot_indices": [1, 2, 3], "code": "*808*7*8#"}'`,

 python: (origin) => `import time
import requests

BASE_URL = "${origin}"

def get_active_sims():
    """Mengambil daftar nomor telepon yang aktif di pool."""
    resp = requests.get(f"{BASE_URL}/api/slots")
    slots = resp.json()
    return [s for s in slots if s["status"] == "READY"]

def wait_for_otp(slot_index, timeout_seconds=60):
    """Polling SMS masuk pada port tertentu sampai SMS/OTP diterima."""
    print(f"Menunggu SMS masuk pada Port {slot_index}...")
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        resp = requests.get(f"{BASE_URL}/api/slots/{slot_index}/sms")
        messages = resp.json()
        if messages:
            latest = messages[0]
            return latest  # Berisi sender, body, received_at
        time.sleep(2)
    return None

# Contoh pemakaian layanan:
active_slots = get_active_sims()
if active_slots:
    sim = active_slots[0]
    print(f"Menggunakan Port {sim['slot_index']}: {sim['phone_number']} ({sim['operator_name']})")
    sms = wait_for_otp(sim['slot_index'])
    if sms:
        print(f"Diterima dari {sms['sender']}: {sms['body']}")`,

 node: (origin) => `// Integrasi Node.js / JavaScript untuk Bot Layanan SIM Pool
const BASE_URL = '${origin}';

async function main() {
  // 1. Ambil daftar kartu SIM aktif
  const resSlots = await fetch(\`\${BASE_URL}/api/slots\`);
  const slots = await resSlots.json();
  const activeSlots = slots.filter(s => s.status === 'READY');
  
  console.log(\`SIM Siap: \${activeSlots.length} nomor\`);
  const target = activeSlots[0];
  console.log(\`Gunakan Port \${target.slot_index}: \${target.phone_number}\`);

  // 2. Polling SMS masuk pada port target
  const resSms = await fetch(\`\${BASE_URL}/api/slots/\${target.slot_index}/sms\`);
  const messages = await resSms.json();
  console.log('Riwayat SMS:', messages);
}

main().catch(console.error);`
};

function switchApiTab(lang){
 currentApiLang = lang;
 $$('.btn-lang').forEach(b => b.classList.toggle('pri', b.dataset.lang === lang));
 const box = $('#api-snippet-box');
 if(box){
  box.textContent = API_SNIPPETS[lang](location.origin);
 }
}

function copyCurrentSnippet(){
 const box = $('#api-snippet-box');
 if(box){
  copyToClipboard(box.textContent, 'Contoh kode disalin ke clipboard.');
 }
}

function pSet(){
 if(!currentUser || !currentUser.logged_in){
  renderLoggedOutState();
  return;
 }
 v.innerHTML = `
 <div class="sec" id="sf">
  <!-- MODEM POOL SAYA -->
  <section class="panel" id="pool-manager"></section>
  <!-- TEMA TAMPILAN -->
  <section class="panel">
   <h3>Tema Tampilan</h3>
   <p class="mu" style="margin-bottom:12px;font-size:13px;">Tema menyesuaikan otomatis dengan preferensi sistem operasi Anda, atau pilih tema secara langsung:</p>
   <div class="row" style="gap:8px;">
     <button type="button" class="btn btn-theme-opt ${currentThemeMode==='auto'?'pri':''}" data-mode="auto" onclick="setThemeMode('auto')">🌓 Otomatis (Sistem)</button>
     <button type="button" class="btn btn-theme-opt ${currentThemeMode==='dark'?'pri':''}" data-mode="dark" onclick="setThemeMode('dark')">🌙 Gelap (Dark)</button>
     <button type="button" class="btn btn-theme-opt ${currentThemeMode==='light'?'pri':''}" data-mode="light" onclick="setThemeMode('light')">☀️ Terang (Light)</button>
   </div>
  </section>

  <!-- AKUN & KREDENSIAL AKSES POOL -->
  <section class="panel">
   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
     <div>
       <h3 style="display:flex;align-items:center;gap:6px;">
         <svg class="i" viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
         Akun &amp; Kredensial
       </h3>
       <p class="mu" style="font-size:12.5px;">Kredensial login untuk akses admin panel dan perlindungan antarmuka.</p>
     </div>
     <button type="button" class="btn pri" onclick="openChangePasswordModal()">🔑 Ubah Password</button>
   </div>

   <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:14px 18px;display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;">
     <div>
       <span class="mu" style="font-size:12px;display:block;margin-bottom:4px;">Username</span>
       <b class="mono" style="font-size:15px;color:var(--tx);" id="cred-username">${esc(userCredentials.username)}</b>
     </div>
     <div>
       <span class="mu" style="font-size:12px;display:block;margin-bottom:4px;">Password</span>
       <div style="display:flex;align-items:center;gap:10px;">
         <span class="mono" style="font-size:15px;color:var(--tx);letter-spacing:1px;" id="cred-password">••••••••</span>
         <button type="button" class="btn" style="padding:3px 8px;font-size:11px;" id="btn-toggle-pwd" onclick="togglePasswordVisibility()">👁️ Tampilkan</button>
       </div>
     </div>
     <div>
       <span class="mu" style="font-size:12px;display:block;margin-bottom:4px;">Hak Akses</span>
       <span class="sb-pill" style="font-size:11px;background:rgba(16,185,129,0.18);color:var(--ac);border-color:var(--ac);">${(currentUser.role === 'superadmin' || currentUser.role === 'admin') ? 'Super Administrator' : 'Operator'}</span>
     </div>
   </div>
  </section>

  <!-- NOTIFIKASI & PERSETUJUAN TELEGRAM (SUPERADMIN) -->
  ${(currentUser && (currentUser.role === 'superadmin' || currentUser.role === 'admin')) ? `
  <section class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
      <div>
        <h3 style="display:flex;align-items:center;gap:6px;">
          <svg class="i" viewBox="0 0 24 24"><path d="M21.198 2.433a2.242 2.242 0 0 0-1.022.215l-17.5 7.5a2.25 2.25 0 0 0 .14 4.148l4.47 1.543 1.7 5.1a1.25 1.25 0 0 0 2.235.253l2.67-3.56 4.67 3.5a2.25 2.25 0 0 0 3.535-1.39l3-15a2.25 2.25 0 0 0-3.908-2.309z"/></svg>
          Notifikasi &amp; Persetujuan Telegram
        </h3>
        <p class="mu" style="font-size:12.5px;">Terima notifikasi instan saat ada klien mendaftar, dan setujui langsung via tombol inline Telegram.</p>
      </div>
      <button type="button" class="btn" onclick="testTelegramAlert()" id="btn-tele-test">🔔 Uji Coba Telegram</button>
    </div>

    <form id="form-telegram-settings" onsubmit="saveTelegramSettings(event)" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:16px;">
      <label class="f">
        <span style="font-size:12px;font-weight:600;color:var(--mu);">Bot Token Telegram:</span>
        <input id="set-tele-token" placeholder="Misal: 123456789:ABCdefGhI..." style="padding:8px 10px;font-size:13px;">
        <small class="mu" style="font-size:11px;">Dibuat melalui @BotFather di Telegram</small>
      </label>
      <label class="f">
        <span style="font-size:12px;font-weight:600;color:var(--mu);">Chat ID Admin:</span>
        <input id="set-tele-chatid" placeholder="Misal: 987654321" style="padding:8px 10px;font-size:13px;">
        <small class="mu" style="font-size:11px;">ID akun atau grup admin (dari @userinfobot)</small>
      </label>
      <div style="grid-column:1/-1;display:flex;justify-content:flex-end;margin-top:6px;">
        <button type="submit" class="btn pri" id="btn-save-tele">💾 Simpan Konfigurasi Telegram</button>
      </div>
    </form>
  </section>
  ` : ''}

  <!-- DOKUMENTASI API INTEGRASI (LAYANAN SIM POOL) -->
  <section class="panel">
   <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
     <div>
       <h3 style="display:flex;align-items:center;gap:6px;">
         <svg class="i" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
         Dokumentasi API Integrasi (Layanan SIM Pool)
       </h3>
       <p class="mu" style="font-size:12.5px;">Gunakan REST API ini untuk memberikan layanan SMS, verifikasi OTP, atau dial USSD kepada klien eksternal, bot Telegram, maupun sistem registrasi otomatis.</p>
     </div>
     <div class="row" style="gap:6px;">
       <a href="/docs" target="_blank" class="btn" style="display:inline-flex;align-items:center;gap:5px;">
         <span>↗ Swagger UI (/docs)</span>
       </a>
     </div>
   </div>

   <div class="note" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
     <div>
       <span style="font-size:12px;color:var(--mu);display:block;">BASE URL SERVER:</span>
       <b class="mono" style="color:var(--ac);font-size:14px;" id="api-base-url">${location.origin}</b>
     </div>
     <button type="button" class="btn" onclick="copyToClipboard(location.origin, 'Base URL disalin')">📋 Salin Base URL</button>
   </div>

   <div style="display:grid;gap:10px;margin-bottom:20px;">
     <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;">
       <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">
         <span class="sb-pill" style="background:#0284c7;color:#fff;font-weight:700;">GET</span>
         <code class="mono" style="font-size:13px;font-weight:600;">/api/slots</code>
         <span class="mu" style="font-size:12px;margin-left:auto;">Cek Port &amp; Nomor SIM Aktif</span>
       </div>
       <p class="mu" style="font-size:12px;margin:0;">Mengembalikan seluruh slot modem (1–32) lengkap dengan status (READY/EMPTY), nomor telepon, provider, dan ICCID.</p>
     </div>

     <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;">
       <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">
         <span class="sb-pill" style="background:#0284c7;color:#fff;font-weight:700;">GET</span>
         <code class="mono" style="font-size:13px;font-weight:600;">/api/slots/{slot_index}/sms</code>
         <span class="mu" style="font-size:12px;margin-left:auto;">Polling SMS / OTP per Port</span>
       </div>
       <p class="mu" style="font-size:12px;margin:0;">Mengambil riwayat SMS khusus pada slot tertentu. Digunakan klien/bot untuk menunggu kode OTP masuk.</p>
     </div>

     <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;">
       <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">
         <span class="sb-pill" style="background:#0284c7;color:#fff;font-weight:700;">GET</span>
         <code class="mono" style="font-size:13px;font-weight:600;">/api/sms?limit=50&amp;q={kata_kunci}</code>
         <span class="mu" style="font-size:12px;margin-left:auto;">Feed Gabungan Seluruh SMS</span>
       </div>
       <p class="mu" style="font-size:12px;margin:0;">Mengambil feed pesan SMS seluruh pool dengan opsi filter pencarian pengirim atau kata kunci.</p>
     </div>

     <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;">
       <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">
         <span class="sb-pill" style="background:#059669;color:#fff;font-weight:700;">POST</span>
         <code class="mono" style="font-size:13px;font-weight:600;">/api/slots/{slot_index}/ussd?code={kode}</code>
         <span class="mu" style="font-size:12px;margin-left:auto;">Dial USSD Port Tertentu</span>
       </div>
       <p class="mu" style="font-size:12px;margin:0;">Mengirim perintah dial USSD (contoh <code>*808*7*8#</code> atau <code>*123#</code>) untuk cek nomor atau kuota.</p>
     </div>

     <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:12px 14px;">
       <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;">
         <span class="sb-pill" style="background:#059669;color:#fff;font-weight:700;">POST</span>
         <code class="mono" style="font-size:13px;font-weight:600;">/api/ussd/bulk</code>
         <span class="mu" style="font-size:12px;margin-left:auto;">Dial USSD Massal</span>
       </div>
       <p class="mu" style="font-size:12px;margin:0;">Eksekusi dial USSD serentak secara serial aman. Body: <code>{"slot_indices": [1,2,3], "code": "*808#"}</code></p>
     </div>
   </div>

   <!-- Interactive Code Examples -->
   <div style="margin-top:14px;">
     <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px;">
       <div class="row" style="gap:6px;">
         <button type="button" class="btn btn-lang pri" data-lang="curl" onclick="switchApiTab('curl')">cURL</button>
         <button type="button" class="btn btn-lang" data-lang="python" onclick="switchApiTab('python')">Python</button>
         <button type="button" class="btn btn-lang" data-lang="node" onclick="switchApiTab('node')">Node.js / JS</button>
       </div>
       <button type="button" class="btn" onclick="copyCurrentSnippet()">📋 Salin Kode Contoh</button>
     </div>

     <pre id="api-snippet-box" style="background:var(--bg);border:1px solid var(--ln);border-radius:8px;padding:14px;overflow-x:auto;font-family:monospace;font-size:12px;line-height:1.55;color:var(--tx);margin:0;"></pre>
   </div>
  </section>

  <!-- HARDWARE & MODEM POOL -->
  <section class="panel">
   <h3>Hardware &amp; Modem Pool</h3>
   <ul class="list0" style="list-style:none;padding:0;margin:0;">
     <li style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--ln)"><span>Chassis Pool</span><b>${esc(poolInfo.pool)}</b></li>
     <li style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--ln)"><span>Baudrate</span><span class="mono">115200 bps · 8-N-1</span></li>
     <li style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--ln)"><span>Tautan Publik</span><a href="${esc(location.origin)}" target="_blank" style="color:var(--ac);">${esc(location.origin)}</a></li>
     <li style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--ln)"><span>Sistem</span><b style="color:var(--tx);">SCPool Engine</b></li>
     <li style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--ln)"><span>Versi</span><b style="color:var(--ac);">1.1.0</b></li>
   </ul>
  </section>
 </div>
 `;
 renderPoolManager(true);

 switchApiTab(currentApiLang);
 loadCredentialsUI();
 loadTelegramSettings();
}

async function loadTelegramSettings(){
 try {
  const res = await fetch('/api/admin/settings');
  if(!res.ok) return;
  const data = await res.json();
  const tokenInput = $('#set-tele-token');
  const chatIdInput = $('#set-tele-chatid');
  if(tokenInput && data.telegram_bot_token) tokenInput.value = data.telegram_bot_token;
  if(chatIdInput && data.telegram_chat_id) chatIdInput.value = data.telegram_chat_id;
 } catch(e){}
}

async function saveTelegramSettings(e){
 e.preventDefault();
 const token = $('#set-tele-token')?.value.trim() || '';
 const chatId = $('#set-tele-chatid')?.value.trim() || '';
 const btn = $('#btn-save-tele');
 if(btn) btn.disabled = true;

 try {
  const res = await fetch('/api/admin/settings', {
   method: 'POST',
   headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({
    telegram_bot_token: token,
    telegram_chat_id: chatId
   })
  });
  const data = await res.json();
  if(res.ok && data.success){
   toast('Pengaturan Telegram berhasil disimpan.');
  } else {
   toast(data.detail || 'Gagal menyimpan pengaturan.', 1);
  }
 } catch(err){
  toast('Error: ' + err.message, 1);
 } finally {
  if(btn) btn.disabled = false;
 }
}

async function testTelegramAlert(){
 const btn = $('#btn-tele-test');
 if(btn) btn.disabled = true;
 toast('Mengirim pesan uji coba ke Telegram...');
 try {
  const res = await fetch('/api/admin/telegram/test', { method: 'POST' });
  const data = await res.json();
  if(res.ok && data.success){
   toast('✅ Pesan uji coba berhasil terkirim ke Telegram Anda!');
  } else {
   toast('❌ ' + (data.detail || 'Gagal mengirim pesan Telegram.'), 1);
  }
 } catch(err){
  toast('Error: ' + err.message, 1);
 } finally {
  if(btn) btn.disabled = false;
 }
}

function apply(){
 const activeTheme = currentThemeMode === 'auto' ? getSystemTheme() : currentThemeMode;
 document.documentElement.dataset.theme = activeTheme;
}

/* ==========================================================================
   MULTI-TENANT & USER MANAGEMENT (SUPERADMIN & CLIENT)
   ========================================================================== */

let isAuthMandatory = false;

function showLoginOverlay(mandatory = false, defaultTab = 'login'){
  isAuthMandatory = mandatory;
  const el = $('#login-overlay');
  const closeBtn = $('#btn-login-close');
  if(closeBtn) closeBtn.style.display = mandatory ? 'none' : 'block';
  switchAuthTab(defaultTab);
  if(el){
    el.style.display = 'grid';
    if(defaultTab === 'login') $('#login-uname')?.focus();
    else $('#reg-uname')?.focus();
  }
}

function hideLoginOverlay(){
  if(isAuthMandatory && (!currentUser || !currentUser.logged_in)) {
    return;
  }
  const el = $('#login-overlay');
  if(el) el.style.display = 'none';
}

function switchAuthTab(tab){
  const formLogin = $('#form-login');
  const formReg = $('#form-register');
  const tabLogin = $('#tab-btn-login');
  const tabReg = $('#tab-btn-register');
  const regMsg = $('#reg-msg');

  if(tab === 'register'){
    if(formLogin){
      formLogin.classList.remove('active');
      formLogin.style.display = 'none';
    }
    if(formReg){
      formReg.classList.add('active');
      formReg.style.display = 'flex';
    }
    if(regMsg) regMsg.style.display = 'none';
    if(tabLogin){
      tabLogin.style.borderBottom = '2px solid transparent';
      tabLogin.style.color = 'var(--mu)';
      tabLogin.style.fontWeight = 'normal';
    }
    if(tabReg){
      tabReg.style.borderBottom = '2px solid var(--ac)';
      tabReg.style.color = 'var(--tx)';
      tabReg.style.fontWeight = '700';
    }
    $('#reg-uname')?.focus();
  } else {
    if(formLogin){
      formLogin.classList.add('active');
      formLogin.style.display = 'flex';
    }
    if(formReg){
      formReg.classList.remove('active');
      formReg.style.display = 'none';
    }
    if(tabLogin){
      tabLogin.style.borderBottom = '2px solid var(--ac)';
      tabLogin.style.color = 'var(--tx)';
      tabLogin.style.fontWeight = '700';
    }
    if(tabReg){
      tabReg.style.borderBottom = '2px solid transparent';
      tabReg.style.color = 'var(--mu)';
      tabReg.style.fontWeight = 'normal';
    }
  }
}
window.switchAuthTab = switchAuthTab;
window.hideLoginOverlay = hideLoginOverlay;
window.showLoginOverlay = showLoginOverlay;

function clearSessionState(){
  slots = [];
  sms = [];
  poolsList = [];
  activePoolId = null;
  selectedSlots.clear();
  isSelectMode = false;
  currentDrawerSlot = null;
  lastSeenOtpIds = new Set();
  if(dr) dr.innerHTML = '';
  const wrap = $('#top-pool-wrap');
  if(wrap) wrap.style.display = 'none';
  const bar = $('#bulk-bar');
  if(bar) bar.style.display = 'none';
  const np = $('#np');
  if(np) np.innerHTML = '';
  poolInfo.pool = 'Belum ada modem pool';
  const t = $('#side-hw-title');
  if(t) t.textContent = poolInfo.pool;
}

function renderLoggedOutState(){
  clearSessionState();
  const mc = v;
  if(!mc) return;
  mc.innerHTML = `
    <div style="min-height:65vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center;padding:32px 16px;">
      <div class="logo" style="width:58px;height:58px;font-size:26px;">S</div>
      <div>
        <h2 style="font-size:20px;font-weight:700;color:var(--tx);margin-bottom:4px;">SCPool Modem Pool Hub</h2>
        <p class="mu" style="font-size:13px;max-width:440px;line-height:1.5;margin:0 auto;">
          Otentikasi diperlukan untuk mengakses modem pool dan SMS. Silakan login menggunakan akun Anda atau daftarkan akun klien baru.
        </p>
      </div>
      <div style="display:flex;gap:10px;margin-top:6px;">
        <button type="button" class="btn pri" onclick="showLoginOverlay(true, 'login')">🔑 Masuk ke Panel</button>
        <button type="button" class="btn" onclick="showLoginOverlay(true, 'register')">📝 Daftar Akun Baru</button>
      </div>
    </div>
  `;
}

function updateUserUI(){
  const btn = $('#top-user-btn');
  const nameEl = $('#top-user-name');
  const pop = $('#pp');
  if(!btn || !nameEl || !pop) return;

  if(!currentUser || !currentUser.logged_in){
    nameEl.textContent = 'Masuk';
    pop.innerHTML = `<button type="button" onclick="showLoginOverlay(true, 'login')">🔑 Masuk ke Akun</button>`;
    const pw = $('#top-pool-wrap');
    if(pw) pw.style.display = 'none';
    renderNav();
    return;
  }

  const isSuper = currentUser.role === 'superadmin' || currentUser.role === 'admin';
  nameEl.textContent = `${currentUser.username} (${isSuper ? 'Admin' : 'Operator'})`;

  let popHtml = `
    <div style="padding:8px 12px;border-bottom:1px solid var(--ln);display:flex;flex-direction:column;gap:3px;">
      <div style="font-weight:700;color:var(--tx);">${esc(currentUser.username)}</div>
      <div class="mu mono" style="font-size:11px;">${isSuper ? 'Super Administrator' : 'Operator Client'}</div>
    </div>
    <a href="#/dashboard">📊 Dashboard</a>
    <a href="#/sms">✉️ Pesan SMS</a>
  `;

  if(isSuper){
    popHtml += `<a href="#/pengguna">👥 Kelola Pengguna</a>`;
  } else {
    popHtml += `<button type="button" onclick="openAgentTokenModal()">🔌 Token Agent Modem</button>`;
  }

  popHtml += `
    <a href="#/pengaturan">⚙️ Pengaturan</a>
    <button type="button" onclick="openChangePasswordModal()">🔑 Ubah Password</button>
    <div style="border-top:1px solid var(--ln);margin-top:4px;padding-top:4px;">
      <button type="button" onclick="submitLogout()" style="color:var(--er);display:flex;align-items:center;gap:6px;">
        <svg class="i" style="width:13px;height:13px;" viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>
        Keluar (Logout)
      </button>
    </div>
  `;
  pop.innerHTML = popHtml;
  renderNav();
}

async function loadPools(){
  const wrap = $('#top-pool-wrap');
  const sel = $('#top-pool-select');
  if(!currentUser || !currentUser.logged_in) return;
  try {
    const res = await fetch('/api/pools');
    if(!res.ok) return;
    const next = await res.json();
    const changed = JSON.stringify(next) !== JSON.stringify(poolsList);
    poolsList = next;
    if(activePoolId !== ALL_POOLS && !poolsList.some(p => p.id === activePoolId)){
      activePoolId = poolsList.length > 1 ? ALL_POOLS : (poolsList[0] ? poolsList[0].id : null);
    }
    if(activePoolId === ALL_POOLS && poolsList.length < 2) activePoolId = poolsList[0] ? poolsList[0].id : null;
    if(!changed) return;
    if(wrap && sel){
      wrap.style.display = poolsList.length > 1 ? 'inline-flex' : 'none';
      sel.innerHTML = (poolsList.length > 1 ? `<option value="${ALL_POOLS}"${activePoolId === ALL_POOLS ? ' selected' : ''}>Semua modem pool</option>` : '') + poolsList.map(p => `
        <option value="${esc(p.id)}"${p.id === activePoolId ? ' selected' : ''}>
          ${esc(p.name)}${p.owner ? ' · ' + esc(p.owner) : ''} (${p.port_count} port)
        </option>`).join('');
    }
    renderPoolManager(false);
  } catch(e){}
}

function poolStatusBadge(p){
  const on = p.agent_online;
  const off = p.offline_count ? ` · ${p.offline_count} port tidak terdeteksi` : '';
  return `<span class="mu" style="font-size:12px;">${on ? '🟢 Agent online' : '⚪ Agent offline'} · ${p.ready_count} SIM aktif${off}</span>`;
}

function renderPoolManager(force){
  const box = $('#pool-manager');
  if(!box) return;
  if(!force && box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
  const rows = poolsList.map(p => `
    <div class="pool-row" data-pool="${esc(p.id)}" style="display:grid;grid-template-columns:minmax(180px,1fr) auto;gap:10px;align-items:center;padding:12px 0;border-top:1px solid var(--ln);">
      <div style="min-width:0;">
        <label class="mu" style="font-size:11px;display:block;margin-bottom:3px;" for="pn-${esc(p.id)}">Nama modem pool · ${p.port_count} port${p.owner ? ' · ' + esc(p.owner) : ''}</label>
        <div style="display:flex;gap:6px;">
          <input id="pn-${esc(p.id)}" value="${esc(p.name)}" maxlength="60" style="flex:1;min-width:0;padding:7px 10px;font-size:13px;">
          <button type="button" class="btn pri" style="padding:6px 12px;font-size:12px;" onclick="savePoolName(${jq(p.id)})">Simpan</button>
        </div>
        <div style="margin-top:4px;">${poolStatusBadge(p)}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
        <button type="button" class="btn" style="padding:6px 10px;font-size:12px;" onclick="openMovePortsDialog(${jq(p.id)})" ${p.port_count ? '' : 'disabled'}>Pindahkan Port…</button>
        <button type="button" class="btn danger" style="padding:6px 10px;font-size:12px;" onclick="deleteEmptyPool(${jq(p.id)})" ${p.port_count ? 'disabled title="Pindahkan semua port dulu"' : ''}>Hapus</button>
      </div>
    </div>`).join('');
  box.innerHTML = `
    <h3>Modem Pool Saya</h3>
    <p class="mu" style="font-size:12.5px;margin-bottom:6px;">Modem pool dan jumlah port terdeteksi otomatis dari agent: tidak perlu diisi manual. Beri nama sesuka Anda dan ganti kapan saja. Bila dua modem pool tergabung (mis. lewat satu hub USB), pisahkan dengan "Pindahkan Port…".</p>
    ${rows || '<div class="note" style="margin:8px 0 0;">Belum ada modem pool. Jalankan agent di komputer yang terhubung ke modem pool Anda.</div>'}`;
}

async function savePoolName(poolId){
  const input = document.getElementById('pn-' + poolId);
  if(!input) return;
  const res = await fetch(`/api/pools/${encodeURIComponent(poolId)}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ name: input.value })
  });
  const data = await res.json().catch(() => ({}));
  if(!res.ok){ toast(data.detail || 'Gagal mengganti nama.', 1); return; }
  toast(`Nama diganti menjadi "${data.name}".`);
  poolsList = [];
  await loadPools();
  fetchBackendData();
}

async function deleteEmptyPool(poolId){
  if(!confirm('Hapus modem pool kosong ini?')) return;
  const res = await fetch(`/api/pools/${encodeURIComponent(poolId)}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if(!res.ok){ toast(data.detail || 'Gagal menghapus.', 1); return; }
  toast('Modem pool dihapus.');
  await loadPools();
  renderPoolManager(true);
}

async function openMovePortsDialog(poolId){
  const pool = poolsList.find(p => p.id === poolId);
  if(!pool) return;
  const res = await fetch(`/api/slots?pool_id=${encodeURIComponent(poolId)}`);
  const list = res.ok ? await res.json() : [];
  const others = poolsList.filter(p => p.id !== poolId && p.owner === pool.owner);
  dlg.innerHTML = `
    <form id="form-move-ports" style="display:grid;gap:12px;min-width:min(420px,86vw);">
      <h3>Pindahkan port dari ${esc(pool.name)}</h3>
      <p class="mu" style="font-size:12.5px;margin:0;">Riwayat SIM dan SMS ikut berpindah. Nomor port yang tersisa tidak digeser.</p>
      <div style="max-height:200px;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:6px;border:1px solid var(--ln);border-radius:8px;padding:8px;">
        ${list.map(s => `<label style="display:flex;gap:6px;align-items:center;font-size:12px;"><input type="checkbox" name="mv" value="${s.slot_index}"> Port ${String(s.slot_index).padStart(2, '0')} <span class="mu mono" style="font-size:10px;">${esc(s.port_name || '')}</span></label>`).join('')}
      </div>
      <label class="f">Pindahkan ke
        <select id="mv-target">
          ${others.map(p => `<option value="${esc(p.id)}">${esc(p.name)} (${p.port_count} port)</option>`).join('')}
          <option value="__new__">Modem pool baru…</option>
        </select>
      </label>
      <label class="f" id="mv-new-wrap" style="display:none;">Nama modem pool baru
        <input id="mv-new-name" maxlength="60" placeholder="Misal: Gudang B">
      </label>
      <small id="mv-err" class="er"></small>
      <div class="row end"><button type="button" class="btn" onclick="dlg.close()">Batal</button><button type="submit" class="btn pri">Pindahkan</button></div>
    </form>`;
  const target = $('#mv-target');
  const sync = () => { $('#mv-new-wrap').style.display = target.value === '__new__' ? '' : 'none'; };
  target.onchange = sync; sync();
  $('#form-move-ports').onsubmit = async (e) => {
    e.preventDefault();
    const chosen = [...dlg.querySelectorAll('input[name=mv]:checked')].map(i => parseInt(i.value, 10));
    const err = $('#mv-err');
    if(!chosen.length){ err.textContent = 'Pilih minimal satu port.'; return; }
    const body = { slot_indices: chosen };
    if(target.value === '__new__'){
      body.new_pool_name = $('#mv-new-name').value.trim();
      if(!body.new_pool_name){ err.textContent = 'Isi nama modem pool baru.'; return; }
    } else { body.target_pool_id = target.value; }
    const r = await fetch(`/api/pools/${encodeURIComponent(poolId)}/move-ports`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ err.textContent = d.detail || 'Gagal memindahkan port.'; return; }
    dlg.close();
    toast(`${d.moved} port dipindahkan ke ${d.target_pool_name}.`);
    poolsList = [];
    await loadPools();
    renderPoolManager(true);
    fetchBackendData();
  };
  dlg.showModal();
}

window.savePoolName = savePoolName;
window.deleteEmptyPool = deleteEmptyPool;
window.openMovePortsDialog = openMovePortsDialog;

function onPoolChange(poolId){
  activePoolId = poolId;
  const p = poolsList.find(x => x.id === poolId);
  toast(p ? `Beralih ke ${p.name}.` : 'Menampilkan semua modem pool.');
  selectedSlots.clear();
  fetchBackendData();
}

$('#form-login').onsubmit = async (e) => {
  e.preventDefault();
  const uname = $('#login-uname').value.trim();
  const pwd = $('#login-pwd').value;
  const errEl = $('#login-err');
  const btn = $('#btn-login-submit');

  errEl.textContent = '';
  btn.disabled = true;
  btn.textContent = 'Memverifikasi...';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ username: uname, password: pwd })
    });
    const data = await res.json();
    if(res.ok && data.success){
      toast(`Selamat datang, ${data.user.username}!`);
      $('#login-pwd').value = '';
      isAuthMandatory = false;
      await checkAuthAndInit();
    } else {
      errEl.textContent = data.detail || 'Username atau password salah.';
    }
  } catch(err) {
    errEl.textContent = 'Gagal menghubungi server: ' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Masuk ke Panel';
  }
};

function copyRegToken(){
  const input = $('#reg-token-display');
  if(!input) return;
  input.select();
  navigator.clipboard.writeText(input.value).then(() => {
    toast('Token agen berhasil disalin!');
  }).catch(() => {
    document.execCommand('copy');
    toast('Token agen disalin!');
  });
}
window.copyRegToken = copyRegToken;

const regFormEl = $('#form-register');
if(regFormEl){
  regFormEl.onsubmit = async (e) => {
    e.preventDefault();
    const uname = ($('#reg-uname')?.value || '').trim();
    const pwd = $('#reg-pwd')?.value || '';
    const msgEl = $('#reg-msg');
    const btn = $('#btn-reg-submit');
    const regFields = $('#reg-fields');
    const regSuccess = $('#reg-success-card');

    if(!uname || !pwd){
      if(msgEl){
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--er)';
        msgEl.textContent = 'Harap isi username dan password.';
      }
      return;
    }

    if(msgEl){
      msgEl.style.display = 'none';
      msgEl.textContent = '';
    }
    if(btn){
      btn.disabled = true;
      btn.textContent = 'Mendaftarkan...';
    }

    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: uname,
          password: pwd
        })
      });
      const data = await res.json();
      if(res.ok && data.success){
        if(regFields) regFields.style.display = 'none';
        if(regSuccess) regSuccess.style.display = 'flex';
        
        const tokenInput = $('#reg-token-display');
        if(tokenInput) tokenInput.value = data.agent_token || 'Dikirim setelah akun disetujui';
        
        const descEl = $('#reg-success-desc');
        if(descEl) descEl.textContent = data.message || 'Pendaftaran berhasil. Akun menunggu verifikasi Admin.';
        
        const dlBat = $('#btn-dl-bat');
        if(dlBat) { if(data.agent_token) dlBat.href = `/api/download/agent?file_type=bat&token=${encodeURIComponent(data.agent_token)}`; else dlBat.style.display = 'none'; }

        const dlScript = $('#btn-dl-script');
        if(dlScript) { if(data.agent_token) dlScript.href = `/api/download/agent?file_type=py&token=${encodeURIComponent(data.agent_token)}`; else dlScript.style.display = 'none'; }

        $('#reg-pwd').value = '';
        $('#login-uname').value = uname;
      } else {
        msgEl.style.display = 'block';
        msgEl.style.background = 'rgba(239,68,68,0.15)';
        msgEl.style.border = '1px solid var(--er)';
        msgEl.style.color = 'var(--er)';
        msgEl.textContent = data.detail || 'Pendaftaran gagal.';
      }
    } catch(err) {
      msgEl.style.display = 'block';
      msgEl.style.background = 'rgba(239,68,68,0.15)';
      msgEl.style.border = '1px solid var(--er)';
      msgEl.style.color = 'var(--er)';
    } finally {
      if(btn){
        btn.disabled = false;
        btn.textContent = 'Daftar Akun Klien';
      }
    }
  };
}

async function submitLogout(){
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch(e){}
  currentUser = null;
  activePoolId = null;
  slots = [];
  sms = [];
  toast('Anda telah keluar.');
  updateUserUI();
  showLoginOverlay(true, 'login');
  renderLoggedOutState();
  location.hash = '#/dashboard';
}

async function pUsers(){
  if(!currentUser || !currentUser.logged_in || (currentUser.role !== 'superadmin' && currentUser.role !== 'admin')){
    renderLoggedOutState();
    return;
  }
  v.innerHTML = `
    <div class="ph">
      <div>
        <h2 style="font-size:18px;font-weight:700;color:var(--tx);">Manajemen Pengguna &amp; Modem Pool</h2>
        <p class="mu" style="font-size:12.5px;margin-top:2px;">Kelola akun klien remote, alokasi modem pool terisolasi, dan token koneksi agent.exe.</p>
      </div>
      <button type="button" class="btn pri" onclick="openCreateUserModal()" style="display:inline-flex;align-items:center;gap:6px;">
        <svg class="i" style="width:14px;height:14px;" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
        Tambah Pengguna Baru
      </button>
    </div>
    <section class="panel" style="padding:0;overflow:hidden;">
      <div id="users-table-wrap" style="overflow-x:auto;">
        <div class="note" style="margin:20px;text-align:center;">Memuat data pengguna...</div>
      </div>
    </section>
  `;
  loadAdminUsersList();
}

async function loadAdminUsersList(){
  const wrap = $('#users-table-wrap');
  if(!wrap) return;

  try {
    const res = await fetch('/api/admin/users');
    if(!res.ok) throw new Error('HTTP ' + res.status);
    const users = await res.json();

    if(!users.length){
      wrap.innerHTML = `<div class="mu" style="padding:32px;text-align:center;">Belum ada pengguna terdaftar.</div>`;
      return;
    }

    wrap.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:13px;text-align:left;">
        <thead>
          <tr style="border-bottom:1px solid var(--ln);background:var(--p2);color:var(--mu);font-size:11.5px;text-transform:uppercase;letter-spacing:0.04em;">
            <th style="padding:12px 16px;">Pengguna</th>
            <th style="padding:12px 16px;">Peran</th>
            <th style="padding:12px 16px;">Modem Pool</th>
            <th style="padding:12px 16px;">Token Agent (Remote)</th>
            <th style="padding:12px 16px;">Status</th>
            <th style="padding:12px 16px;text-align:right;">Aksi</th>
          </tr>
        </thead>
        <tbody>
          ${users.map(u => {
            const isSuper = u.role === 'superadmin' || u.role === 'admin';
            const isProtected = isSuper || (u.username && u.username.toLowerCase() === 'admin') || (currentUser && u.id === currentUser.id);
            const tokMask = u.agent_token ? (u.agent_token.slice(0, 14) + '...') : '—';
            return `
              <tr style="border-bottom:1px solid var(--ln);">
                <td style="padding:14px 16px;">
                  <b style="color:var(--tx);">${esc(u.username)}</b>
                  <div class="mu mono" style="font-size:11px;">ID: ${u.id}</div>
                </td>
                <td style="padding:14px 16px;">
                  <span class="sb-pill" style="background:${isSuper ? 'rgba(59,130,246,0.15)' : 'rgba(16,185,129,0.15)'};color:${isSuper ? 'var(--blue)' : 'var(--ac)'};font-size:11px;font-weight:700;">
                    ${isSuper ? 'Superadmin' : 'Operator'}
                  </span>
                </td>
                <td style="padding:14px 16px;">
                  <div>${u.unit_count || 0} modem pool</div>
                  <div class="mu" style="font-size:11px;">${u.agent_online ? '🟢 Agent online' : '⚪ Agent offline'}</div>
                </td>
                <td style="padding:14px 16px;">
                  <div style="display:flex;align-items:center;gap:6px;">
                    <span class="mono" style="font-size:11.5px;color:var(--mu);">${esc(tokMask)}</span>
                    ${u.agent_token ? `
                      <button type="button" class="btn ic" style="width:24px;height:24px;padding:0;" onclick="copyToClipboard(${jq(u.agent_token)}, ${jq('Token agent milik ' + u.username + ' disalin.')})" title="Salin Token Lengkap">
                        <svg class="i" style="width:12px;height:12px;" viewBox="0 0 24 24"><path d="M8 4v12a2 2 0 002 2h8a2 2 0 002-2V7.242a2 2 0 00-.602-1.43L16.083 2.57A2 2 0 0014.685 2H10a2 2 0 00-2 2z"/><path d="M16 18v2a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h2"/></svg>
                      </button>
                    ` : ''}
                  </div>
                </td>
                <td style="padding:14px 16px;">
                  <span class="sb-pill" style="background:${u.is_active ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.15)'};color:${u.is_active ? 'var(--ac)' : '#f59e0b'};font-size:11px;font-weight:700;">
                    ${u.is_active ? 'Aktif' : 'Menunggu Persetujuan'}
                  </span>
                </td>
                <td style="padding:14px 16px;text-align:right;">
                  <div style="display:inline-flex;gap:6px;align-items:center;">
                    ${!u.is_active ? `
                      <button type="button" class="btn pri" style="padding:4px 10px;font-size:11px;background:#10b981;border-color:#10b981;" onclick="approveUserAccount(${u.id}, ${jq(u.username)})">
                        ✅ Setujui
                      </button>
                      <button type="button" class="btn danger" style="padding:4px 8px;font-size:11px;" onclick="rejectUserAccount(${u.id}, ${jq(u.username)})">
                        ❌ Tolak
                      </button>
                    ` : `
                      <button type="button" class="btn" style="padding:4px 8px;font-size:11px;" onclick="regenerateUserToken(${u.id}, ${jq(u.username)})">
                        Token Baru
                      </button>
                      ${!isProtected ? `
                        <button type="button" class="btn" style="padding:4px 8px;font-size:11px;" onclick="toggleUserActive(${u.id}, false)">
                          Nonaktifkan
                        </button>
                      ` : ''}
                    `}
                    ${!isProtected ? `
                      <button type="button" class="btn danger" style="padding:4px 8px;font-size:11px;" onclick="deleteUserAccount(${u.id}, ${jq(u.username)})">
                        Hapus
                      </button>
                    ` : `
                      <span class="mu mono" style="font-size:11px;padding:3px 8px;background:var(--p2);border:1px solid var(--ln);border-radius:4px;">Terproteksi</span>
                    `}
                  </div>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  } catch(err) {
    wrap.innerHTML = `<div class="note" style="margin:16px;color:var(--er);">Gagal memuat pengguna: ${esc(err.message)}</div>`;
  }
}

async function approveUserAccount(userId, username){
  if(!confirm(`Setujui pendaftaran klien '${username}' dan aktifkan akses pool?`)) return;
  try {
    const res = await fetch(`/api/admin/users/${userId}/approve`, { method: 'POST' });
    const data = await res.json();
    if(res.ok && data.success){
      toast(data.message);
      loadAdminUsersList();
    } else {
      toast(data.detail || 'Gagal menyetujui akun.', 1);
    }
  } catch(e){
    toast('Error: ' + e.message, 1);
  }
}

async function rejectUserAccount(userId, username){
  if(!confirm(`Tolak pendaftaran akun '${username}'?`)) return;
  try {
    const res = await fetch(`/api/admin/users/${userId}/reject`, { method: 'POST' });
    const data = await res.json();
    if(res.ok && data.success){
      toast(data.message);
      loadAdminUsersList();
    } else {
      toast(data.detail || 'Gagal menolak akun.', 1);
    }
  } catch(e){
    toast('Error: ' + e.message, 1);
  }
}

function openCreateUserModal(){
  dlg.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
      <h3 style="display:flex;align-items:center;gap:8px;">
        <svg class="i" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>
        Tambah Pengguna Baru
      </h3>
      <button type="button" class="btn ic" onclick="dlg.close()">×</button>
    </div>
    <form id="form-create-user" style="display:grid;gap:12px;">
      <label class="f">
        Username:
        <input id="cu-uname" required minlength="3" placeholder="Contoh: budi, cabang_sby">
      </label>
      <label class="f">
        Password Awal:
        <input type="password" id="cu-pwd" required minlength="8" placeholder="Minimal 8 karakter">
      </label>
      <label class="f">
        Peran:
        <select id="cu-role">
          <option value="operator" selected>Operator (Klien Remote)</option>
          <option value="superadmin">Super Administrator</option>
        </select>
      </label>
      <small id="cu-err" style="color:var(--er);"></small>
      <div class="row end" style="margin-top:6px;">
        <button type="button" class="btn" onclick="dlg.close()">Batal</button>
        <button type="submit" class="btn pri" id="btn-submit-create-user">Simpan Pengguna</button>
      </div>
    </form>
  `;

  $('#form-create-user').onsubmit = async (e) => {
    e.preventDefault();
    const uname = $('#cu-uname').value.trim();
    const pwd = $('#cu-pwd').value;
    const role = $('#cu-role').value;
    const errEl = $('#cu-err');
    const btn = $('#btn-submit-create-user');

    btn.disabled = true;
    btn.textContent = 'Menyimpan...';

    try {
      const res = await fetch('/api/admin/users', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: uname,
          password: pwd,
          role: role
        })
      });
      const data = await res.json();
      if(res.ok && data.success){
        dlg.close();
        toast(`Pengguna ${uname} berhasil dibuat!`);
        loadAdminUsersList();
      } else {
        errEl.textContent = data.detail || 'Gagal membuat pengguna.';
      }
    } catch(err) {
      errEl.textContent = 'Error: ' + err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Simpan Pengguna';
    }
  };

  dlg.showModal();
}

async function openAgentTokenModal(){
  const token = currentUser?.agent_token || '—';
  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/agent';

  dlg.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="display:flex;align-items:center;gap:8px;">
        <svg class="i" viewBox="0 0 24 24"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
        Koneksi Remote Agent Modem Pool
      </h3>
      <button type="button" class="btn ic" onclick="dlg.close()">×</button>
    </div>
    <div style="display:grid;gap:12px;">
      <div class="note" style="margin:0;font-size:12.5px;">
        Gunakan informasi di bawah untuk menghubungkan modem pool fisik Anda ke server melalui <b>agent.exe</b> tanpa memerlukan source code.
      </div>
      <div>
        <label style="font-size:11.5px;font-weight:600;color:var(--mu);display:block;margin-bottom:4px;">Token Agent Anda:</label>
        <div style="display:flex;gap:6px;">
          <input class="mono" value="${esc(token)}" readonly style="flex:1;font-size:12px;background:var(--p2);">
          <button type="button" class="btn pri" onclick="copyToClipboard(${jq(token)}, 'Token agent disalin.')">Salin</button>
        </div>
      </div>
      <div>
        <label style="font-size:11.5px;font-weight:600;color:var(--mu);display:block;margin-bottom:4px;">WebSocket Server URL:</label>
        <div style="display:flex;gap:6px;">
          <input class="mono" value="${esc(wsUrl)}" readonly style="flex:1;font-size:12px;background:var(--p2);">
          <button type="button" class="btn" onclick="copyToClipboard(${jq(wsUrl)}, 'URL WebSocket disalin.')">Salin</button>
        </div>
      </div>
      <div id="modal-agent-status-box" style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:10px 14px;font-size:12px;display:flex;align-items:center;justify-content:space-between;">
        <span>Status Agent:</span>
        <b id="modal-agent-status-val" style="color:var(--mu);">Memeriksa...</b>
      </div>
      <div class="row end">
        <button type="button" class="btn" onclick="dlg.close()">Tutup</button>
      </div>
    </div>
  `;
  dlg.showModal();

  try {
    const res = await fetch('/api/agent/status');
    const d = await res.json();
    const valEl = $('#modal-agent-status-val');
    if(valEl){
      if(d.is_online){
        valEl.innerHTML = `<span style="color:var(--ac);">🟢 Terhubung (${d.info?.port_count || 0} port terdeteksi)</span>`;
      } else {
        valEl.innerHTML = `<span style="color:var(--mu);">⚪ Belum Terhubung (Offline)</span>`;
      }
    }
  } catch(e){}
}

async function regenerateUserToken(userId, uname){
  if(!confirm(`Buat ulang token agent untuk pengguna ${uname}? Token lama tidak akan berlaku lagi.`)) return;
  try {
    const res = await fetch(`/api/admin/users/${userId}/token`, { method: 'POST' });
    const data = await res.json();
    if(res.ok && data.success){
      toast(`Token baru berhasil diterbitkan untuk ${uname}.`);
      loadAdminUsersList();
    }
  } catch(e){
    toast('Gagal buat token baru: ' + e.message, 1);
  }
}

async function toggleUserActive(userId, newActive){
  try {
    const res = await fetch(`/api/admin/users/${userId}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ is_active: newActive })
    });
    const data = await res.json();
    if(res.ok && data.success){
      toast('Status pengguna diperbarui.');
      loadAdminUsersList();
    } else {
      toast(data.detail || 'Gagal perbarui status.', 1);
    }
  } catch(e){
    toast('Error: ' + e.message, 1);
  }
}

async function deleteUserAccount(userId, uname){
  if(!confirm(`Hapus akun pengguna ${uname}? Aksi ini tidak dapat dibatalkan.`)) return;
  try {
    const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
    const data = await res.json();
    if(res.ok && data.success){
      toast(`Pengguna ${uname} dihapus.`);
      loadAdminUsersList();
      loadPools();
    } else {
      toast(data.detail || 'Gagal menghapus pengguna.', 1);
    }
  } catch(e){
    toast('Error: ' + e.message, 1);
  }
}

async function checkAuthAndInit(){
  setThemeMode(currentThemeMode);
  try{
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if(data && data.logged_in){
      currentUser = data;
      hideLoginOverlay();
      updateUserUI();
      await fetchBackendData();
      route();
    } else {
      currentUser = null;
      slots = [];
      sms = [];
      updateUserUI();
      showLoginOverlay(true, 'login');
      renderLoggedOutState();
    }
  }catch(e){
    console.warn("Auth check error:", e);
    currentUser = null;
    slots = [];
    sms = [];
    updateUserUI();
    showLoginOverlay(true, 'login');
    renderLoggedOutState();
  }
}


/* ==========================================================================
   MODE 2 JAVASCRIPT: FAST OTP STREAM & NUMBER LIST AUTO-COPY
   ========================================================================== */


function switchDashMode(mode){
  currentDashMode = mode;
  localStorage.setItem('scpool_dash_mode', mode);
  $('#btn-mode-grid')?.classList.toggle('on', mode === 'grid');
  $('#btn-mode-stream')?.classList.toggle('on', mode === 'stream');
  const gs = $('#gs');
  if(gs){
    gs.placeholder = (mode === 'grid') ? "Cari slot, nomor, atau provider…" : "Cari nomor atau OTP…";
  }
  if(isSelectMode) exitSelectMode();
  pDash();
}

function toggleOtpSound(){
  soundEnabled = !soundEnabled;
  localStorage.setItem('scpool_sound', soundEnabled);
  const btn = $('#btn-toggle-sound');
  if(btn) btn.textContent = soundEnabled ? '🔔 Suara: Aktif' : '🔕 Suara: Bisu';
  toast(`Suara notifikasi OTP ${soundEnabled ? 'diaktifkan' : 'dibisukan'}.`);
}

function playOtpChime(){
  try{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if(ctx.state === 'suspended') ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(880.00, ctx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.38);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.38);
  }catch(e){}
}

function detectAppFromSMS(body = '', sender = ''){
  const b = (body + ' ' + sender).toLowerCase();
  if(/whatsapp|wa\.me|\bwa\b/i.test(b)){
    return { name: 'WhatsApp', key: 'whatsapp', color: '#25D366', bg: 'linear-gradient(135deg, rgba(37,211,102,0.22) 0%, rgba(18,140,126,0.3) 100%)' };
  }
  if(/telegram|tg\.me|\btg\b/i.test(b)){
    return { name: 'Telegram', key: 'telegram', color: '#2AABEE', bg: 'linear-gradient(135deg, rgba(42,171,238,0.22) 0%, rgba(34,158,217,0.3) 100%)' };
  }
  if(/tiktok/i.test(b)){
    return { name: 'TikTok', key: 'tiktok', color: '#FE2C55', bg: 'linear-gradient(135deg, rgba(254,44,85,0.25) 0%, rgba(0,242,254,0.18) 100%)' };
  }
  if(/google|g-|gmail|youtube/i.test(b)){
    return { name: 'Google', key: 'google', color: '#4285F4', bg: 'linear-gradient(135deg, rgba(66,133,244,0.18) 0%, rgba(234,67,53,0.18) 100%)' };
  }
  if(/shopee/i.test(b)){
    return { name: 'Shopee', key: 'shopee', color: '#EE4D2D', bg: 'linear-gradient(135deg, rgba(238,77,45,0.25) 0%, rgba(255,87,34,0.2) 100%)' };
  }
  if(/tokopedia|toped/i.test(b)){
    return { name: 'Tokopedia', key: 'tokopedia', color: '#03AC0E', bg: 'linear-gradient(135deg, rgba(3,172,14,0.25) 0%, rgba(32,201,151,0.2) 100%)' };
  }
  if(/instagram|ig\.me/i.test(b)){
    return { name: 'Instagram', key: 'instagram', color: '#E1306C', bg: 'linear-gradient(135deg, rgba(131,58,180,0.25) 0%, rgba(253,29,29,0.2) 100%)' };
  }
  if(/facebook|meta/i.test(b)){
    return { name: 'Facebook', key: 'facebook', color: '#1877F2', bg: 'linear-gradient(135deg, rgba(24,119,242,0.25) 0%, rgba(13,71,161,0.2) 100%)' };
  }
  if(/twitter|x corp/i.test(b)){
    return { name: 'Twitter / X', key: 'twitter', color: '#1DA1F2', bg: 'linear-gradient(135deg, rgba(29,161,242,0.25) 0%, rgba(0,0,0,0.4) 100%)' };
  }
  if(/dana/i.test(b)){
    return { name: 'DANA', key: 'dana', color: '#118EEA', bg: 'linear-gradient(135deg, rgba(17,142,234,0.25) 0%, rgba(0,90,170,0.2) 100%)' };
  }
  if(/ovo/i.test(b)){
    return { name: 'OVO', key: 'ovo', color: '#4C3494', bg: 'linear-gradient(135deg, rgba(76,52,148,0.25) 0%, rgba(115,82,199,0.2) 100%)' };
  }
  if(/gojek|gopay/i.test(b)){
    return { name: 'Gojek / GoPay', key: 'gojek', color: '#00AA13', bg: 'linear-gradient(135deg, rgba(0,170,19,0.25) 0%, rgba(0,128,14,0.2) 100%)' };
  }
  if(/grab/i.test(b)){
    return { name: 'Grab', key: 'grab', color: '#00B14F', bg: 'linear-gradient(135deg, rgba(0,177,79,0.25) 0%, rgba(0,120,54,0.2) 100%)' };
  }
  if(/bca|klikbca/i.test(b)){
    return { name: 'Bank BCA', key: 'bca', color: '#0060AF', bg: 'linear-gradient(135deg, rgba(0,96,175,0.25) 0%, rgba(0,40,90,0.3) 100%)' };
  }
  if(/mandiri|livin/i.test(b)){
    return { name: 'Bank Mandiri', key: 'mandiri', color: '#003876', bg: 'linear-gradient(135deg, rgba(0,56,118,0.25) 0%, rgba(245,158,11,0.2) 100%)' };
  }
  if(/bri|brimo/i.test(b)){
    return { name: 'Bank BRI', key: 'bri', color: '#00529C', bg: 'linear-gradient(135deg, rgba(0,82,156,0.25) 0%, rgba(0,150,214,0.2) 100%)' };
  }
  if(/discord/i.test(b)){
    return { name: 'Discord', key: 'discord', color: '#5865F2', bg: 'linear-gradient(135deg, rgba(88,101,242,0.25) 0%, rgba(47,49,54,0.3) 100%)' };
  }
  if(/lazada/i.test(b)){
    return { name: 'Lazada', key: 'lazada', color: '#0F146D', bg: 'linear-gradient(135deg, rgba(15,20,109,0.25) 0%, rgba(245,114,36,0.2) 100%)' };
  }
  return { name: sender || 'Verifikasi SMS', key: 'generic', color: '#F59E0B', bg: 'linear-gradient(135deg, rgba(245,158,11,0.22) 0%, rgba(217,119,6,0.2) 100%)' };
}

function renderAnimatedAppLogo(key, color){
  if(key === 'whatsapp'){
    return `<svg class="anim-pulse" viewBox="0 0 24 24" fill="${color}"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"/></svg>`;
  }
  if(key === 'telegram'){
    return `<svg class="anim-glide" viewBox="0 0 24 24" fill="${color}"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/></svg>`;
  }
  if(key === 'tiktok'){
    return `<svg class="anim-rhythm" viewBox="0 0 24 24" fill="${color}"><path d="M12.53.02C13.84 0 15.14.01 16.44 0c.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.24 1.07-.14 1.61.24 1.16 1.18 2.09 2.35 2.3 1.05.2 2.16-.08 2.94-.78.78-.69 1.15-1.75 1.15-2.79V.02h2.24z"/></svg>`;
  }
  if(key === 'google'){
    return `<svg class="anim-shimmer" viewBox="0 0 24 24"><path fill="#EA4335" d="M12 5c1.6 0 3 .6 4.1 1.6l3.1-3.1C17.3 1.8 14.8 1 12 1 7.5 1 3.7 3.6 1.9 7.3l3.7 2.9C6.5 7.4 9 5 12 5z"/><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.7-.2-2.3H12v4.6h6.5c-.3 1.5-1.1 2.8-2.4 3.7l3.7 2.9c2.2-2 3.7-5 3.7-8.9z"/><path fill="#FBBC05" d="M5.6 14.8c-.2-.7-.4-1.5-.4-2.3s.2-1.6.4-2.3L1.9 7.3C.7 9.7 0 12 0 12s.7 2.3 1.9 4.7l3.7-1.9z"/><path fill="#34A853" d="M12 23c3.2 0 6-1.1 8-3l-3.7-2.9c-1.1.7-2.5 1.2-4.3 1.2-3 0-5.5-2.4-6.4-5.2L1.9 16C3.7 19.7 7.5 23 12 23z"/></svg>`;
  }
  if(key === 'shopee'){
    return `<svg class="anim-float" viewBox="0 0 24 24" fill="${color}"><path d="M19.5 7.5h-2.25V6a5.25 5.25 0 00-10.5 0v1.5H4.5A1.5 1.5 0 003 9l1.5 12a1.5 1.5 0 001.5 1.5h12a1.5 1.5 0 001.5-1.5L21 9a1.5 1.5 0 00-1.5-1.5zm-11.25-1.5a3.75 3.75 0 017.5 0v1.5h-7.5V6zm4.5 11.5c-2 0-3.25-.9-3.25-2.25 0-1.6 1.8-1.9 2.8-2.1.8-.1 1.2-.3 1.2-.6 0-.4-.4-.7-1-.7-.8 0-1.5.3-2.1.8l-.8-1.1c.8-.7 1.8-1 2.9-1 1.8 0 3 1 3 2.2 0 1.5-1.7 1.9-2.7 2.1-.9.1-1.3.3-1.3.6 0 .4.4.8 1.2.8.9 0 1.8-.4 2.4-1l.8 1.1c-.8.8-2 1.3-3.2 1.3z"/></svg>`;
  }
  if(key === 'tokopedia'){
    return `<svg class="anim-pulse" viewBox="0 0 24 24" fill="${color}"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-4 7a2 2 0 110 4 2 2 0 010-4zm8 0a2 2 0 110 4 2 2 0 010-4zm-4 8c-2.33 0-4.31-1.46-5.11-3.5h10.22c-.8 2.04-2.78 3.5-5.11 3.5z"/></svg>`;
  }
  if(key === 'instagram'){
    return `<svg class="anim-pulse" viewBox="0 0 24 24" fill="${color}"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>`;
  }
  if(key === 'dana' || key === 'ovo' || key === 'gojek' || key === 'grab'){
    return `<svg class="anim-float" viewBox="0 0 24 24" fill="${color}"><path d="M20 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V6a2 2 0 00-2-2zm0 14H4V8h16v10zm-2-5a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z"/></svg>`;
  }
  if(key === 'bca' || key === 'mandiri' || key === 'bri'){
    return `<svg class="anim-shimmer" viewBox="0 0 24 24" fill="${color}"><path d="M12 1L2 6v2h20V6L12 1zm-7 9h3v8H5v-8zm6 0h3v8h-3v-8zm6 0h3v8h-3v-8zM2 20h20v2H2v-2z"/></svg>`;
  }
  return `<svg class="anim-pulse" viewBox="0 0 24 24" fill="${color}"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 6c1.66 0 3 1.34 3 3 0 1.09-.59 2.04-1.46 2.56l.46 2.44h-4l.46-2.44A2.99 2.99 0 019 10c0-1.66 1.34-3 3-3z"/></svg>`;
}

function highlightOTPInBody(body = '', otp = ''){
  const safe = esc(body);
  if(!otp) return safe;
  const safeOtp = esc(otp);
  return safe.split(safeOtp).join(`<mark style="background:rgba(16,185,129,0.35);color:#fff;font-weight:800;border-radius:4px;padding:1px 5px;">${safeOtp}</mark>`);
}

function pStreamDash(){
  if(!currentUser || !currentUser.logged_in){
    renderLoggedOutState();
    return;
  }
  const c = cnt();
  const existingContainer = $('#fast-otp-container');
  if(!existingContainer){
    v.innerHTML = `
      <div id="fast-otp-container" class="fast-otp-wrapper">
        <!-- 1. BAGIAN ATAS: TABEL TERPADU (CENTER DI TENGAH) -->
        <div class="fast-unified-table-box">
          <div class="unified-table-header">
            <div>
              <b style="font-size:14px;">Daftar SIM & OTP</b>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <div style="display:flex;gap:4px;flex-wrap:wrap;" id="fnum-filter-chips"></div>
              <input type="search" id="fnum-search" placeholder="Cari nomor, port, operator…" style="width:200px;font-size:12px;padding:5px 10px;" oninput="updateUnifiedTable()">
            </div>
          </div>

          <div class="unified-table-columns-header">
            <div>Nomor SIM & Status</div>
            <div>OTP Terakhir</div>
          </div>

          <div class="unified-table-body" id="unified-table-rows"></div>
        </div>

        <!-- 2. BAGIAN BAWAH: LIVE STREAM OTP MASUK (PRESISI LURUS SEJAJAR & BERGULIR) -->
        <div class="fast-stream-bottom-section">
          <div class="stream-bottom-head">
            <div style="display:flex;align-items:center;gap:10px;">
              <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#10b981;box-shadow:0 0 10px #10b981;animation:appPulse 1.5s infinite;"></span>
              <b style="font-size:14.5px;letter-spacing:-0.01em;">OTP Masuk</b>
              <span class="mu" style="font-size:12px;" id="otp-total-count"></span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <button type="button" class="btn" style="padding:4px 10px;font-size:11.5px;" id="btn-toggle-sound" onclick="toggleOtpSound()">
                ${soundEnabled ? 'Suara: On' : 'Suara: Off'}
              </button>
              <button type="button" class="btn" style="padding:4px 10px;font-size:11.5px;" onclick="$('#top-poll').click()">
                Periksa SMS
              </button>
            </div>
          </div>
          <div class="stream-bottom-feed" id="otp-stream-feed"></div>
        </div>
      </div>
    `;
  }
  updateUnifiedTable();
  updateFastOtpFeed();
}

function renderFastFilterChips(){
  const container = $('#fnum-filter-chips');
  if(!container) return;

  const opSet = new Set();
  slots.forEach(s => {
    if(s.st === 'active' && s.op && s.op !== '—' && s.op !== 'SIM' && s.op !== 'BELUM DIKETAHUI'){
      opSet.add(s.op.trim());
    }
  });
  const distinctOps = Array.from(opSet);

  if(fastNumFilterOp !== 'all' && fastNumFilterOp !== 'active' && !distinctOps.some(o => o.toLowerCase() === fastNumFilterOp.toLowerCase())){
    fastNumFilterOp = 'all';
  }

  let html = `
    <button type="button" class="btn ${fastNumFilterOp==='all'?'pri':''}" style="padding:2px 8px;font-size:11px;" onclick="setFastFilterOp('all')">Semua</button>
    <button type="button" class="btn ${fastNumFilterOp==='active'?'pri':''}" style="padding:2px 8px;font-size:11px;" onclick="setFastFilterOp('active')">Aktif</button>
  `;

  if(distinctOps.length > 1){
    distinctOps.forEach(opName => {
      const isSelected = fastNumFilterOp.toLowerCase() === opName.toLowerCase();
      html += `
        <button type="button" class="btn ${isSelected ? 'pri' : ''}" style="padding:2px 8px;font-size:11px;" onclick="setFastFilterOp(${jq(opName)})">${esc(opName)}</button>
      `;
    });
  }

  container.innerHTML = html;
}

function setFastFilterOp(op){
  fastNumFilterOp = op;
  renderFastFilterChips();
  updateUnifiedTable();
}

function updateUnifiedTable(){
  renderFastFilterChips();
  const rowsEl = $('#unified-table-rows');
  if(!rowsEl) return;
  const q = ($('#fnum-search')?.value || '').toLowerCase().trim();

  const filteredSlots = slots.filter(s => {
    if(fastNumFilterOp === 'active' && s.st !== 'active') return false;
    if(fastNumFilterOp !== 'all' && fastNumFilterOp !== 'active'){
      const opName = (s.op || '').toLowerCase();
      if(!opName.includes(fastNumFilterOp.toLowerCase())) return false;
    }
    if(!q) return true;
    
    // Check if slot info matches
    const slotMatch = (s.pool_name + ' ' + slotLabel(s)).toLowerCase().includes(q) ||
                      (s.num && s.num.toLowerCase().includes(q)) ||
                      (s.op && s.op.toLowerCase().includes(q)) ||
                      (s.dev && s.dev.toLowerCase().includes(q));
    if(slotMatch) return true;

    // Check if any OTP belonging to this slot matches
    const slotOtps = sms.filter(m => m.slot === s.id && m.otp);
    return slotOtps.some(m => {
      const app = detectAppFromSMS(m.body, m.n);
      return app.name.toLowerCase().includes(q) ||
             (m.otp && m.otp.toLowerCase().includes(q)) ||
             (m.body && m.body.toLowerCase().includes(q));
    });
  });

  if(!filteredSlots.length){
    rowsEl.innerHTML = `<div class="mu" style="padding:32px;text-align:center;font-size:13px;">Tidak ada nomor atau OTP yang cocok dengan filter.</div>`;
    return;
  }

  const rowHtml = s => {
    const isAct = s.st === 'active';
    const numDisplay = (s.num && s.num !== '—' && s.num !== 'BELUM DIKETAHUI') ? s.num : (isAct ? 'Nomor Belum Terbaca' : '(Slot Kosong)');
    const canCopy = isAct && s.num && s.num !== '—' && s.num !== 'BELUM DIKETAHUI';

    // Ambil SEMUA OTP milik slot ini (selain OTP diabaikan)
    const slotOtps = sms.filter(m => m.slot === s.id && m.otp);

    // Kumpulkan logo aplikasi unik yang sudah masuk ke nomor ini (agar tampak tanpa perlu scroll)
    const appMap = new Map();
    slotOtps.forEach(m => {
      const app = detectAppFromSMS(m.body, m.n);
      if(!appMap.has(app.name)){
        appMap.set(app.name, app);
      }
    });
    const uniqueApps = Array.from(appMap.values());

    const appBadgesHtml = uniqueApps.length ? `
      <div class="app-badges-row">
        ${uniqueApps.map(app => `
          <span class="app-tag-badge" style="background:${app.bg};border-color:${app.color}40;color:${app.color};">
            ${renderAnimatedAppLogo(app.key, app.color)}
            ${esc(app.name)}
          </span>
        `).join('')}
      </div>
    ` : `<span class="mu" style="font-size:11.5px;">${isAct ? 'Belum ada OTP' : 'Slot kosong'}</span>`;

    // Kontainer OTP bergulir: tinggi tepat 1 kartu (44px), namun jika ada lebih dari 1 OTP tetap dapat digulir
    const otpsListHtml = slotOtps.length ? `
      <div style="display:flex;align-items:center;gap:6px;min-width:0;width:100%;">
        <div class="row-otp-scroll-list" id="otp-scroll-${domId(s.id)}">
          ${slotOtps.map((m, idx) => {
            const app = detectAppFromSMS(m.body, m.n);
            return `
              <div class="mini-otp-card">
                <div style="display:flex;align-items:center;gap:8px;min-width:0;">
                  <div style="width:22px;height:22px;border-radius:6px;background:${app.bg};display:grid;place-items:center;flex:none;">
                    ${renderAnimatedAppLogo(app.key, app.color)}
                  </div>
                  <div style="display:flex;flex-direction:column;gap:1px;min-width:0;">
                    <div style="font-weight:700;font-size:12px;color:var(--tx);display:flex;gap:6px;align-items:center;">
                      <span>${esc(app.name)}</span>
                      <span class="mu" style="font-size:10.5px;font-weight:normal;">• ${ago(m.m)}</span>
                      ${slotOtps.length > 1 ? `<span class="mu mono" style="font-size:10px;padding:0 5px;background:rgba(255,255,255,0.06);border-radius:6px;" title="Gulir untuk melihat OTP lain">↕ ${idx + 1}/${slotOtps.length}</span>` : ''}
                    </div>
                    <div class="mu mono" style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px;" title="${esc(m.body)}">${esc(m.body)}</div>
                  </div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex:none;">
                  <span class="mini-otp-digits">${esc(m.otp)}</span>
                  <button type="button" class="btn pri" style="padding:3px 8px;font-size:11.5px;font-weight:700;" id="btn-mini-otp-${m.id}" onclick="event.stopPropagation();copyFastOTP(${jq(m.otp)}, 'btn-mini-otp-${m.id}')" title="Salin kode ${m.otp}">
                    Salin
                  </button>
                </div>
              </div>
            `;
          }).join('')}
        </div>
        ${slotOtps.length > 1 ? `
          <div style="display:flex;flex-direction:column;gap:2px;flex:none;">
            <button type="button" class="btn ic" style="width:20px;height:20px;padding:0;font-size:9px;line-height:1;border-radius:4px;" onclick="event.stopPropagation();scrollSlotOtp(${jq(s.id)}, -1)" title="OTP Sebelumnya">▲</button>
            <button type="button" class="btn ic" style="width:20px;height:20px;padding:0;font-size:9px;line-height:1;border-radius:4px;" onclick="event.stopPropagation();scrollSlotOtp(${jq(s.id)}, 1)" title="OTP Berikutnya">▼</button>
          </div>
        ` : ''}
      </div>
    ` : '';

    return `
      <div class="unified-slot-row" id="unified-row-${domId(s.id)}">
        <!-- Sisi Kiri: Nomor & Port -->
        <div class="slot-left-col st-${s.st}" id="slot-left-${domId(s.id)}" onclick="onFastCardClick(${jq(s.id)}, ${jq(s.num||'')})" title="${canCopy ? 'Klik untuk salin ' + esc(s.num) : esc(s.pool_name) + ' · Port ' + s.slot_index}">
          <div style="display:flex;flex-direction:column;gap:3px;min-width:0;">
            <div style="display:flex;align-items:center;gap:6px;">
              <span class="fnum-port">${slotLabel(s)}</span>
              <span class="fnum-val mono">${esc(numDisplay)}</span>
            </div>
            <div style="font-size:11px;color:var(--mu);display:flex;gap:6px;align-items:center;">
              <span>${esc(s.op || '—')}</span>
              <span>•</span>
              <span class="mono">${esc(s.dev)}</span>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:6px;flex:none;">
            ${canCopy ? `<span class="copy-badge"><svg class="i" style="width:13px;height:13px;" viewBox="0 0 24 24"><path d="M8 4v12a2 2 0 002 2h8a2 2 0 002-2V7.242a2 2 0 00-.602-1.43L16.083 2.57A2 2 0 0014.685 2H10a2 2 0 00-2 2z"/><path d="M16 18v2a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h2"/></svg> Salin</span>` : `<span class="mu" style="font-size:11px;">${bd(s.st)}</span>`}
            <button type="button" class="btn ic" style="width:26px;height:26px;padding:0;" onclick="event.stopPropagation();openDrawer(${jq(s.id)})" title="Buka detail Port ${s.slot_index}">
              <svg class="i" style="width:13px;height:13px;" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
            </button>
          </div>
        </div>

        <!-- Sisi Kanan: Koleksi OTP Nomor Ini (Badge Apps & Scrollable Vertikal) -->
        <div class="slot-right-col">
          ${appBadgesHtml}
          ${otpsListHtml}
        </div>
      </div>
    `;
  };

  const shown = visiblePools();
  if(shown.length < 2){
    rowsEl.classList.remove('multi-pool');
    rowsEl.innerHTML = filteredSlots.map(rowHtml).join('');
    return;
  }
  rowsEl.classList.add('multi-pool');
  rowsEl.innerHTML = shown.map(pool => {
    const part = filteredSlots.filter(x => x.pool_id === pool.id);
    return `<div class="unified-pool-group">
      <div class="unified-pool-title"><span class="agent-led ${pool.agent_online ? 'on' : 'off'}"></span><b>${esc(pool.name)}</b><span class="mu">${part.length} port</span></div>
      ${part.length ? part.map(rowHtml).join('') : '<div class="mu" style="padding:14px;font-size:12px;">Tidak ada yang cocok.</div>'}
    </div>`;
  }).join('');
}

function onFastCardClick(id, rawNum){
  if(!rawNum || rawNum === '—' || rawNum === 'BELUM DIKETAHUI'){
    const s = slots.find(x => x.id === id);
    if(s && s.st === 'empty'){
      toast('Slot kosong.', 0);
    } else {
      toast('Nomor pada slot ini belum terdeteksi. Buka detail untuk dial CNUM.', 0);
    }
    return;
  }
  const clean = rawNum.replace(/[^\d+]/g, '');
  navigator.clipboard.writeText(clean);

  const card = $(`#slot-left-${domId(id)}`);
  if(card){
    card.classList.add('copied');
    const badge = $('.copy-badge', card);
    if(badge) badge.innerHTML = '✓ Tersalin!';
    setTimeout(()=>{
      card.classList.remove('copied');
      if(badge) badge.innerHTML = `<svg class="i" style="width:13px;height:13px;" viewBox="0 0 24 24"><path d="M8 4v12a2 2 0 002 2h8a2 2 0 002-2V7.242a2 2 0 00-.602-1.43L16.083 2.57A2 2 0 0014.685 2H10a2 2 0 00-2 2z"/><path d="M16 18v2a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h2"/></svg> Salin`;
    }, 1200);
  }
  toast(`Nomor ${clean} tersalin ke clipboard.`);
}

function updateFastOtpFeed(){
  const feedEl = $('#otp-stream-feed');
  if(!feedEl) return;

  const otpList = sms.filter(m => m.otp || m.type === 'OTP');
  const countEl = $('#otp-total-count');
  if(countEl) countEl.textContent = `(${otpList.length} kode verifikasi)`;

  if(!otpList.length){
    feedEl.innerHTML = `
      <div style="background:var(--p2);border:1px solid var(--ln);border-radius:8px;padding:36px 24px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:10px;">
        <div style="width:48px;height:48px;border-radius:50%;background:rgba(16,185,129,0.1);display:grid;place-items:center;color:var(--ac);animation:appPulse 2.5s infinite;">
          <svg class="i" style="width:24px;height:24px;" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        </div>
        <b style="font-size:15px;">Menunggu SMS Kode Masuk</b>
        <p class="mu" style="max-width:380px;font-size:12.5px;line-height:1.5;margin:0;">
          Aliran pesan akan bergulir otomatis di sini begitu kode OTP diterima di port mana pun, lengkap dengan logo aplikasi yang bergerak.
        </p>
      </div>
    `;
    return;
  }

  // Cek apakah ada OTP baru untuk sound chime
  const hasNewOtp = otpList.some(m => !lastSeenOtpIds.has(m.id));
  if(hasNewOtp && lastSeenOtpIds.size > 0 && soundEnabled){
    playOtpChime();
  }
  otpList.forEach(m => lastSeenOtpIds.add(m.id));

  feedEl.innerHTML = otpList.map((m, idx) => {
    const app = detectAppFromSMS(m.body, m.n);
    const s = slots.find(x => x.id === m.slot);
    const targetNum = s && s.num && s.num !== '—' ? s.num : 'Port ' + (s ? slotLabel(s) : m.slot_index);
    const isRecent = idx === 0 && m.m <= 2;

    return `
      <div class="otp-card ${isRecent ? 'is-new' : ''}" id="otp-card-${m.id}">
        <!-- Baris Atas: Info Layanan + Kode OTP Menonjol + Nomor Penerima -->
        <div class="otp-card-top-row">
          <div style="display:flex;align-items:center;gap:10px;min-width:0;">
            <div class="app-logo-wrap" style="background:${app.bg};border:1px solid ${app.color}40;">
              ${renderAnimatedAppLogo(app.key, app.color)}
            </div>
            <div style="min-width:0;">
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <b style="font-size:13.5px;color:var(--tx);">${esc(app.name)}</b>
                <span class="mu" style="font-size:11px;">• ${ago(m.m)}</span>
              </div>
              <div class="mu" style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                ${esc(m.n)}
              </div>
            </div>
          </div>

          <!-- Highlight OTP Code Display -->
          <div class="otp-box-display">
            <span class="otp-big-num">${esc(m.otp)}</span>
            <button type="button" class="btn pri otp-copy-btn" id="btn-otp-${m.id}" onclick="copyFastOTP(${jq(m.otp)}, 'btn-otp-${m.id}')" title="Salin kode ${m.otp}">
              <svg class="i" style="width:12px;height:12px;" viewBox="0 0 24 24"><path d="M8 4v12a2 2 0 002 2h8a2 2 0 002-2V7.242a2 2 0 00-.602-1.43L16.083 2.57A2 2 0 0014.685 2H10a2 2 0 00-2 2z"/><path d="M16 18v2a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h2"/></svg>
              Salin
            </button>
          </div>

          <div style="text-align:right;flex:none;">
            <div style="font-weight:600;color:var(--tx);display:flex;align-items:center;gap:6px;justify-content:flex-end;">
              <span class="fnum-port">P${String(m.slot_index).padStart(2,'0')}</span>${poolsList.length > 1 ? `<span class="pool-chip" title="${esc(m.pool_name)}">${esc(m.pool_name)}</span>` : ''}
              <span class="mono" style="font-size:13px;font-weight:700;">${esc(targetNum)}</span>
            </div>
          </div>
        </div>

        <!-- Baris Bawah: Cuplikan SMS Utuh dengan Highlight OTP -->
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--bg);border:1px solid var(--ln);border-radius:6px;padding:6px 12px;">
          <div class="mono" style="font-size:11.5px;line-height:1.4;color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;" title="${esc(m.body)}">
            ${highlightOTPInBody(m.body, m.otp)}
          </div>
          <button type="button" class="btn ic" style="flex:none;width:24px;height:24px;padding:0;" onclick="copyFullSMS(${jq(m.body)}, this)" title="Salin seluruh isi SMS">
            <svg class="i" style="width:12px;height:12px;" viewBox="0 0 24 24"><path d="M8 4v12a2 2 0 002 2h8a2 2 0 002-2V7.242a2 2 0 00-.602-1.43L16.083 2.57A2 2 0 0014.685 2H10a2 2 0 00-2 2z"/><path d="M16 18v2a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h2"/></svg>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

function copyFastOTP(otpCode, btnId){
  navigator.clipboard.writeText(otpCode);
  const btn = $('#' + btnId);
  if(btn){
    const orig = btn.innerHTML;
    btn.innerHTML = '✓ Tersalin!';
    setTimeout(()=>{ btn.innerHTML = orig; }, 1200);
  }
  toast(`Kode OTP ${otpCode} tersalin ke clipboard.`);
}

function scrollSlotOtp(slotId, dir){
  const el = $('#otp-scroll-' + domId(slotId));
  if(!el) return;
  el.scrollBy({top: dir * 50, behavior: 'smooth'});
}

function copyFullSMS(text, btn){
  navigator.clipboard.writeText(text);
  if(btn){
    const orig = btn.innerHTML;
    btn.innerHTML = '✓';
    setTimeout(()=>{ btn.innerHTML = orig; }, 1200);
  }
  toast('Seluruh isi pesan SMS disalin.');
}

checkAuthAndInit();
