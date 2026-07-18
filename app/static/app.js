// CIS画素光学シミュレーター 画面ロジック

const JOB_POLL_INTERVAL_MS = 2000;

// ジョブ履歴の折りたたみ状態を保存するlocalStorageキー
const JOB_HISTORY_COLLAPSED_KEY = "jobHistoryCollapsed";

// 設定タブの選択状態を保存するlocalStorageキー
const SETTINGS_ACTIVE_TAB_KEY = "settingsActiveTab";

const form = document.getElementById("parameter-form");
const previewButton = document.getElementById("preview-button");
const runButton = document.getElementById("run-button");
const batchRunButton = document.getElementById("batch-run-button");
const batchFileInput = document.getElementById("batch-file");
const formMessage = document.getElementById("form-message");
const previewArea = document.getElementById("preview-area");
const resultArea = document.getElementById("result-area");
const jobTableBody = document.getElementById("job-table-body");
const selectAllCheckbox = document.getElementById("select-all-jobs");
const deleteSelectedButton = document.getElementById("delete-selected-button");
const deleteAllButton = document.getElementById("delete-all-button");
const jobHistoryBody = document.getElementById("job-history-body");
const jobHistoryToggle = document.getElementById("job-history-toggle");

let pollTimerId = null;

function numberValue(id) {
  return parseFloat(document.getElementById(id).value);
}

// ---- 設定タブ ----

const settingsTabButtons = [...document.querySelectorAll(".settings-tab")];

function activateSettingsTab(panelId) {
  const exists = settingsTabButtons.some(
    (button) => button.dataset.panel === panelId);
  if (!exists) panelId = settingsTabButtons[0].dataset.panel;
  for (const button of settingsTabButtons) {
    const active = button.dataset.panel === panelId;
    button.classList.toggle("active", active);
    document.getElementById(button.dataset.panel).hidden = !active;
  }
  localStorage.setItem(SETTINGS_ACTIVE_TAB_KEY, panelId);
}

for (const button of settingsTabButtons) {
  button.addEventListener("click",
                          () => activateSettingsTab(button.dataset.panel));
}
activateSettingsTab(localStorage.getItem(SETTINGS_ACTIVE_TAB_KEY)
                    || settingsTabButtons[0].dataset.panel);

function collectSweep() {
  if (!document.getElementById("sweep-enabled").checked) return null;
  const values = document.getElementById("sweep-values").value
    .split(",")
    .map((text) => parseFloat(text.trim()))
    .filter((value) => !Number.isNaN(value));
  return {
    parameter: document.getElementById("sweep-parameter").value,
    values: values,
  };
}

function collectParams() {
  // 通常は2D断面モード。真上ビューを有効にしたときだけ3Dで計算する
  // （2026-07-18 ユーザー指示。観察深さが空欄ならPD面と同じ深さ）
  const topViewEnabled = document.getElementById("topview-enabled").checked;
  const topViewDepth = numberValue("topview-depth");
  return {
    mode: topViewEnabled ? "3d" : "2d",
    view: { depth_um: Number.isNaN(topViewDepth) ? null : topViewDepth },
    crosstalk: document.getElementById("breakdown-enabled").checked,
    sweep: collectSweep(),
    pixel_pitch_um: numberValue("pixel-pitch"),
    ocl: {
      enabled: document.getElementById("ocl-enabled").checked,
      height_um: numberValue("ocl-height"),
      shape: document.getElementById("ocl-shape").value,
      superellipse_exponent: numberValue("ocl-superellipse-exponent"),
      sharing: document.getElementById("ocl-sharing").value,
      offset_um: numberValue("ocl-offset"),
      base_um: numberValue("ocl-base"),
      gap_height_left_um: numberValue("ocl-gap-left"),
      gap_height_right_um: numberValue("ocl-gap-right"),
    },
    materials: {
      ocl_n: numberValue("ocl-n"),
    },
    layers: {
      planarization_um: numberValue("layer-planarization"),
      color_filter_um: numberValue("layer-color-filter"),
      ar_um: numberValue("layer-ar"),
      si_um: numberValue("layer-si"),
    },
    dti: {
      enabled: document.getElementById("dti-enabled").checked,
      width_um: numberValue("dti-width"),
      depth_um: numberValue("dti-depth"),
      placement: document.getElementById("dti-placement").value,
      offset_um: numberValue("dti-offset"),
    },
    source: {
      wavelength_nm: numberValue("wavelength"),
      incident_angle_deg: numberValue("incident-angle"),
    },
    pd: { top_depth_um: numberValue("pd-depth") },
    resolution_pixels_per_um: numberValue("resolution"),
  };
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function showMessage(text, kind) {
  formMessage.textContent = text;
  formMessage.className = `message ${kind}`;
  formMessage.hidden = false;
}

function clearMessage() {
  formMessage.hidden = true;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `サーバーエラー（${response.status}）`);
  }
  return response;
}

