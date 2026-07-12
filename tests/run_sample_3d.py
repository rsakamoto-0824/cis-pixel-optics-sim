"""3Dモードの動作確認サンプル。

1.0 µm画素（OCL・DTIあり）を低解像度（40セル/µm）の3Dで計算し、
断面と真上ビューの画像を出力する。傾向確認用であり、定量評価には
警告に従って解像度を上げること。

実行方法（リポジトリ直下、conda環境 cis-pixel-optics で）:
    python -m tests.run_sample_3d
出力先: jobs/sample_3d/
"""

import json
from pathlib import Path

from app.result_plotter import plot_cross_section, plot_top_view
from engine import fdtd_worker

SAMPLE_RESOLUTION = 40  # 傾向確認用の低解像度（実行時間を数分に抑える）

SAMPLE_PARAMS = {
    "mode": "3d",
    "pixel_pitch_um": 1.0,
    "resolution_pixels_per_um": SAMPLE_RESOLUTION,
}


def main():
    repo_root = Path(__file__).resolve().parent.parent
    job_dir = repo_root / "jobs" / "sample_3d"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "input.json").write_text(
        json.dumps(SAMPLE_PARAMS, ensure_ascii=False, indent=2))

    print("=== 3Dサンプル計算を開始（低解像度） ===")
    result = fdtd_worker.run_job(job_dir)
    for warning in result["warnings"]:
        print(f"注意: {warning}")
    print(f"集光効率: {result['collection_efficiency_total']:.4f}"
          f"（計算時間 {result['elapsed_seconds']}秒）")
    print(f"断面図: {plot_cross_section(job_dir)}")
    print(f"真上ビュー: {plot_top_view(job_dir)}")


if __name__ == "__main__":
    main()
