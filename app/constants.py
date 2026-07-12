"""アプリ全体の定数。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = REPO_ROOT / "jobs"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ローカル専用アプリのため127.0.0.1に固定する（外部からアクセスさせない）
HOST = "127.0.0.1"
PORT = 8000

JOB_ID_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

# ジョブ一覧に表示する最大件数
JOB_LIST_MAX_COUNT = 50