// ---- 構造プレビュー ----

previewButton.addEventListener("click", async () => {
  clearMessage();
  previewButton.disabled = true;
  previewArea.innerHTML =
    '<p class="placeholder"><span class="spinner"></span>プレビュー生成中…</p>';
  try {
    const response = await postJson("/api/preview", collectParams());
    const blob = await response.blob();
    const image = document.createElement("img");
    image.src = URL.createObjectURL(blob);
    image.alt = "構造プレビュー（誘電率分布の断面図）";
    previewArea.innerHTML = "";
    previewArea.appendChild(image);
  } catch (error) {
    previewArea.innerHTML =
      '<p class="placeholder">プレビューを生成できませんでした</p>';
    showMessage(error.message, "error");
  } finally {
    previewButton.disabled = false;
  }
});

// ---- 計算実行 ----

// 非表示タブ内の入力エラーで送信が黙って失敗しないよう、
// エラーになった入力があるタブへ自動で切り替える
form.addEventListener("invalid", (event) => {
  const panel = event.target.closest(".settings-panel");
  if (panel && panel.hidden) activateSettingsTab(panel.id);
}, true);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearMessage();
  runButton.disabled = true;
  try {
    const response = await postJson("/api/jobs", collectParams());
    const data = await response.json();
    if (data.warnings && data.warnings.length > 0) {
      showMessage(`注意:\n${data.warnings.join("\n")}`, "warning");
    }
    watchJob(data.job_id);
    refreshJobList();
  } catch (error) {
    showMessage(error.message, "error");
    runButton.disabled = false;
  }
});

// ---- CSV一括計算 ----

async function readCsvFileText(file) {
  // ExcelのCSV（Shift_JIS保存）でも読めるよう、UTF-8で失敗したら読み直す
  const buffer = await file.arrayBuffer();
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch (error) {
    return new TextDecoder("shift_jis").decode(buffer);
  }
}

batchRunButton.addEventListener("click", async () => {
  clearMessage();
  if (batchFileInput.files.length === 0) {
    showMessage("条件CSVファイルを選択してください", "error");
    return;
  }
  batchRunButton.disabled = true;
  runButton.disabled = true;
  try {
    const params = collectParams();
    params.sweep = null; // 一括計算とスイープは同時に使わない
    params.batch_csv = await readCsvFileText(batchFileInput.files[0]);
    const response = await postJson("/api/jobs", params);
    const data = await response.json();
    if (data.warnings && data.warnings.length > 0) {
      showMessage(`注意:\n${data.warnings.join("\n")}`, "warning");
    }
    watchJob(data.job_id);
    refreshJobList();
  } catch (error) {
    showMessage(error.message, "error");
    runButton.disabled = false;
    batchRunButton.disabled = false;
  }
});

function watchJob(jobId) {
  if (pollTimerId !== null) clearInterval(pollTimerId);
  resultArea.innerHTML =
    `<p class="placeholder"><span class="spinner"></span>計算中…（ジョブ ${jobId}）</p>`;

  pollTimerId = setInterval(async () => {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) return;
      const job = await response.json();
      if (job.status === "running") return;

      clearInterval(pollTimerId);
      pollTimerId = null;
      runButton.disabled = false;
      batchRunButton.disabled = false;
      refreshJobList();

      if (job.status === "finished" && job.result) {
        renderResult(jobId, job.result);
      } else {
        resultArea.innerHTML = "";
        showMessage(`計算が失敗しました: ${job.error || "原因不明"}`, "error");
      }
    } catch (error) {
      // 一時的な通信エラーは次のポーリングで再試行する
    }
  }, JOB_POLL_INTERVAL_MS);
}

