// CIS画素光学シミュレーター メイン画面ロジック
// （ジョブ履歴は別ページ history.html / history.js に分離。2026-07-19）

const JOB_POLL_INTERVAL_MS = 2000;

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

function collectOclPattern() {
  const text = document.getElementById("ocl-pattern").value.trim();
  if (!text) return null;
  const tokenToSharing = { "1": "single", "2": "shared2", "4": "shared4" };
  return text.split(",")
    .map((token) => token.trim())
    .filter((token) => token !== "")
    // 不明な値はそのまま送り、サーバー側の日本語エラーに任せる
    .map((token) => tokenToSharing[token] || token);
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
    rgb: document.getElementById("rgb-enabled").checked,
    rgb_wavelengths_nm: [
      numberValue("rgb-wavelength-r"),
      numberValue("rgb-wavelength-g"),
      numberValue("rgb-wavelength-b"),
    ],
    pixel_pitch_um: numberValue("pixel-pitch"),
    ocl: {
      enabled: document.getElementById("ocl-enabled").checked,
      height_um: numberValue("ocl-height"),
      shape: document.getElementById("ocl-shape").value,
      superellipse_exponent: numberValue("ocl-superellipse-exponent"),
      sharing: document.getElementById("ocl-sharing").value,
      pattern: collectOclPattern(),
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
    // 表示し終えた画像データを解放する（繰り返しプレビューでのメモリ増加防止）
    image.addEventListener("load", () => URL.revokeObjectURL(image.src),
                           { once: true });
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
    params.sweep = null; // 一括計算とスイープ・RGB評価は同時に使わない
    params.rgb = false;
    params.batch_csv = await readCsvFileText(batchFileInput.files[0]);
    const response = await postJson("/api/jobs", params);
    const data = await response.json();
    if (data.warnings && data.warnings.length > 0) {
      showMessage(`注意:\n${data.warnings.join("\n")}`, "warning");
    }
    watchJob(data.job_id);
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

      if (job.status === "finished" && job.result) {
        renderResult(jobId, job.result, resultArea);
      } else if (job.status === "cancelled") {
        resultArea.innerHTML = '<p class="placeholder">計算を中断しました</p>';
        showMessage("計算を中断しました", "warning");
      } else {
        resultArea.innerHTML = "";
        showMessage(`計算が失敗しました: ${job.error || "原因不明"}`, "error");
      }
    } catch (error) {
      // 一時的な通信エラーは次のポーリングで再試行する
    }
  }, JOB_POLL_INTERVAL_MS);
}
