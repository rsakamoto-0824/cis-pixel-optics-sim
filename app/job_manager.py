"""計算ジョブの起動・状態管理。

ジョブはサブプロセス（engine.fdtd_worker）として起動し、
状態は jobs/<job_id>/ 内のファイル（progress.json / result.json）で管理する。
"""

import datetime
import json
import os
import secrets
import signal
import subprocess
import sys

from app.constants import (JOB_ID_TIMESTAMP_FORMAT, JOB_LIST_MAX_COUNT,
                           JOBS_DIR, REPO_ROOT)


def create_job(params):
    """input.jsonを書き出してワーカーを起動し、ジョブIDを返す。"""
    timestamp = datetime.datetime.now().strftime(JOB_ID_TIMESTAMP_FORMAT)
    job_id = f"{timestamp}-{secrets.token_hex(3)}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)

    (job_dir / "input.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=2))
    (job_dir / "progress.json").write_text(
        json.dumps({"status": "running", "phase": "starting"},
                   ensure_ascii=False))

    log_file = open(job_dir / "worker.log", "w")
    process = subprocess.Popen(
        [sys.executable, "-m", "engine.fdtd_worker", str(job_dir)],
        cwd=REPO_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    (job_dir / "meta.json").write_text(json.dumps({
        "pid": process.pid,
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


def is_process_alive(pid):
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
        status = "failed"
        progress["error"] = progress.get(
            "error", "計算プロセスが異常終了しました（worker.logを確認）")

    return {
        "job_id": job_id,
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
    if not JOBS_DIR.is_dir():
        return []
    job_ids = sorted((p.name for p in JOBS_DIR.iterdir() if p.is_dir()),
                     reverse=True)
    return [get_job_status(job_id)
            for job_id in job_ids[:JOB_LIST_MAX_COUNT]]


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