function renderResult(jobId, result) {
  if (result.type === "sweep") {
    renderSweepResult(jobId, result);
    return;
  }
  if (result.type === "batch") {
    renderBatchResult(jobId, result);
    return;
  }

  const efficiencyPercent =
    (result.collection_efficiency_total * 100).toFixed(1);
  // 受光内訳（中央照射）のときは位置が分かる名前で表示する。
  // 共有レンズでは単位内の画素番号を付ける（例: 中央-1, 中央-2）
  const perPixelValues = result.collection_efficiency_per_pixel;
  const unitPixels = result.unit_pixels || 1;
  const isBreakdown = (result.crosstalk_total !== undefined
                       && perPixelValues.length === 3 * unitPixels);
  const pixelName = (index) => {
    if (!isBreakdown) return `画素${index + 1}`;
    const unitLabel = ["左隣", "中央", "右隣"][Math.floor(index / unitPixels)];
    if (unitPixels === 1) return unitLabel;
    return `${unitLabel}-${(index % unitPixels) + 1}`;
  };
  const perPixel = perPixelValues
    .map((value, index) =>
      `${pixelName(index)}: ${(value * 100).toFixed(1)}%`)
    .join(" / ");

  let metrics = `
    <div class="metric">
      <div class="metric-label">集光効率（合計）</div>
      <div class="metric-value">${efficiencyPercent}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">画素ごとの内訳</div>
      <div class="metric-value" style="font-size:0.95rem">${perPixel}</div>
    </div>
  `;
  if (result.crosstalk_total !== undefined) {
    const centerPercent =
      (result.collection_efficiency_center * 100).toFixed(1);
    const crosstalkPercent = (result.crosstalk_total * 100).toFixed(2);
    metrics += `
      <div class="metric">
        <div class="metric-label">集光効率（中央画素）</div>
        <div class="metric-value">${centerPercent}%</div>
      </div>
      <div class="metric">
        <div class="metric-label">クロストーク（漏れ合計）</div>
        <div class="metric-value">${crosstalkPercent}%</div>
      </div>
    `;
  }
  metrics += `
    <div class="metric">
      <div class="metric-label">計算時間</div>
      <div class="metric-value">${result.elapsed_seconds}秒</div>
    </div>
  `;

  let images =
    `<img src="/api/jobs/${jobId}/image" alt="断面の構造と電場強度分布">`;
  if (result.input && result.input.mode === "3d") {
    images += `
      <img src="/api/jobs/${jobId}/topview" alt="真上ビューの電場強度分布">`;
  }

  resultArea.innerHTML =
    `<div class="result-numbers">${metrics}</div>${images}`;
}

