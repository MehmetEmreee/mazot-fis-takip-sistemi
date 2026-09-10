const FIELDS = ["fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre"];
const FIELD_LABELS = { firma: "Firma", plaka: "Plaka", ad_soyad: "Ad Soyad" };

const state = {
  receipts: [],
  selectedFilename: null,
  filter: "all",
  query: "",
  modified: null,
  transform: { scale: 1, rotation: 0, x: 0, y: 0 },
  dragging: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const form = $("#receiptForm");

async function loadReceipts({ keepSelection = true, announce = false } = {}) {
  setBusy($("#reloadButton"), true);
  try {
    const response = await fetch("/api/receipts", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Sonuçlar yüklenemedi");
    state.receipts = data.receipts || [];
    state.modified = data.modified;

    if (!keepSelection || !state.receipts.some((r) => r._dosya === state.selectedFilename)) {
      const firstPending = state.receipts.find((r) => !r._onaylandi);
      state.selectedFilename = (firstPending || state.receipts[0] || {})._dosya || null;
    }
    renderAll();
    if (announce) toast(`${state.receipts.length} fiş yeniden yüklendi`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy($("#reloadButton"), false);
  }
}

function filteredReceipts() {
  const query = state.query.trim().toLocaleLowerCase("tr-TR");
  return state.receipts.filter((receipt) => {
    if (state.filter === "pending" && receipt._onaylandi) return false;
    if (state.filter === "suspicious" && (!receipt._kontrol_gerekli || receipt._isleniyor)) return false;
    if (!query) return true;
    return [receipt._dosya, receipt.plaka, receipt.fis_no, receipt.ad_soyad]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("tr-TR").includes(query));
  });
}

function renderAll() {
  renderCounts();
  renderList();
  renderReceipt();
}

function renderCounts() {
  const total = state.receipts.length;
  const approved = state.receipts.filter((r) => r._onaylandi).length;
  const suspicious = state.receipts.filter((r) => r._kontrol_gerekli && !r._isleniyor).length;
  const percent = total ? Math.round((approved / total) * 100) : 0;
  $("#allCount").textContent = total;
  $("#pendingCount").textContent = total - approved;
  $("#suspiciousCount").textContent = suspicious;
  $("#progressText").textContent = `${approved} / ${total} kontrol edildi`;
  $("#progressPercent").textContent = `%${percent}`;
  $("#progressBar").style.width = `${percent}%`;
  $("#dataTime").textContent = state.modified
    ? `Son veri: ${new Date(state.modified).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" })}`
    : "sonuclar.json bekleniyor";
}

function renderList() {
  const list = $("#receiptList");
  const receipts = filteredReceipts();
  list.innerHTML = "";
  receipts.forEach((receipt) => {
    const actualIndex = state.receipts.indexOf(receipt) + 1;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `receipt-item ${receipt._dosya === state.selectedFilename ? "active" : ""}`;
    const statusClass = receipt._onaylandi ? "approved" : receipt._tekrar ? "duplicate" : receipt._isleniyor ? "processing" : receipt._kontrol_gerekli ? "suspicious" : "";
    button.innerHTML = `
      <span class="item-index">${actualIndex}</span>
      <span class="item-copy">
        <strong>${escapeHtml(receipt._dosya || "Adsız fiş")}</strong>
        <small>${escapeHtml(receipt.plaka || receipt.fis_no || "Okuma bekleniyor")}</small>
      </span>
      <span class="item-status ${statusClass}" aria-label="Durum"></span>`;
    button.addEventListener("click", () => selectReceipt(receipt._dosya));
    list.appendChild(button);
  });

  if (!receipts.length && state.receipts.length) {
    list.innerHTML = '<div class="empty-state" style="height:180px"><p>Bu filtrede fiş yok.</p></div>';
  }
}

function renderReceipt() {
  const receipt = currentReceipt();
  $("#emptyState").classList.toggle("hidden", Boolean(receipt));
  $("#review").classList.toggle("hidden", !receipt);
  if (!receipt) return;

  $("#imageFilename").textContent = receipt._dosya || "—";
  const image = $("#receiptImage");
  $("#imageError").classList.add("hidden");
  image.classList.remove("hidden");
  image.src = `/api/image?file=${encodeURIComponent(receipt._dosya || "")}`;
  image.alt = `${receipt._dosya || "Fiş"} görseli`;
  resetTransform();

  FIELDS.forEach((field) => {
    form.elements[field].value = receipt[field] ?? "";
    form.elements[field].dataset.original = receipt[field] ?? "";
    form.elements[field].classList.remove("changed");
  });
  renderExcelSuggestions(receipt);

  const badge = $("#statusBadge");
  badge.className = "status-badge";
  if (receipt._onaylandi) {
    badge.textContent = "✓ Onaylandı";
    badge.classList.add("approved");
  } else if (receipt._tekrar) {
    badge.textContent = "Tekrar fiş";
    badge.classList.add("duplicate");
  } else if (receipt._isleniyor) {
    badge.textContent = "OCR işleniyor";
  } else if (receipt._kontrol_gerekli) {
    badge.textContent = "Şüpheli";
    badge.classList.add("suspicious");
  } else {
    badge.textContent = "Bekliyor";
  }

  $("#warningBox").classList.toggle("hidden", !receipt._kontrol_gerekli);
  $("#warningText").textContent = receipt._kontrol_nedeni || "Otomatik kontrol bu kaydı işaretledi.";
  $("#durationMeta").textContent = receipt._sure_sn != null ? `OCR süresi: ${receipt._sure_sn} sn` : "";
  $("#changeMeta").textContent = receipt._duzeltildi ? "Elle düzeltildi" : receipt._onaylandi ? "Değişiklik yapılmadı" : "";
  $("#approveButton").innerHTML = receipt._onaylandi ? "<span>✓</span> Onayı güncelle" : "<span>✓</span> Doğru, onayla";
}

function renderExcelSuggestions(receipt) {
  const box = $("#excelSuggestions");
  const list = $("#suggestionList");
  const data = receipt._excel_onerileri;
  const suggestions = data?.oneriler || [];
  box.classList.toggle("hidden", !suggestions.length);
  list.innerHTML = "";
  if (!suggestions.length) return;

  $("#suggestionSource").textContent = `${data.kaynak || "Excel"} · ${data.sayfa || ""}`;
  suggestions.forEach((suggestion) => {
    const row = document.createElement("div");
    row.className = "suggestion-item";
    const copy = document.createElement("div");
    copy.className = "suggestion-copy";
    copy.innerHTML = `
      <b>${escapeHtml(FIELD_LABELS[suggestion.alan] || suggestion.alan)} · %${escapeHtml(suggestion.benzerlik)}</b>
      <span>${escapeHtml(suggestion.mevcut)} → <em>${escapeHtml(suggestion.onerilen)}</em></span>`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-apply";
    button.textContent = "Uygula";
    button.addEventListener("click", () => {
      const input = form.elements[suggestion.alan];
      if (!input) return;
      input.value = suggestion.onerilen;
      input.classList.toggle("changed", input.value !== input.dataset.original);
      row.remove();
      if (!list.children.length) box.classList.add("hidden");
      input.focus();
      toast(`${FIELD_LABELS[suggestion.alan] || suggestion.alan} önerisi uygulandı`);
    });
    row.append(copy, button);
    list.appendChild(row);
  });
}

function applyAllSuggestions() {
  const suggestions = currentReceipt()?._excel_onerileri?.oneriler || [];
  let applied = 0;
  suggestions.forEach((suggestion) => {
    const input = form.elements[suggestion.alan];
    if (!input) return;
    input.value = suggestion.onerilen;
    input.classList.toggle("changed", input.value !== input.dataset.original);
    applied += 1;
  });
  if (!applied) return;
  $("#excelSuggestions").classList.add("hidden");
  toast(`${applied} geçmiş önerisi uygulandı`);
}

function currentReceipt() {
  return state.receipts.find((r) => r._dosya === state.selectedFilename) || null;
}

function selectReceipt(filename) {
  state.selectedFilename = filename;
  renderList();
  renderReceipt();
}

function formValues() {
  return Object.fromEntries(FIELDS.map((field) => [field, form.elements[field].value.trim()]));
}

async function saveCurrent(approved, goNext = false) {
  const receipt = currentReceipt();
  if (!receipt) return;
  const button = approved ? $("#approveButton") : $("#saveButton");
  setBusy(button, true);
  try {
    const response = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: receipt._dosya, fields: formValues(), approved }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Kayıt başarısız");
    const index = state.receipts.findIndex((r) => r._dosya === receipt._dosya);
    state.receipts[index] = data.receipt;
    const learned = Number(data.ogrenilen || 0);
    const message = approved ? "Fiş onaylandı" : "Değişiklikler kaydedildi";
    toast(learned ? `${message} · ${learned} düzeltme öğrenildi` : message);
    if (goNext) selectNextPending(index);
    else renderAll();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

async function deleteCurrent() {
  const receipt = currentReceipt();
  if (!receipt) return;
  if (!confirm(`"${receipt._dosya}" silinsin mi? (silinenler/ klasörüne taşınır, kalıcı silinmez)`)) return;

  const button = $("#deleteButton");
  setBusy(button, true);
  try {
    const response = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: receipt._dosya }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Silme başarısız");
    const index = state.receipts.findIndex((r) => r._dosya === receipt._dosya);
    state.receipts.splice(index, 1);
    toast("Fiş silindi");
    state.selectedFilename = (state.receipts[index] || state.receipts[index - 1] || {})._dosya || null;
    renderAll();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function excelError(message) {
  const box = $("#excelError");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

async function openExcelModal() {
  $("#excelModal").classList.remove("hidden");
  excelError("");
  $("#excelPreview").classList.add("hidden");
  try {
    const response = await fetch("/api/excel/files", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Dosyalar listelenemedi");

    const select = $("#excelFile");
    select.innerHTML = '<option value="">— dosya seç —</option>';
    (data.dosyalar || []).forEach((file) => {
      const option = document.createElement("option");
      option.value = file.yol;
      option.textContent = `${file.ad}  (${file.klasor})`;
      select.appendChild(option);
    });

    const previous = (data.son_ayar || {}).dosya;
    const learning = data.ogrenme || {};
    $("#learningStatus").textContent = learning.hazir
      ? `Etkin referans: ${learning.kaynak_sayisi || 1} sayfa · ${learning.satir_sayisi} satır · ${learning.kullanici_duzeltme_sayisi || 0} kullanıcı düzeltmesi`
      : "Henüz bir öneri referansı oluşturulmadı";
    if (previous && (data.dosyalar || []).some((f) => f.yol === previous)) {
      select.value = previous;
      await loadExcelSheets((data.son_ayar || {}).sayfa);
    }
  } catch (error) {
    excelError(error.message);
  }
}

async function learnFromExcel() {
  const dosya = $("#excelFile").value;
  const sayfa = $("#excelSheet").value;
  if (!dosya || !sayfa) {
    excelError("Önce Excel dosyası ve sayfa seçilmeli.");
    return;
  }
  const button = $("#excelLearn");
  setBusy(button, true);
  excelError("");
  try {
    const response = await fetch("/api/excel/learn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dosya, sayfa }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Excel öğrenilemedi");
    const learning = data.durum || {};
    $("#learningStatus").textContent =
      `Etkin referans: ${learning.kaynak_sayisi || 1} sayfa · ${learning.satir_sayisi} satır · ${learning.kullanici_duzeltme_sayisi || 0} kullanıcı düzeltmesi`;
    await loadReceipts();
    toast(data.mesaj || "Excel geçmişi öğrenildi");
  } catch (error) {
    excelError(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function loadExcelSheets(preferredSheet) {
  const dosya = $("#excelFile").value;
  const sheetSelect = $("#excelSheet");
  sheetSelect.innerHTML = "";
  $("#excelPreview").classList.add("hidden");
  if (!dosya) return;

  try {
    const response = await fetch(`/api/excel/sheets?dosya=${encodeURIComponent(dosya)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Sayfalar okunamadı");

    (data.sayfalar || []).forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      sheetSelect.appendChild(option);
    });
    if (preferredSheet && (data.sayfalar || []).includes(preferredSheet)) {
      sheetSelect.value = preferredSheet;
    }
    await loadExcelPreview();
  } catch (error) {
    excelError(error.message);
  }
}

async function loadExcelPreview() {
  const dosya = $("#excelFile").value;
  const sayfa = $("#excelSheet").value;
  if (!dosya || !sayfa) return;
  excelError("");
  try {
    const response = await fetch(
      `/api/excel/preview?dosya=${encodeURIComponent(dosya)}&sayfa=${encodeURIComponent(sayfa)}`
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Sayfa okunamadı");

    $("#previewLastRow").textContent = data.son_veri_satiri;
    $("#previewPending").textContent = data.bekleyen;
    $("#excelStartRow").value = data.onerilen_baslangic;

    const columns = $("#previewColumns");
    columns.innerHTML = "";
    Object.entries(data.sutun_eslestirmesi || {}).forEach(([field, label]) => {
      const row = document.createElement("div");
      row.innerHTML = `<code>${escapeHtml(label)}</code> ← ${escapeHtml(field)}`;
      columns.appendChild(row);
    });
    const headerRow = document.createElement("div");
    headerRow.innerHTML = `<code>Sayfa başlıkları:</code> ${escapeHtml(
      (data.basliklar || []).filter(Boolean).join(" · ")
    )}`;
    columns.appendChild(headerRow);

    $("#excelPreview").classList.remove("hidden");
  } catch (error) {
    excelError(error.message);
  }
}

async function confirmExcelExport() {
  const dosya = $("#excelFile").value;
  const sayfa = $("#excelSheet").value;
  const baslangic = parseInt($("#excelStartRow").value, 10);
  if (!dosya || !sayfa) {
    excelError("Önce dosya ve sayfa seç.");
    return;
  }

  const button = $("#excelConfirm");
  setBusy(button, true);
  excelError("");
  try {
    const response = await fetch("/api/export-excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dosya, sayfa, baslangic: baslangic || null }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Aktarım başarısız");

    if (data.aktarilan > 0) {
      toast(`${data.aktarilan} fiş eklendi (satır ${data.ilk_satir}-${data.son_satir})`);
      $("#excelModal").classList.add("hidden");
      loadReceipts();
    } else {
      excelError("Aktarılacak yeni onaylı fiş yok. Önce fişleri onayla.");
    }
  } catch (error) {
    excelError(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function uploadExcelFile(file) {
  const button = $("#excelUploadButton");
  setBusy(button, true);
  excelError("");
  try {
    const response = await fetch("/api/excel/upload", {
      method: "POST",
      headers: { "X-Dosya-Adi": encodeURIComponent(file.name) },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Yükleme başarısız");
    await openExcelModal();
    $("#excelFile").value = data.yol;
    await loadExcelSheets();
    toast(`${data.ad} yüklendi`);
  } catch (error) {
    excelError(error.message);
  } finally {
    setBusy(button, false);
  }
}

function selectNextPending(afterIndex) {
  const total = state.receipts.length;
  for (let step = 1; step <= total; step += 1) {
    const candidate = state.receipts[(afterIndex + step) % total];
    if (!candidate._onaylandi) {
      state.selectedFilename = candidate._dosya;
      renderAll();
      return;
    }
  }
  renderAll();
  toast("Tüm fişler kontrol edildi");
}

function resetTransform() {
  state.transform = { scale: 1, rotation: 0, x: 0, y: 0 };
  applyTransform();
}

function applyTransform() {
  const { scale, rotation, x, y } = state.transform;
  $("#receiptImage").style.transform = `translate(${x}px, ${y}px) rotate(${rotation}deg) scale(${scale})`;
}

function zoom(amount) {
  state.transform.scale = Math.min(4, Math.max(0.5, state.transform.scale + amount));
  applyTransform();
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2400);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

$("#reloadButton").addEventListener("click", () => loadReceipts({ announce: true }));
$("#searchInput").addEventListener("input", (event) => { state.query = event.target.value; renderList(); });
$$('.filter').forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  $$(".filter").forEach((item) => item.classList.toggle("active", item === button));
  renderList();
}));

form.addEventListener("submit", (event) => { event.preventDefault(); saveCurrent(false); });
form.addEventListener("input", (event) => {
  if (event.target.name) event.target.classList.toggle("changed", event.target.value !== event.target.dataset.original);
});
form.elements.firma.addEventListener("blur", (event) => {
  if (!event.target.value.trim()) {
    event.target.value = "Selim İnşaat";
    event.target.classList.toggle("changed", event.target.value !== event.target.dataset.original);
  }
});
form.elements.km.addEventListener("blur", (event) => {
  if (/^\d$/.test(event.target.value.trim())) {
    event.target.value = "-";
    event.target.classList.toggle("changed", event.target.value !== event.target.dataset.original);
  }
});
$("#approveButton").addEventListener("click", () => saveCurrent(true, true));
$("#deleteButton").addEventListener("click", deleteCurrent);
$("#exportButton").addEventListener("click", openExcelModal);
$("#excelFile").addEventListener("change", () => loadExcelSheets());
$("#excelSheet").addEventListener("change", loadExcelPreview);
$("#excelConfirm").addEventListener("click", confirmExcelExport);
$("#excelLearn").addEventListener("click", learnFromExcel);
$("#applyAllSuggestions").addEventListener("click", applyAllSuggestions);
$("#excelClose").addEventListener("click", () => $("#excelModal").classList.add("hidden"));
$("#excelCancel").addEventListener("click", () => $("#excelModal").classList.add("hidden"));
$("#excelModal").addEventListener("click", (event) => {
  if (event.target === $("#excelModal")) $("#excelModal").classList.add("hidden");
});
$("#excelUploadButton").addEventListener("click", () => $("#excelUpload").click());
$("#excelUpload").addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (file) uploadExcelFile(file);
});
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); saveCurrent(true, true); }
});

$("#receiptImage").addEventListener("error", () => {
  $("#receiptImage").classList.add("hidden");
  $("#imageError").classList.remove("hidden");
});
$("#zoomIn").addEventListener("click", () => zoom(0.2));
$("#zoomOut").addEventListener("click", () => zoom(-0.2));
$("#zoomReset").addEventListener("click", resetTransform);
$("#rotateLeft").addEventListener("click", () => { state.transform.rotation -= 90; applyTransform(); });
$("#rotateRight").addEventListener("click", () => { state.transform.rotation += 90; applyTransform(); });
$("#imageStage").addEventListener("wheel", (event) => { event.preventDefault(); zoom(event.deltaY < 0 ? 0.15 : -0.15); }, { passive: false });
$("#imageStage").addEventListener("pointerdown", (event) => {
  state.dragging = { startX: event.clientX, startY: event.clientY, x: state.transform.x, y: state.transform.y };
  $("#imageStage").classList.add("dragging");
  $("#imageStage").setPointerCapture(event.pointerId);
});
$("#imageStage").addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  state.transform.x = state.dragging.x + event.clientX - state.dragging.startX;
  state.transform.y = state.dragging.y + event.clientY - state.dragging.startY;
  applyTransform();
});
$("#imageStage").addEventListener("pointerup", () => { state.dragging = null; $("#imageStage").classList.remove("dragging"); });

loadReceipts({ keepSelection: false });
