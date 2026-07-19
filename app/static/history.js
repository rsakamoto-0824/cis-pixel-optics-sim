// ジョブ履歴ページのロジック（メイン画面 app.js から2026-07-19に分離）

// 実行中ジョブの状態が自動で最新になるよう、一覧を定期更新する
const JOB_LIST_REFRESH_INTERVAL_MS = 5000;

const jobTableBody = document.getElementById("job-table-body");
const selectAllCheckbox = document.getElementById("select-all-jobs");
const deleteSelectedButton = document.getElementById("delete-selected-button");
const deleteAllButton = document.getElementById("delete-all-button");
const historyMessage = document.getElementById("history-message");
const historyResultArea = document.getElementById("history-result-area");

function showMessage(text, kind) {
  historyMessage.textContent = text;
  historyMessage.className = `message ${kind}`;
  historyMessage.hidden = false;
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

// ---- 一括削除 ----

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

// ---- ジョブ一覧 ----

async function refreshJobList() {
  try {
    const response = await fetch("/api/jobs");
    const data = await response.json();
    // 選択中のチェックを再描画後も引き継ぐ
    const checkedIds = new Set(
      [...jobTableBody.querySelectorAll(".job-select:checked")]
        .map((checkbox) => checkbox.dataset.jobId));
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
      const selectChecked = checkedIds.has(job.job_id) ? "checked" : "";
      row.innerHTML = `
        <td class="select-column">
          <input type="checkbox" class="job-select"
                 ${selectDisabled} ${selectChecked}
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
          if (detail.result) {
            renderResult(job.job_id, detail.result, historyResultArea);
          }
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

// 名前変更の入力中は再描画すると編集が消えるためスキップする
setInterval(() => {
  if (jobTableBody.querySelector("input[type='text']")) return;
  refreshJobList();
}, JOB_LIST_REFRESH_INTERVAL_MS);

refreshJobList();
