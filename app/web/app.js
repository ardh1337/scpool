let currentSlots = [];
let currentSlotIndex = null;
let allSMSList = [];

// Inisialisasi Aplikasi
document.addEventListener("DOMContentLoaded", () => {
  loadAll();
  // Auto refresh data status setiap 5 detik
  setInterval(loadAll, 5000);
});

async function loadAll() {
  await Promise.all([
    fetchStatus(),
    fetchSlots(),
    fetchSMS()
  ]);
}

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById("stat-total").textContent = data.total_slots || 32;
    document.getElementById("stat-ready").textContent = data.ready_count || 0;
    document.getElementById("stat-empty").textContent = data.empty_count || 0;
    document.getElementById("stat-sms").textContent = data.total_sms || 0;
  } catch (err) {
    console.error("Gagal mengambil status:", err);
  }
}

async function fetchSlots() {
  try {
    const res = await fetch("/api/slots");
    if (!res.ok) return;
    const slots = await res.json();
    currentSlots = slots;

    renderSlotsGrid(slots);
  } catch (err) {
    console.error("Gagal mengambil daftar slot:", err);
  }
}

function renderSlotsGrid(slots) {
  const bank1 = document.getElementById("grid-bank-1");
  const bank2 = document.getElementById("grid-bank-2");

  bank1.innerHTML = "";
  bank2.innerHTML = "";

  slots.forEach(slot => {
    const card = document.createElement("div");
    card.className = `slot-card`;
    card.onclick = () => openSlotModal(slot.slot_index);

    const isReady = slot.status === "READY";
    const phoneDisplay = (isReady && slot.phone_number) ? slot.phone_number : (isReady ? "BELUM DIKETAHUI" : "-");
    const phoneClass = (isReady && slot.phone_number) ? "" : "unknown";

    card.innerHTML = `
      <div class="slot-card-header">
        <span class="slot-num">Slot ${slot.slot_index}</span>
        <span class="slot-port">${slot.port_name || "-"}</span>
      </div>
      <span class="status-pill status-${slot.status}">${slot.status === "EMPTY" ? "KOSONG" : slot.status}</span>
      <div class="slot-phone ${phoneClass}">${phoneDisplay}</div>
      <div class="slot-footer">
        <span>${slot.operator_name || (isReady ? "SIM" : "")}</span>
        ${slot.sms_count > 0 ? `<span class="sms-badge">${slot.sms_count} SMS</span>` : ""}
      </div>
    `;

    if (slot.slot_index <= 16) {
      bank1.appendChild(card);
    } else {
      bank2.appendChild(card);
    }
  });
}

async function fetchSMS() {
  try {
    const res = await fetch("/api/sms?limit=30");
    if (!res.ok) return;
    const sms = await res.json();
    allSMSList = sms;
    renderSMSFeed(sms);
  } catch (err) {
    console.error("Gagal mengambil SMS:", err);
  }
}

function renderSMSFeed(list) {
  const container = document.getElementById("sms-feed-list");
  if (!list || list.length === 0) {
    container.innerHTML = `<p class="empty-text">Belum ada pesan SMS yang tersimpan.</p>`;
    return;
  }

  container.innerHTML = list.map(m => {
    const tag = m.status === "PESAN BAWAAN" ? `<span class="sms-status-tag sms-tag-bawaan">PESAN BAWAAN</span>` : "";
    const timeStr = m.received_at ? new Date(m.received_at).toLocaleTimeString("id-ID") : "-";

    return `
      <div class="sms-item">
        <div class="sms-item-top">
          <div>
            <span class="sms-sender">${m.sender}</span>
            ${tag}
          </div>
          <span>Slot ${m.slot_index} (${m.port_name}) &bull; ${timeStr}</span>
        </div>
        <div class="sms-body">${escapeHTML(m.body)}</div>
      </div>
    `;
  }).join("");
}

function filterSMS() {
  const query = document.getElementById("sms-search").value.toLowerCase().trim();
  if (!query) {
    renderSMSFeed(allSMSList);
    return;
  }

  const filtered = allSMSList.filter(m => 
    (m.sender && m.sender.toLowerCase().includes(query)) ||
    (m.body && m.body.toLowerCase().includes(query)) ||
    (`slot ${m.slot_index}`.includes(query))
  );
  renderSMSFeed(filtered);
}

// Modal Slot
async function openSlotModal(slotIndex) {
  currentSlotIndex = slotIndex;
  const slot = currentSlots.find(s => s.slot_index === slotIndex);
  if (!slot) return;

  const isReady = slot.status === "READY";
  document.getElementById("modal-slot-title").textContent = `Detail Slot Fisik ${slot.slot_index}`;
  document.getElementById("modal-slot-port").textContent = slot.port_name || "-";
  document.getElementById("modal-phone").textContent = isReady ? (slot.phone_number || "BELUM DIKETAHUI") : "-";
  document.getElementById("modal-operator").textContent = isReady ? (slot.operator_name || "-") : "-";
  document.getElementById("modal-iccid").textContent = isReady ? (slot.iccid_masked || "-") : "-";

  // Sembunyikan banner ussd lama
  const banner = document.getElementById("ussd-result-banner");
  banner.className = "ussd-banner hidden";
  banner.textContent = "";

  document.getElementById("slot-modal").classList.add("active");

  // Ambil SMS slot ini
  try {
    const res = await fetch(`/api/slots/${slotIndex}/sms`);
    if (res.ok) {
      const messages = await res.json();
      renderSlotSMSModal(messages);
    }
  } catch (err) {
    console.error("Gagal mengambil SMS slot:", err);
  }
}

