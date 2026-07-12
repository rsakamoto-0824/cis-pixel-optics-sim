"""クロストーク評価（2D・3画素・中央照射）の動作確認サンプル。

実行方法（リポジトリ直下、conda環境 cis-pixel-optics で）:
    python -m tests.run_sample_crosstalk_2d
出力先: jobs/sample_crosstalk_2d/
"""

import json
from pathlib import Path

from app.result_plotter import plot_cross_section
from engine import fdtd_worker

SAMPLE_PARAMS = {
    "crosstalk": True,
    "pixel_pitch_um": 1.0,
}


def main():
    repo_root = Path(__file__).resolve().parent.parent
    job_dir = repo_root / "jobs" / "sample_crosstalk_2d"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "input.json").write_text(
        json.dumps(SAMPLE_PARAMS, ensure_ascii=False, indent=2))

    print("=== クロストーク評価サンプル（2D）を開始 ===")
    result = fdtd_worker.run_job(job_dir)
    center = result["collection_efficiency_center"]
    crosstalk = result["crosstalk_total"]
    print(f"集光効率（中央画素）: {center:.4f}")
    print(f"クロストーク（漏れ合計）: {crosstalk:.4f}"
          f"（計算時間 {result['elapsed_seconds']}秒）")
    print(f"断面図: {plot_cross_section(job_dir)}")


if __name__ == "__main__":
    main()
