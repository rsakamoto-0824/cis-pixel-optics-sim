// CIS画素光学シミュレーター 画面ロジック

const JOB_POLL_INTERVAL_MS = 2000;

const form = document.getElementById("parameter-form");
const previewButton = document.getElementById("preview-button");
const runButton = document.getElementById("run-button");
const formMessage = document.getElementById("form-message");
const previewArea = document.getElementById("preview-area");
const resultArea = document.getElementById("result-area");
const jobTableBody = document.getElementById("job-table-body");

let pollTimerId = null;

function numberValue(id) {
  return parseFloat(document.getElementById(id).value);
}

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
  const viewDepthText = document.getElementById("view-depth").value;
  return {
    mode: document.getElementById("calc-mode").value,
    crosstalk: document.getElementById("crosstalk-enabled").checked,
    view: {
      depth_um: viewDepthText === "" ? null : parseFloat(viewDepthText),
    },
    sweep: collectSweep(),
    pixel_pitch_um: numberValue("pixel-pitch"),
    ocl: {
      enabled: document.getElementById("ocl-enabled").checked,
      height_um: numberValue("ocl-height"),
      shape: document.getElementById("ocl-shape").value,
      superellipse_exponent: numberValue("ocl-superellipse-exponent"),
      sharing: document.getElementById("ocl-sharing").value,
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

  const efficiencyPercent =
    (result.collection_efficiency_total * 100).toFixed(1);
  const perPixel = result.collection_efficiency_per_pixel
    .map((value, index) => `画素${index + 1}: ${(value * 100).toFixed(1)}%`)
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

// ---- ジョブ履歴 ----

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

      row.innerHTML = `
        <td>${job.job_id}</td>
        <td class="status-${job.status}">${statusLabel}</td>
        <td>${job.elapsed_seconds ?? "—"}</td>
        <td></td>
      `;
      const actionCell = row.querySelector("td:last-child");
      if (job.status === "finished") {
        const viewButton = document.createElement("button");
        viewButton.className = "link-button";
        viewButton.textContent = "結果を表示";
        viewButton.addEventListener("click", async () => {
          const jobResponse = await fetch(`/api/jobs/${job.job_id}`);
          const detail = await jobResponse.json();
          if (detail.result) renderResult(job.job_id, detail.result);
        });
        actionCell.appendChild(viewButton);
      } else if (job.status === "running") {
        const cancelButton = document.createElement("button");
        cancelButton.className = "link-button";
        cancelButton.textContent = "中断";
        cancelButton.addEventListener("click", async () => {
          await fetch(`/api/jobs/${job.job_id}/cancel`, { method: "POST" });
          refreshJobList();
        });
        actionCell.appendChild(cancelButton);
      }
      jobTableBody.appendChild(row);
    }
  } catch (error) {
    // 一覧取得の失敗は画面を壊さない（次回の更新で回復する）
  }
}

refreshJobList();
