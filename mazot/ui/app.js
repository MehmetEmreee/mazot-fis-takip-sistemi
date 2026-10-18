const FIELDS = ["fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre"];
const FIELD_LABELS = { firma: "Firma", plaka: "Plaka", ad_soyad: "Ad Soyad" };

const state = {
  receipts: [],
  parties: [],
  partyId: "aktif",
  readOnly: false,
  selectedFilename: null,
  filter: "all",
  query: "",
  modified: null,
  transform: { scale: 1, rotation: 0, x: 0, y: 0 },
  dragging: null,
};

// Oturum düştüyse (401) giriş sayfasına dön.
const rawFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const response = await rawFetch(input, init);
  if (response.status === 401 && !String(input).startsWith("/api/giris")) {
    location.href = "/giris";
  }
  return response;
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const form = $("#receiptForm");

async function loadParties({ keepSelection = true } = {}) {
  const response = await fetch("/api/partiler", { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Partiler yüklenemedi");
  state.parties = data.partiler || [];
  if (!keepSelection || !state.parties.some((party) => party.id === state.partyId)) {
    state.partyId = "aktif";
  }
  const select = $("#partySelect");
  select.innerHTML = "";
  state.parties.forEach((party) => {
    const option = document.createElement("option");
    option.value = party.id;
    option.textContent = `${party.aktif ? "● " : ""}${party.ad} (${party.toplam})`;
    select.appendChild(option);
  });
  select.value = state.partyId;
  renderPartyStatus();
}

function selectedParty() {
  return state.parties.find((party) => party.id === state.partyId) || null;
}

function renderPartyStatus() {
  const party = selectedParty();
  if (!party) return;
  $(".party-panel").classList.toggle("archive", !party.aktif);
  $("#partyStatus").textContent = party.aktif
    ? `${party.onayli}/${party.toplam} onaylı · yeni yüklemeler bu partiye gelir`
    : `Arşiv · ${party.onayli}/${party.toplam} onaylı · Excel ${party.excel_aktarilan}/${party.toplam}`;
}

async function loadReceipts({ keepSelection = true, announce = false } = {}) {
  setBusy($("#reloadButton"), true);
  try {
    const response = await fetch(`/api/receipts?parti=${encodeURIComponent(state.partyId)}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Sonuçlar yüklenemedi");
    state.receipts = data.receipts || [];
    state.modified = data.modified;
    state.readOnly = Boolean(data.salt_okunur);

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
    if (state.filter === "error" && !receipt.HATA) return false;
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
  renderPartyStatus();
  $("#review").classList.toggle("readonly-party", state.readOnly);
  FIELDS.forEach((field) => { form.elements[field].disabled = state.readOnly; });
  $("#saveButton").classList.toggle("hidden", state.readOnly);
  $("#approveButton").classList.toggle("hidden", state.readOnly);
  $("#deleteButton").classList.toggle("hidden", state.readOnly);
  $("#exportButton").disabled = false;
  const captureLink = document.querySelector('a[href^="/cek"]');
  if (captureLink) captureLink.classList.toggle("hidden", state.readOnly);
  $("#bulkButton").classList.toggle("hidden", state.readOnly);
}

function renderCounts() {
  const total = state.receipts.length;
  const approved = state.receipts.filter((r) => r._onaylandi).length;
  const suspicious = state.receipts.filter((r) => r._kontrol_gerekli && !r._isleniyor).length;
  const percent = total ? Math.round((approved / total) * 100) : 0;
  $("#allCount").textContent = total;
  $("#pendingCount").textContent = total - approved;
  $("#suspiciousCount").textContent = suspicious;
  $("#errorCount").textContent = state.receipts.filter((r) => r.HATA).length;
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
    const statusClass = receipt.HATA && !receipt._isleniyor ? "error" : receipt._onaylandi ? "approved" : receipt._tekrar ? "duplicate" : receipt._isleniyor ? "processing" : receipt._kontrol_gerekli ? "suspicious" : "";
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
  image.src = `/api/image?parti=${encodeURIComponent(state.partyId)}&file=${encodeURIComponent(receipt._dosya || "")}`;
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

  const failed = Boolean(receipt.HATA) && !receipt._isleniyor;
  $("#warningBox").classList.toggle("hidden", !receipt._kontrol_gerekli && !failed);
  $("#warningBox").classList.toggle("error", failed);
  $("#warningTitle").textContent = failed ? "İşlenemedi" : "Kontrol öneriliyor";
  $("#warningText").textContent = failed
    ? `${receipt.HATA}${receipt._deneme_sayisi ? ` (deneme: ${receipt._deneme_sayisi + 1})` : ""}`
    : receipt._kontrol_nedeni || "Otomatik kontrol bu kaydı işaretledi.";
  $("#retryButton").classList.toggle("hidden", !failed || state.readOnly);
  $("#auditMeta").textContent = auditText(receipt);
  $("#durationMeta").textContent = receipt._sure_sn != null ? `OCR süresi: ${receipt._sure_sn} sn` : "";
  $("#changeMeta").textContent = receipt._duzeltildi ? "Elle düzeltildi" : receipt._onaylandi ? "Değişiklik yapılmadı" : "";
  $("#approveButton").innerHTML = receipt._onaylandi ? "<span>✓</span> Onayı güncelle" : "<span>✓</span> Doğru, onayla";
}

function shortTime(value) {
  return value ? new Date(value).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "";
}

function auditText(receipt) {
  const parts = [];
  if (receipt._yukleyen) parts.push(`Yükleyen: ${receipt._yukleyen}`);
  if (receipt._son_duzenleyen && !receipt._onaylandi) parts.push(`Düzenleyen: ${receipt._son_duzenleyen}`);
  if (receipt._onaylandi && receipt._onaylayan) parts.push(`Onaylayan: ${receipt._onaylayan} ${shortTime(receipt._onay_zamani)}`);
  if (receipt._excel_aktarildi) {
    parts.push(`Excel: ${receipt._excel_aktaran || "?"} ${shortTime(receipt._excel_aktarim_zamani)} · satır ${receipt._excel_satir}`);
  }
  return parts.join(" · ");
}

async function retryCurrent() {
  const receipt = currentReceipt();
  if (!receipt) return;
  const button = $("#retryButton");
  setBusy(button, true);
  try {
    const response = await fetch("/api/retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: receipt._dosya }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Tekrar deneme başlatılamadı");
    toast("Fiş yeniden OCR kuyruğuna alındı");
    await loadReceipts();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

async function loadSession() {
  try {
    const response = await fetch("/api/oturum", { cache: "no-store" });
    const data = await response.json();
    const who = data.kim;
    if (!who) return;
    $("#userChip").textContent = `${who.ad} (${who.rol})`;
    $("#userLine").classList.toggle("hidden", !data.kullanici_modu);
    $("#brandSub").classList.toggle("hidden", Boolean(data.kullanici_modu));
    const captureLink = document.querySelector('a[href^="/cek"]');
    if (captureLink && data.telefon_baglantisi) captureLink.href = data.telefon_baglantisi;
  } catch (_) { /* oturum bilgisi olmadan da çalışır */ }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/durum", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const messages = [];
    if (data.ocr_hatali?.length) {
      messages.push(`${data.ocr_hatali.length} fiş işlenemedi <button type="button" data-action="errors">göster</button>`);
    }
    if (data.excel_yarim?.length) {
      messages.push(`${data.excel_yarim.length} fişin Excel aktarımı yarım kaldı: aynı aktarımı tekrar başlatın, fişler ikinci kez yazılmaz.`);
    }
    if (data.gelen_hatali) {
      messages.push(`Gelen kutusunda aktarılamayan ${data.gelen_hatali} dosya var (${escapeHtml(data.gelen_klasoru)}).`);
    }
    if (data.parti_uyarisi) {
      messages.push(`${escapeHtml(data.parti_uyarisi.mesaj)}: ${data.parti_uyarisi.cakisan.map(escapeHtml).join(", ")}`);
    }
    const banner = $("#statusBanner");
    banner.innerHTML = messages.join("<br>");
    banner.classList.toggle("hidden", !messages.length);
  } catch (_) { /* sonraki yoklamada tekrar denenir */ }
}

$("#statusBanner").addEventListener("click", (event) => {
  if (event.target.dataset.action !== "errors") return;
  state.filter = "error";
  $$(".filter").forEach((button) => button.classList.toggle("active", button.dataset.filter === "error"));
  renderAll();
});
$("#retryButton").addEventListener("click", retryCurrent);
$("#logoutButton").addEventListener("click", async () => {
  await fetch("/api/cikis", { method: "POST" });
  location.href = "/giris";
});

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
    const response = await fetch(`/api/excel/files?parti=${encodeURIComponent(state.partyId)}`, { cache: "no-store" });
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
      `/api/excel/preview?parti=${encodeURIComponent(state.partyId)}&dosya=${encodeURIComponent(dosya)}&sayfa=${encodeURIComponent(sayfa)}&tumu=${$("#excelAll").checked ? 1 : 0}`
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
      body: JSON.stringify({
        parti: state.partyId, dosya, sayfa,
        baslangic: baslangic || null, tumu: $("#excelAll").checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Aktarım başarısız");

    if (data.aktarilan > 0) {
      toast(`${data.aktarilan} fiş eklendi (satır ${data.ilk_satir}-${data.son_satir})`);
      $("#excelModal").classList.add("hidden");
      loadReceipts();
    } else if (data.tamamlanan > 0) {
      toast(`Yarım kalan aktarım tamamlandı: ${data.tamamlanan} fiş (Excel'e tekrar yazılmadı)`);
      $("#excelModal").classList.add("hidden");
      loadReceipts();
    } else {
      excelError(
        $("#excelAll").checked
          ? "Bu partide aktarılacak onaylı fiş yok."
          : "Aktarılacak yeni onaylı fiş yok. Bu fişler daha önce aktarıldıysa "
            + "“Daha önce aktarılanları da ekle” kutusunu işaretle."
      );
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

function partyError(message) {
  const box = $("#partyError");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

async function openPartyModal() {
  const active = state.parties.find((party) => party.aktif);
  if (!active) return;
  partyError("");
  $("#partySummary").innerHTML = `
    <div><b>${active.toplam}</b><span>TOPLAM FİŞ</span></div>
    <div><b>${active.onayli}</b><span>ONAYLI</span></div>
    <div><b>${active.bekleyen}</b><span>BEKLEYEN</span></div>`;
  const now = new Date();
  $("#newPartyName").value = `${now.toLocaleDateString("tr-TR", { day: "numeric", month: "long" })} Gelen Fişler`;
  $("#partyModal").classList.remove("hidden");
  $("#newPartyName").focus();
}

function closePartyModal() {
  $("#partyModal").classList.add("hidden");
  partyError("");
}

async function createNewParty() {
  const ad = $("#newPartyName").value.trim();
  if (!ad) {
    partyError("Yeni parti için bir ad yaz.");
    return;
  }
  const button = $("#partyConfirm");
  setBusy(button, true);
  partyError("");
  try {
    const response = await fetch("/api/partiler/yeni", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ad }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Yeni parti açılamadı");
    state.partyId = "aktif";
    closePartyModal();
    await loadParties({ keepSelection: false });
    await loadReceipts({ keepSelection: false });
    const count = data.arsivlenen?.toplam || 0;
    toast(count ? `${count} fiş arşivlendi · Excel'e dokunulmadı` : "Yeni boş parti açıldı");
  } catch (error) {
    partyError(error.message);
  } finally {
    setBusy(button, false);
  }
}

// ---- Toplu ekleme (bilgisayardan çok sayıda fotoğraf) ----
const BULK_IMAGE_NAME = /\.(jpe?g|png|webp|bmp|heic|heif)$/i;
const BULK_IMAGE_TYPE = /^image\/(jpeg|pjpeg|png|webp|bmp|x-ms-bmp|heic|heif)$/i;
const BULK_CONCURRENCY = 4;
const BULK_RETRIES = 2;
const bulk = {
  queue: [], running: 0, total: 0,
  ok: 0, dup: 0, err: 0, skipped: 0, errors: [], inboxTimer: null, inboxLast: null,
};

function bulkError(message) {
  const box = $("#bulkError");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

function isBulkImage(file) {
  return BULK_IMAGE_NAME.test(file.name) || BULK_IMAGE_TYPE.test(file.type || "");
}

function readEntryFile(entry) {
  return new Promise((resolve) => entry.file(resolve, () => resolve(null)));
}

function readDirectoryBatch(reader) {
  return new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
}

async function walkEntry(entry, out) {
  if (!entry) return;
  if (entry.isFile) {
    const file = await readEntryFile(entry);
    if (file) out.push({ file, path: entry.fullPath || file.name });
    return;
  }
  if (entry.isDirectory) {
    const reader = entry.createReader();
    // readEntries her çağrıda en fazla ~100 öğe döndürür; boş gelene kadar oku.
    for (;;) {
      const batch = await readDirectoryBatch(reader);
      if (!batch.length) break;
      for (const child of batch) await walkEntry(child, out);
    }
  }
}

async function filesFromDrop(dataTransfer) {
  // webkitGetAsEntry ilk await'ten önce çağrılmalı; sonra DataTransfer boşalır.
  const entries = [...(dataTransfer.items || [])]
    .filter((item) => item.kind === "file")
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null));
  if (!entries.length || entries.some((entry) => !entry)) {
    return [...(dataTransfer.files || [])].map((file) => ({ file, path: file.name }));
  }
  const out = [];
  for (const entry of entries) await walkEntry(entry, out);
  return out;
}

function renderBulkProgress() {
  const finished = bulk.ok + bulk.dup + bulk.err;
  const percent = bulk.total ? Math.round((finished / bulk.total) * 100) : 0;
  $("#bulkProgress").classList.toggle("hidden", !bulk.total);
  $("#bulkProgressText").textContent = bulk.running || bulk.queue.length
    ? `${finished} / ${bulk.total} gönderildi…`
    : `${finished} / ${bulk.total} tamamlandı`;
  $("#bulkProgressPercent").textContent = `%${percent}`;
  $("#bulkProgressBar").style.width = `${percent}%`;
  $("#bulkOk").textContent = bulk.ok;
  $("#bulkDup").textContent = bulk.dup;
  $("#bulkErr").textContent = bulk.err;
  const errors = $("#bulkErrors");
  errors.textContent = bulk.errors.slice(-30).join("\n");
  errors.classList.toggle("hidden", !bulk.errors.length);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function uploadBulkItem(item) {
  let lastError = "Bilinmeyen hata";
  for (let attempt = 0; attempt <= BULK_RETRIES; attempt += 1) {
    if (attempt) await sleep(700 * attempt);
    try {
      const response = await fetch("/api/upload", {
        method: "POST",
        headers: {
          // Bilgisayarda sıkıştırma yok: orijinal dosya olduğu gibi gider.
          "Content-Type": item.file.type || "application/octet-stream",
          "X-Orijinal-Ad": encodeURIComponent(item.file.name),
          "X-Kaynak": "toplu",
        },
        body: item.file,
      });
      if (response.status === 202) return "ok";
      if (response.status === 409) return "dup";
      const data = await response.json().catch(() => ({}));
      lastError = data.error || `HTTP ${response.status}`;
      // 4xx kalıcı hatadır (biçim, boyut); tekrar denemek işe yaramaz.
      if (response.status >= 400 && response.status < 500) break;
    } catch (error) {
      lastError = "Sunucuya ulaşılamadı";
    }
  }
  throw new Error(lastError);
}

async function bulkWorker() {
  while (bulk.queue.length) {
    const item = bulk.queue.shift();
    try {
      const result = await uploadBulkItem(item);
      bulk[result] += 1;
    } catch (error) {
      bulk.err += 1;
      bulk.errors.push(`${item.path}: ${error.message}`);
    }
    renderBulkProgress();
  }
}

async function startBulkUpload(items) {
  bulkError("");
  const images = items.filter((item) => isBulkImage(item.file));
  const skipped = items.length - images.length;
  if (skipped) toast(`${skipped} görsel olmayan dosya atlandı`);
  if (!images.length) {
    if (!skipped) bulkError("Eklenecek fotoğraf bulunamadı.");
    return;
  }
  // Sunucudaki OCR sırası tahmin edilebilir olsun diye yola göre sırala.
  images.sort((a, b) => a.path.localeCompare(b.path, "tr", { numeric: true }));

  const fresh = !bulk.running && !bulk.queue.length;
  if (fresh) Object.assign(bulk, { total: 0, ok: 0, dup: 0, err: 0, errors: [] });
  bulk.queue.push(...images);
  bulk.total += images.length;
  renderBulkProgress();
  if (!fresh) return; // çalışan işçiler yeni eklenenleri de alır

  const workers = Array.from({ length: BULK_CONCURRENCY }, async () => {
    bulk.running += 1;
    try { await bulkWorker(); } finally { bulk.running -= 1; }
  });
  await Promise.all(workers);
  renderBulkProgress();
  const parts = [`${bulk.ok} fiş eklendi`];
  if (bulk.dup) parts.push(`${bulk.dup} tekrar atlandı`);
  if (bulk.err) parts.push(`${bulk.err} hata`);
  toast(parts.join(" · "), bulk.err > 0 && !bulk.ok);
  await refreshAfterBulk();
}

async function refreshAfterBulk() {
  try {
    await loadParties();
  } catch (error) {
    // parti listesi yenilenemese de fişler yenilensin
  }
  if (state.partyId === "aktif") await loadReceipts();
}

async function loadInboxStatus() {
  try {
    const response = await fetch("/api/gelen", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Gelen kutusu okunamadı");
    $("#inboxPath").textContent = data.klasor;
    $("#inboxPath").title = data.klasor;
    $("#inboxStatus").textContent =
      `Klasörde bekleyen: ${data.bekleyen} · bugün klasörden eklenen: ${data.son_aktarilan}`;
    if (bulk.inboxLast !== null && data.son_aktarilan > bulk.inboxLast && !bulk.running) {
      refreshAfterBulk();
    }
    bulk.inboxLast = data.son_aktarilan;
  } catch (error) {
    $("#inboxStatus").textContent = error.message;
  }
}

function openBulkModal() {
  if (state.readOnly) return;
  bulkError("");
  renderBulkProgress();
  $("#bulkModal").classList.remove("hidden");
  bulk.inboxLast = null;
  loadInboxStatus();
  clearInterval(bulk.inboxTimer);
  bulk.inboxTimer = setInterval(loadInboxStatus, 4000);
}

function closeBulkModal() {
  $("#bulkModal").classList.add("hidden");
  clearInterval(bulk.inboxTimer);
  bulk.inboxTimer = null;
  if (bulk.running) toast("Yükleme arka planda sürüyor");
}

async function openInboxFolder() {
  const button = $("#inboxOpen");
  setBusy(button, true);
  try {
    const response = await fetch("/api/gelen/ac", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Klasör açılamadı");
  } catch (error) {
    bulkError(error.message);
  } finally {
    setBusy(button, false);
  }
}

function pickedFiles(input) {
  const items = [...(input.files || [])].map((file) => ({
    file, path: file.webkitRelativePath || file.name,
  }));
  input.value = "";
  return items;
}

$("#bulkButton").addEventListener("click", openBulkModal);
$("#bulkClose").addEventListener("click", closeBulkModal);
$("#bulkCancel").addEventListener("click", closeBulkModal);
$("#bulkModal").addEventListener("click", (event) => {
  if (event.target === $("#bulkModal")) closeBulkModal();
});
$("#bulkPickFiles").addEventListener("click", () => $("#bulkFiles").click());
$("#bulkPickFolder").addEventListener("click", () => $("#bulkFolder").click());
$("#bulkFiles").addEventListener("change", (event) => startBulkUpload(pickedFiles(event.target)));
$("#bulkFolder").addEventListener("change", (event) => startBulkUpload(pickedFiles(event.target)));
$("#inboxOpen").addEventListener("click", openInboxFolder);
$("#bulkDrop").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("#bulkFiles").click(); }
});

function isFileDrag(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}
// Sayfanın herhangi bir yerine bırakılan dosyalar da toplu eklemeye gider;
// tarayıcının dosyayı açıp sayfadan çıkmasını engeller.
document.addEventListener("dragover", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = state.readOnly ? "none" : "copy";
  $("#bulkDrop").classList.toggle("dragover", $("#bulkDrop").contains(event.target));
});
document.addEventListener("dragleave", (event) => {
  if (!event.relatedTarget) $("#bulkDrop").classList.remove("dragover");
});
document.addEventListener("drop", async (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  $("#bulkDrop").classList.remove("dragover");
  if (state.readOnly) {
    toast("Arşiv partisine fiş eklenemez; önce aktif partiyi seç", true);
    return;
  }
  const pending = filesFromDrop(event.dataTransfer);
  if ($("#bulkModal").classList.contains("hidden")) openBulkModal();
  $("#bulkDrop").classList.add("busy");
  const items = await pending;
  $("#bulkDrop").classList.remove("busy");
  startBulkUpload(items);
});
window.addEventListener("beforeunload", (event) => {
  if (bulk.running || bulk.queue.length) { event.preventDefault(); event.returnValue = ""; }
});

$("#reloadButton").addEventListener("click", () => loadReceipts({ announce: true }));
$("#partySelect").addEventListener("change", async (event) => {
  state.partyId = event.target.value;
  state.selectedFilename = null;
  await loadReceipts({ keepSelection: false });
});
$("#newPartyButton").addEventListener("click", openPartyModal);
$("#partyClose").addEventListener("click", closePartyModal);
$("#partyCancel").addEventListener("click", closePartyModal);
$("#partyConfirm").addEventListener("click", createNewParty);
$("#partyModal").addEventListener("click", (event) => {
  if (event.target === $("#partyModal")) closePartyModal();
});
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
$("#excelAll").addEventListener("change", loadExcelPreview);
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

async function initialize() {
  loadSession();
  loadStatus();
  setInterval(loadStatus, 8000);
  try {
    await loadParties({ keepSelection: false });
    await loadReceipts({ keepSelection: false });
  } catch (error) {
    toast(error.message, true);
  }
}

initialize();
