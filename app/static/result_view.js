// 計算結果の表示処理（メイン画面とジョブ履歴ページで共有）

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;",
  }[ch]));
}

// targetArea: 結果を描画する要素（メイン画面は計算結果、履歴ページは過去の結果）
function renderResult(jobId, result, targetArea) {
  if (result.type === "sweep") {
    renderSweepResult(jobId, result, targetArea);
    return;
  }
  if (result.type === "batch") {
    renderBatchResult(jobId, result, targetArea);
    return;
  }

  const efficiencyPercent =
    (result.collection_efficiency_total * 100).toFixed(1);
  // 受光内訳（中央照射）のときは位置が分かる名前で表示する。
  // 照射したレンズの画素を「中央」とし、その左右を「左隣」「右隣」と呼ぶ
  // （混在パターンでは中央レンズがセル中央の画素とは限らないため、
  // 計算結果の center_pixel_indices を基準にする）。
  // グループ内が複数画素のときは番号を付ける（例: 中央-1, 中央-2）
  const perPixelValues = result.collection_efficiency_per_pixel;
  const unitPixels = result.unit_pixels || 1;
  const centerIndices = result.center_pixel_indices;
  const hasBreakdownIndices = (result.crosstalk_total !== undefined
                               && Array.isArray(centerIndices)
                               && centerIndices.length > 0);
  // 古い結果（center_pixel_indicesなし）向けの従来判定
  const isLegacyBreakdown = (result.crosstalk_total !== undefined
                             && perPixelValues.length === 3 * unitPixels);
  const pixelName = (index) => {
    if (hasBreakdownIndices) {
      const first = Math.min(...centerIndices);
      const last = Math.max(...centerIndices);
      if (index < first) {
        return first > 1 ? `左隣-${index + 1}` : "左隣";
      }
      if (index <= last) {
        return centerIndices.length > 1
          ? `中央-${index - first + 1}` : "中央";
      }
      const rightCount = perPixelValues.length - last - 1;
      return rightCount > 1 ? `右隣-${index - last}` : "右隣";
    }
    if (!isLegacyBreakdown) return `画素${index + 1}`;
    const unitLabel = ["左隣", "中央", "右隣"][Math.floor(index / unitPixels)];
    if (unitPixels === 1) return unitLabel;
    return `${unitLabel}-${(index % unitPixels) + 1}`;
  };
  const perPixel = perPixelValues
    .map((value, index) =>
      `${pixelName(index)}: ${(value * 100).toFixed(1)}%`)
    .join(" / ");

  // 表示順: 集光効率（合計）→（中央画素）→クロストーク→計算時間→
  // 画素ごとの内訳（2026-07-19 ユーザー指示）
  let metrics = `
    <div class="metric">
      <div class="metric-label">集光効率（合計）</div>
      <div class="metric-value">${efficiencyPercent}%</div>
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
    <div class="metric">
      <div class="metric-label">画素ごとの内訳</div>
      <div class="metric-value" style="font-size:0.95rem">${perPixel}</div>
    </div>
  `;

  let images =
    `<img src="/api/jobs/${jobId}/image" alt="断面の構造と電場強度分布">`;
  if (result.input && result.input.mode === "3d") {
    images += `
      <img src="/api/jobs/${jobId}/topview" alt="真上ビューの電場強度分布">`;
  }

  targetArea.innerHTML =
    `<div class="result-numbers">${metrics}</div>${images}`;
}

function renderSweepResult(jobId, result, targetArea) {
  const rows = result.sweep.results.map((entry) => {
    const crosstalkCell = entry.crosstalk_total !== undefined
      ? `<td>${(entry.crosstalk_total * 100).toFixed(2)}%</td>` : "";
    return `<tr><td>${entry.value}</td>` +
      `<td>${(entry.collection_efficiency_total * 100).toFixed(1)}%</td>` +
      crosstalkCell + `</tr>`;
  }).join("");
  const crosstalkHeader = result.sweep.results[0].crosstalk_total !== undefined
    ? "<th>クロストーク</th>" : "";

  targetArea.innerHTML = `
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

function renderBatchResult(jobId, result, targetArea) {
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

  targetArea.innerHTML = `
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