function renderSweepResult(jobId, result) {
  const rows = result.sweep.results.map((entry) => {
    const crosstalkCell = entry.crosstalk_total !== undefined
      ? `<td>${(entry.crosstalk_total * 100).toFixed(2)}%</td>` : "";
    return `<tr><td>${entry.value}</td>` +
      `<td>${(entry.collection_efficiency_total * 100).toFixed(1)}%</td>` +
      crosstalkCell + `</tr>`;
  }).join("");
  const crosstalkHeader = result.sweep.results[0].crosstalk_total !== undefined
    ? "<th>クロストーク</th>" : "";

  resultArea.innerHTML = `
    <div class="result-numbers">
      <div class="metric">
        <div class="metric-label">スイープ対象</div>
        <div class="metric-value" style="font-size:0.95rem">
          ${result.sweep.label}（${result.sweep.values.length}条件）</div>
      </div>
      <div class="metric">
        <div class="metric-label">計算時間</div>
        <div class="metric-value">${result.elapsed_seconds}秒</div>
      </div>
    </div>
    <p><a href="/api/jobs/${jobId}/csv" download>CSVをダウンロード</a></p>
    <img src="/api/jobs/${jobId}/sweep-plot" alt="スイープ結果のグラフ">
    <table>
      <thead><tr><th>${result.sweep.label}</th><th>集光効率</th>
        ${crosstalkHeader}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderBatchResult(jobId, result) {
  const columns = result.batch.columns;
  const entries = result.batch.results;
  const hasCrosstalk =
    entries.some((entry) => entry.crosstalk_total !== undefined);

  const headerCells = ["条件名"].concat(columns)
    .map((name) => `<th>${escapeHtml(name)}</th>`).join("")
    + "<th>集光効率</th>"
    + (hasCrosstalk ? "<th>クロストーク</th>" : "");

  const rows = entries.map((entry) => {
    // 空欄（上書きなし）の列は「—」で表示する（画面の入力値で計算）
    const parameterCells = columns.map((name) => {
      const value = entry.overrides[name];
      return `<td>${value === undefined ? "—" : escapeHtml(value)}</td>`;
    }).join("");
    const crosstalkCell = !hasCrosstalk ? ""
      : entry.crosstalk_total === undefined ? "<td>—</td>"
      : `<td>${(entry.crosstalk_total * 100).toFixed(2)}%</td>`;
    return `<tr><td>${escapeHtml(entry.label)}</td>${parameterCells}` +
      `<td>${(entry.collection_efficiency_total * 100).toFixed(1)}%</td>` +
      crosstalkCell + `</tr>`;
  }).join("");

  resultArea.innerHTML = `
    <div class="result-numbers">
      <div class="metric">
        <div class="metric-label">CSV一括計算</div>
        <div class="metric-value">${entries.length}条件</div>
      </div>
      <div class="metric">
        <div class="metric-label">計算時間</div>
        <div class="metric-value">${result.elapsed_seconds}秒</div>
      </div>
    </div>
    <p><a href="/api/jobs/${jobId}/csv" download>結果CSVをダウンロード</a></p>
    <table>
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ---- ジョブ履歴 ----

function addActionButton(cell, labelText, onClick) {
  const button = document.createElement("button");
  button.className = "link-button";
  button.textContent = labelText;
  button.addEventListener("click", onClick);
  cell.appendChild(button);
}

// ジョブ名のセルを入力欄に切り替え、Enterまたは欄外クリックで保存する
function startRenameJob(nameCell, job) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = job.name;
  input.maxLength = 60;
  input.style.width = "95%";
  nameCell.innerHTML = "";
  nameCell.appendChild(input);
  input.focus();
  input.select();

  const save = async () => {
    const newName = input.value.trim();
    if (newName && newName !== job.name) {
      try {
        await postJson(`/api/jobs/${job.job_id}/name`, { name: newName });
      } catch (error) {
        showMessage(error.message, "error");
      }
    }
    refreshJobList();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") input.blur();
    if (event.key === "Escape") {
      input.value = job.name; // 元の名前に戻してから保存（変更なし扱い）
      input.blur();
    }
  });
  input.addEventListener("blur", save);
}