function renderSlotSMSModal(messages) {
  const container = document.getElementById("modal-sms-list");
  if (!messages || messages.length === 0) {
    container.innerHTML = `<p class="empty-text">Tidak ada pesan untuk slot ini.</p>`;
    return;
  }

  container.innerHTML = messages.map(m => `
    <div class="sms-item">
      <div class="sms-item-top">
        <span class="sms-sender">${m.sender}</span>
        <span>${new Date(m.received_at).toLocaleTimeString("id-ID")}</span>
      </div>
      <div class="sms-body">${escapeHTML(m.body)}</div>
    </div>
  `).join("");
}

function closeModal() {
  document.getElementById("slot-modal").classList.remove("active");
  currentSlotIndex = null;
}

// USSD Trigger
async function runSlotUSSD() {
  if (!currentSlotIndex) return;
  const code = document.getElementById("ussd-input").value.trim() || "*808*7*8#";
  const btn = document.getElementById("btn-run-ussd");
  const banner = document.getElementById("ussd-result-banner");

  btn.disabled = true;
  btn.textContent = "Menghubungi...";
  banner.className = "ussd-banner";
  banner.textContent = "Sedang menjalankan dial USSD ke jaringan seluler...";

  try {
    const res = await fetch(`/api/slots/${currentSlotIndex}/ussd?code=${encodeURIComponent(code)}`, {
      method: "POST"
    });
    const data = await res.json();

    if (data.success) {
      banner.textContent = `Balasan:\n${data.response_text}\n\n${data.phone_number ? "Nomor Terdeteksi: " + data.phone_number : ""}`;
      if (data.phone_number) {
        document.getElementById("modal-phone").textContent = data.phone_number;
      }
      fetchSlots();
    } else {
      banner.textContent = `Gagal: ${data.message || "Tidak ada balasan."}`;
    }
  } catch (err) {
    banner.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Dial USSD";
  }
}

// Tombol Global Action
async function syncHardware() {
  const btn = document.getElementById("btn-sync");
  btn.disabled = true;
  btn.innerHTML = `<span class="icon">⏳</span> Memindai 32 Port...`;

  try {
    const res = await fetch("/api/sync", { method: "POST" });
    if (res.ok) {
      await loadAll();
    }
  } catch (err) {
    console.error("Gagal sinkronisasi:", err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="icon">🔄</span> Sinkronkan Hardware`;
  }
}

async function pollSMS() {
  const btn = document.getElementById("btn-poll-sms");
  btn.disabled = true;
  btn.innerHTML = `<span class="icon">⏳</span> Membaca SMS...`;

  try {
    const res = await fetch("/api/poll-sms", { method: "POST" });
    if (res.ok) {
      await fetchSMS();
      await fetchStatus();
    }
  } catch (err) {
    console.error("Gagal membaca SMS:", err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="icon">📩</span> Periksa SMS Baru`;
  }
}

function escapeHTML(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Modal Pengaturan Operator & USSD
async function openOperatorModal() {
  document.getElementById("operator-modal").classList.add("active");
  const banner = document.getElementById("operator-save-status");
  banner.className = "ussd-banner hidden";
  banner.textContent = "";
  await loadOperatorProfiles();
}

function closeOperatorModal() {
  document.getElementById("operator-modal").classList.remove("active");
}

async function loadOperatorProfiles() {
  const container = document.getElementById("operator-list-container");
  container.innerHTML = `<p class="empty-text">Memuat profil operator...</p>`;

  try {
    const res = await fetch("/api/operators");
    if (!res.ok) throw new Error("Gagal mengambil data");
    const profiles = await res.json();

    if (!profiles || profiles.length === 0) {
      container.innerHTML = `<p class="empty-text">Tidak ada profil operator.</p>`;
      return;
    }

    container.innerHTML = profiles.map(p => `
      <div class="operator-card" id="op-card-${p.id}">
        <div class="operator-card-header">
          <span class="operator-title">${escapeHTML(p.name)}</span>
          <span class="operator-meta font-mono">${escapeHTML(p.iccid_prefixes || "-")}</span>
        </div>
        ${p.description ? `<div class="operator-desc">${escapeHTML(p.description)}</div>` : ""}
        <div class="operator-form-row">
          <input type="text" id="op-code-${p.id}" value="${escapeHTML(p.ussd_code)}" 
            placeholder="Kode USSD (misal: *808#)"
            onkeydown="if(event.key==='Enter') saveOperatorProfile('${p.id}')">
          <button class="btn btn-primary btn-sm" id="btn-save-${p.id}" onclick="saveOperatorProfile('${p.id}')">Simpan</button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="empty-text">Error memuat profil: ${escapeHTML(err.message)}</p>`;
  }
}

async function saveOperatorProfile(operatorId) {
  const input = document.getElementById(`op-code-${operatorId}`);
  const btn = document.getElementById(`btn-save-${operatorId}`);
  const banner = document.getElementById("operator-save-status");

  if (!input) return;
  const newCode = input.value.trim();
  if (!newCode) {
    alert("Kode USSD tidak boleh kosong.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Menyimpan...";

  try {
    const res = await fetch(`/api/operators/${operatorId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ussd_code: newCode })
    });
    const data = await res.json();

    if (res.ok && data.success) {
      banner.className = "ussd-banner";
      banner.style.color = "var(--status-ready)";
      banner.textContent = `Berhasil menyimpan kode dial USSD untuk ${data.name}: ${data.ussd_code}`;
      setTimeout(() => {
        banner.className = "ussd-banner hidden";
      }, 4000);
    } else {
      banner.className = "ussd-banner";
      banner.style.color = "var(--status-error)";
      banner.textContent = `Gagal menyimpan: ${data.detail || "Kesalahan server"}`;
    }
  } catch (err) {
    banner.className = "ussd-banner";
    banner.style.color = "var(--status-error)";
    banner.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Simpan";
  }
}


