"""2Dモードの動作確認サンプル。

1.0 µm画素（OCL・DTIあり）に波長550 nmを垂直入射と斜入射（CRA 25°）で
照射し、集光効率と断面の電場強度分布（|E|^2）画像を出力する。

実行方法（リポジトリ直下、conda環境 cis-pixel-optics で）:
    python -m tests.run_sample_2d
出力先: jobs/sample_2d_cra00/ と jobs/sample_2d_cra25/
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from engine import fdtd_worker

# 日本語ラベルの文字化け対策（macOS標準→Windows標準の順で探す）
plt.rcParams["font.family"] = ["Hiragino Sans", "Yu Gothic", "Meiryo",
                               "sans-serif"]

INCIDENT_ANGLES_DEG = [0.0, 25.0]

SAMPLE_PARAMS = {
    "pixel_pitch_um": 1.0,
    "ocl": {"enabled": True, "height_um": 0.5, "shape": "spherical_cap",
            "sharing": "single"},
    "layers": {"planarization_um": 0.1, "color_filter_um": 0.6,
               "ar_um": 0.1, "si_um": 3.0},
    "dti": {"enabled": True, "width_um": 0.1, "depth_um": 2.0,
            "placement": "all"},
    "source": {"wavelength_nm": 550.0},
    "pd": {"top_depth_um": 0.5},
}


def plot_cross_section(job_dir):
    """fields.npz から構造と|E|^2の断面図を並べたPNGを作る。"""
    data = np.load(job_dir / "fields.npz")
    # 縦軸はSi上面を0とした物理座標で表示する
    si_top_y = float(data["si_top_y"])
    x_um = data["x_um"]
    y_um = data["y_um"] - si_top_y
    pd_y = float(data["pd_monitor_y"]) - si_top_y
    extent = [x_um[0], x_um[-1], y_um[0], y_um[-1]]

    figure, (ax_structure, ax_intensity) = plt.subplots(
        1, 2, figsize=(9, 6), sharey=True)

    ax_structure.imshow(data["epsilon"].T, origin="lower", extent=extent,
                        cmap="binary", aspect="equal")
    ax_structure.set_title("構造（誘電率）")
    ax_structure.set_xlabel("x [µm]")
    ax_structure.set_ylabel("y [µm]（Si上面 = 0）")

    image = ax_intensity.imshow(data["intensity"].T, origin="lower",
                                extent=extent, cmap="inferno", aspect="equal")
    ax_intensity.contour(data["epsilon"].T, levels=3, origin="lower",
                         extent=extent, colors="white", linewidths=0.4)
    ax_intensity.axhline(pd_y, color="cyan",
                         linewidth=0.8, linestyle="--", label="PD面")
    ax_intensity.set_title("|E|² 断面分布")
    ax_intensity.set_xlabel("x [µm]")
    ax_intensity.legend(loc="lower right")
    figure.colorbar(image, ax=ax_intensity, shrink=0.8)

    output_path = job_dir / "cross_section.png"
    figure.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output_path


def main():
    repo_root = Path(__file__).resolve().parent.parent
    for angle_deg in INCIDENT_ANGLES_DEG:
        job_dir = repo_root / "jobs" / f"sample_2d_cra{int(angle_deg):02d}"
        job_dir.mkdir(parents=True, exist_ok=True)

        params = json.loads(json.dumps(SAMPLE_PARAMS))
        params["source"]["incident_angle_deg"] = angle_deg
        (job_dir / "input.json").write_text(
            json.dumps(params, ensure_ascii=False, indent=2))

        print(f"=== CRA {angle_deg}° の計算を開始 ===")
        result = fdtd_worker.run_job(job_dir)
        image_path = plot_cross_section(job_dir)

        efficiency = result["collection_efficiency_total"]
        print(f"集光効率: {efficiency:.4f}"
              f"（計算時間 {result['elapsed_seconds']}秒）")
        print(f"断面図: {image_path}")


if __name__ == "__main__":
    main()
