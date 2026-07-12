"""FDTD設定の物理検証: フレネル解析解との比較（design.md 6章）。

1. レンズ・膜なしの平面Si基板に垂直入射したときの透過率を、
   解析解（フレネル係数 + Si内の吸収減衰）と比較する
2. メッシュ解像度を2倍にしたときの変化が±2%以内であることを確認する

実行方法（リポジトリ直下、conda環境 cis-pixel-optics で）:
    python -m tests.validate_fresnel
"""

import json
import sys
import tempfile
from pathlib import Path

from engine import fdtd_worker, materials

# 検証条件
WAVELENGTH_NM = 550.0
PD_DEPTH_UM = 0.1              # Si上面からモニター面までの深さ
TOLERANCE_RELATIVE = 0.03      # 解析解との許容相対誤差（±3%）
CONVERGENCE_TOLERANCE = 0.02   # 解像度2倍時の許容変化（±2%）
BASE_RESOLUTION = 100          # 既定解像度（fdtd_workerの既定値と同じ）

# レンズ・膜なしの平面Si基板
VALIDATION_PARAMS = {
    "pixel_pitch_um": 1.0,
    "ocl": {"enabled": False},
    "layers": {"planarization_um": 0.0, "color_filter_um": 0.0,
               "ar_um": 0.0, "si_um": 3.0},
    "dti": {"enabled": False},
    "source": {"wavelength_nm": WAVELENGTH_NM, "incident_angle_deg": 0.0},
    "pd": {"top_depth_um": PD_DEPTH_UM},
}


def analytic_transmittance():
    """空気/Si界面の透過率（1-R）にSi内の吸収減衰を掛けた解析値。"""
    n, k = materials.silicon_nk(WAVELENGTH_NM)
    si_index = complex(n, k)
    reflectance = abs((1.0 - si_index) / (1.0 + si_index)) ** 2
    absorption = materials.silicon_absorption_factor(WAVELENGTH_NM, PD_DEPTH_UM)
    return (1.0 - reflectance) * absorption


def run_with_resolution(resolution):
    params = json.loads(json.dumps(VALIDATION_PARAMS))
    params["resolution_pixels_per_um"] = resolution
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir)
        (job_dir / "input.json").write_text(
            json.dumps(params, ensure_ascii=False))
        result = fdtd_worker.run_job(job_dir)
    return result["collection_efficiency_total"]


def main():
    analytic_value = analytic_transmittance()
    fdtd_value = run_with_resolution(BASE_RESOLUTION)
    fdtd_value_fine = run_with_resolution(BASE_RESOLUTION * 2)

    relative_error = abs(fdtd_value - analytic_value) / analytic_value
    convergence_change = abs(fdtd_value_fine - fdtd_value) / fdtd_value

    print(f"解析解（フレネル+吸収）    : {analytic_value:.4f}")
    print(f"FDTD透過率（解像度{BASE_RESOLUTION}） : {fdtd_value:.4f}"
          f"  相対誤差 {relative_error * 100:.2f}%"
          f"（許容 {TOLERANCE_RELATIVE * 100:.0f}%）")
    print(f"FDTD透過率（解像度{BASE_RESOLUTION * 2}） : {fdtd_value_fine:.4f}"
          f"  変化 {convergence_change * 100:.2f}%"
          f"（許容 {CONVERGENCE_TOLERANCE * 100:.0f}%）")

    failed = (relative_error > TOLERANCE_RELATIVE
              or convergence_change > CONVERGENCE_TOLERANCE)
    if failed:
        print("判定: NG — FDTD設定を見直してください")
        sys.exit(1)
    print("判定: OK")


if __name__ == "__main__":
    main()