async function deleteJob(job) {
  const confirmed = window.confirm(
    `ジョブ「${job.name}」を削除します。計算結果も消えます。よろしいですか？`);
  if (!confirmed) return;
  try {
    const response = await fetch(`/api/jobs/${job.job_id}`,
                                 { method: "DELETE" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      showMessage(data.detail || "削除できませんでした", "error");
    }
  } catch (error) {
    showMessage("削除できませんでした", "error");
  }
  refreshJobList();
}

// ---- ジョブ履歴の折りたたみ・一括削除 ----

function applyJobHistoryCollapsed(collapsed) {
  jobHistoryBody.hidden = collapsed;
  jobHistoryToggle.textContent = collapsed ? "表示" : "隠す";
}

jobHistoryToggle.addEventListener("click", () => {
  const collapsed = !jobHistoryBody.hidden;
  localStorage.setItem(JOB_HISTORY_COLLAPSED_KEY, collapsed ? "1" : "0");
  applyJobHistoryCollapsed(collapsed);
});

selectAllCheckbox.addEventListener("change", () => {
  // 実行中のジョブは削除できないため選択対象から外れている（disabled）
  for (const checkbox of jobTableBody.querySelectorAll(
      ".job-select:not(:disabled)")) {
    checkbox.checked = selectAllCheckbox.checked;
  }
});

async function bulkDeleteJobs(requestBody, confirmText) {
  if (!window.confirm(confirmText)) return;
  try {
    const response = await postJson("/api/jobs/bulk-delete", requestBody);
    const data = await response.json();
    let message = `${data.deleted}件のジョブを削除しました`;
    if (data.skipped > 0) {
      message += `（実行中などの${data.skipped}件はスキップ）`;
    }
    showMessage(message, "warning");
  } catch (error) {
    showMessage(error.message, "error");
  }
  selectAllCheckbox.checked = false;
  refreshJobList();
}

deleteSelectedButton.addEventListener("click", () => {
  const selectedIds = [...jobTableBody.querySelectorAll(".job-select:checked")]
    .map((checkbox) => checkbox.dataset.jobId);
  if (selectedIds.length === 0) {
    showMessage("削除するジョブにチェックを入れてください", "error");
    return;
  }
  bulkDeleteJobs(
    { job_ids: selectedIds },
    `選択した${selectedIds.length}件のジョブを削除します。` +
      "計算結果も消えます。よろしいですか？");
});

deleteAllButton.addEventListener("click", () => {
  bulkDeleteJobs(
    { all: true },
    "すべてのジョブを削除します（実行中のジョブを除く）。" +
      "計算結果も消えます。よろしいですか？");
});

async function refreshJobList() {
  try {
    const response = await fetch("/api/jobs");
    const data = await response.json();
    jobTableBody.innerHTML = "";
    for (const job of data.jobs) {
      const row = document.createElement("tr");
      const statusLabel = {
        finished: "完了", running: "実行中",
        failed: "失敗", cancelled: "中断",
      }[job.status] || job.status;

      // ジョブ名の下にIDを小さく表示する（結果フォルダを探すときの手がかり）。
      // 名前が未設定でIDと同じときは二重表示になるため省く
      const idSub = job.name === job.job_id ? ""
        : `<div class="job-id-sub">${escapeHtml(job.job_id)}</div>`;
      const selectDisabled = job.status === "running" ? "disabled" : "";
      row.innerHTML = `
        <td class="select-column">
          <input type="checkbox" class="job-select" ${selectDisabled}
                 data-job-id="${escapeHtml(job.job_id)}">
        </td>
        <td class="job-name" title="${escapeHtml(job.job_id)}">
          ${escapeHtml(job.name)}${idSub}
        </td>
        <td class="status-${job.status}">${statusLabel}</td>
        <td>${job.elapsed_seconds ?? "—"}</td>
        <td></td>
      `;
      const nameCell = row.querySelector(".job-name");
      const actionCell = row.querySelector("td:last-child");
      if (job.status === "finished") {
        addActionButton(actionCell, "結果を表示", async () => {
          const jobResponse = await fetch(`/api/jobs/${job.job_id}`);
          const detail = await jobResponse.json();
          if (detail.result) renderResult(job.job_id, detail.result);
        });
      } else if (job.status === "running") {
        addActionButton(actionCell, "中断", async () => {
          await fetch(`/api/jobs/${job.job_id}/cancel`, { method: "POST" });
          refreshJobList();
        });
      }
      addActionButton(actionCell, "名前変更",
                      () => startRenameJob(nameCell, job));
      if (job.status !== "running") {
        addActionButton(actionCell, "削除", () => deleteJob(job));
      }
      jobTableBody.appendChild(row);
    }
  } catch (error) {
    // 一覧取得の失敗は画面を壊さない（次回の更新で回復する）
  }
}

applyJobHistoryCollapsed(
  localStorage.getItem(JOB_HISTORY_COLLAPSED_KEY) === "1");
refreshJobList();
