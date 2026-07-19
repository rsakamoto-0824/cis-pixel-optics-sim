"""計算ジョブの起動・状態管理。

ジョブはサブプロセス（engine.fdtd_worker）として起動し、
状態は jobs/<job_id>/ 内のファイル（progress.json / result.json）で管理する。
"""

import datetime
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys

from app.constants import (JOB_ID_TIMESTAMP_FORMAT, JOB_LIST_MAX_COUNT,
                           JOBS_DIR, REPO_ROOT)


def reap_finished_workers():
    """終了したワーカーを回収し、ハンドル一覧から取り除く（ゾンビ解消）。"""
    for pid, process in list(WORKER_PROCESSES.items()):
        if process.poll() is not None:
            del WORKER_PROCESSES[pid]


def create_job(params, job_name):
    """input.jsonを書き出してワーカーを起動し、ジョブIDを返す。"""
    reap_finished_workers()
    timestamp = datetime.datetime.now().strftime(JOB_ID_TIMESTAMP_FORMAT)
    job_id = f"{timestamp}-{secrets.token_hex(3)}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)

    (job_dir / "input.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=2))
    (job_dir / "progress.json").write_text(
        json.dumps({"status": "running", "phase": "starting"},
                   ensure_ascii=False))

    # ログファイルはワーカー側が引き継ぐため、サーバー側はすぐ閉じる
    # （開いたままにするとジョブごとにファイルハンドルが漏れる）
    with open(job_dir / "worker.log", "w") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "engine.fdtd_worker", str(job_dir)],
            cwd=REPO_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    WORKER_PROCESSES[process.pid] = process
    (job_dir / "meta.json").write_text(json.dumps({
        "pid": process.pid,
        "name": job_name,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False))
    return job_id


def read_json_if_exists(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        # ワーカーの書き込みと読み込みが重なった瞬間は読み飛ばす
        return None


# このサーバーが起動したワーカーのプロセスハンドル（生存判定に使う）。
# ハンドルを残しておかないと、終了したワーカーがゾンビとして残り、
# os.killによる判定では「生きている」と誤判定されることがある
WORKER_PROCESSES = {}


def is_process_alive(pid):
    process = WORKER_PROCESSES.get(pid)
    if process is not None:
        # poll() は終了済みプロセスを回収（ゾンビ解消）して終了を検出する
        return process.poll() is None
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def get_job_status(job_id):
    """ジョブの状態を返す。ワーカーが異常終了していれば failed に補正する。"""
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        return None

    progress = read_json_if_exists(job_dir / "progress.json") or {}
    meta = read_json_if_exists(job_dir / "meta.json") or {}
    params = read_json_if_exists(job_dir / "input.json") or {}
    status = progress.get("status", "unknown")

    if status == "running" and not is_process_alive(meta.get("pid")):
        # 完了直後はprogress.jsonの読み込みとプロセス終了が入れ違うことが
        # あるため、読み直してから失敗と判定する（誤「失敗」表示の防止）
        progress = read_json_if_exists(job_dir / "progress.json") or progress
        status = progress.get("status", "unknown")
        if status == "running":
            status = "failed"
            progress["error"] = progress.get(
                "error", "計算プロセスが異常終了しました（worker.logを確認）")

    return {
        "job_id": job_id,
        # 名前が未設定の古いジョブ・サンプルジョブはIDをそのまま表示する
        "name": meta.get("name") or job_id,
        "status": status,
        "phase": progress.get("phase"),
        "error": progress.get("error"),
        "elapsed_seconds": progress.get("elapsed_seconds"),
        "created_at": meta.get("created_at"),
        "input": params,
    }


def get_job_result(job_id):
    return read_json_if_exists(JOBS_DIR / job_id / "result.json")


def list_jobs():
    """新しい順のジョブ一覧を返す。"""
    reap_finished_workers()
    if not JOBS_DIR.is_dir():
        return []
    job_ids = sorted((p.name for p in JOBS_DIR.iterdir() if p.is_dir()),
                     reverse=True)
    return [get_job_status(job_id)
            for job_id in job_ids[:JOB_LIST_MAX_COUNT]]


def rename_job(job_id, new_name):
    """ジョブ名を変更する（meta.jsonのnameを書き換える）。"""
    meta_path = JOBS_DIR / job_id / "meta.json"
    if not (JOBS_DIR / job_id).is_dir():
        return False
    meta = read_json_if_exists(meta_path) or {}
    meta["name"] = new_name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    return True


def delete_job(job_id):
    """ジョブのフォルダごと削除する。実行中は削除しない。

    戻り値: (成功したか, 失敗理由メッセージ)
    """
    status = get_job_status(job_id)
    if status is None:
        return False, "ジョブが見つかりません"
    if status["status"] == "running":
        return False, "実行中のジョブは中断してから削除してください"
    shutil.rmtree(JOBS_DIR / job_id)
    return True, None


def list_all_job_ids():
    """表示件数の上限に関係なく、全ジョブIDを返す（一括削除用）。"""
    if not JOBS_DIR.is_dir():
        return []
    return [path.name for path in JOBS_DIR.iterdir() if path.is_dir()]


def delete_jobs_bulk(job_ids):
    """複数ジョブを削除する。実行中などで削除できないものはスキップする。

    戻り値: (削除した件数, スキップした件数)
    """
    deleted_count = 0
    skipped_count = 0
    for job_id in job_ids:
        deleted, _ = delete_job(job_id)
        if deleted:
            deleted_count += 1
        else:
            skipped_count += 1
    return deleted_count, skipped_count


def cancel_job(job_id):
    """実行中のジョブを中断する。"""
    status = get_job_status(job_id)
    if status is None or status["status"] != "running":
        return False
    meta = read_json_if_exists(JOBS_DIR / job_id / "meta.json") or {}
    pid = meta.get("pid")
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    (JOBS_DIR / job_id / "progress.json").write_text(json.dumps(
        {"status": "cancelled", "error": "ユーザーが中断しました"},
        ensure_ascii=False))
    return True
