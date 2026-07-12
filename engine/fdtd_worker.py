"""FDTD計算ワーカー（2Dモード）。

使い方（conda環境 cis-pixel-optics で実行）:
    python -m engine.fdtd_worker <job_dir>

job_dir/input.json を読み、以下を出力する。
    progress.json  進行状況（実行中に逐次更新）
    result.json    集光効率などの数値結果
    fields.npz     電場強度分布 |E|^2 と構造（誘電率）分布

集光効率は「参照計算（構造なし・空気のみ）の入射パワー」に対する
「PD面（Si内の指定深さの水平面）を通過するパワー」の比で定義する。
"""

import cmath
import json
import math
import sys
import time
from pathlib import Path

import meep as mp
import numpy as np

from engine import materials, structure_builder

# 光源: 単一波長評価のため狭帯域ガウシアンパルスを使う
SOURCE_FRACTIONAL_BANDWIDTH = 0.2

# 収束判定: PD面近くの電場がピーク比でこの値まで減衰したら計算終了
FIELD_DECAY_THRESHOLD = 1e-4
FIELD_DECAY_CHECK_INTERVAL = 25.0  # Meep時間単位

# 進行状況の書き出し間隔（Meep時間単位）
PROGRESS_WRITE_INTERVAL = 10.0

# 入力パラメータの許容範囲（requirements.md 4章）
PARAMETER_LIMITS = {
    "pixel_pitch_um": (0.5, 2.5),
    "ocl_height_um": (0.1, 1.5),
    "wavelength_nm": (400.0, 700.0),
    "incident_angle_deg": (0.0, 35.0),
    "dti_width_um": (0.05, 0.3),
}

DEFAULT_PARAMS = {
    "mode": "2d",
    "pixel_pitch_um": 1.0,
    "ocl": {
        "enabled": True,
        "height_um": 0.5,
        "shape": "spherical_cap",  # spherical_cap / superellipse
        "superellipse_exponent": 2.5,
        "sharing": "single",       # single / shared2 / shared4
    },
    "layers": {
        "planarization_um": 0.1,
        "color_filter_um": 0.6,
        "ar_um": 0.1,
        "si_um": 3.0,
    },
    "materials": {
        "ocl_n": materials.DEFAULT_OCL_N,
        "planarization_n": materials.DEFAULT_PLANARIZATION_N,
        "color_filter_n": materials.DEFAULT_COLOR_FILTER_N,
        "ar_n": materials.DEFAULT_AR_N,
        "dti_n": materials.DEFAULT_DTI_FILL_N,
    },
    "dti": {
        "enabled": True,
        "width_um": 0.1,
        "depth_um": 2.0,
        "placement": "all",  # all / shared_only
    },
    "source": {
        "wavelength_nm": 550.0,
        "incident_angle_deg": 0.0,
    },
    "pd": {
        "top_depth_um": 0.5,  # Si上面からPD面までの深さ
    },
    # Si内は波長が1/n（550 nmで約135 nm）に縮むため、空気基準ではなく
    # Si内波長を10セル以上で分解できる値にする（フレネル検証で確認済み）
    "resolution_pixels_per_um": 100,
}

# Si内波長あたりのセル数がこの値を下回ると精度警告を出す
MIN_CELLS_PER_WAVELENGTH_IN_SI = 10.0


def merge_defaults(user_params, defaults):
    """既定値辞書にユーザー入力を重ねる（入れ子辞書対応）。"""
    merged = {}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict):
            merged[key] = merge_defaults(user_params.get(key, {}), default_value)
        else:
            merged[key] = user_params.get(key, default_value)
    return merged


def validate_params(params):
    """パラメータの範囲チェック。問題があれば日本語メッセージで例外を出す。"""
    errors = []

    def check_range(value, key, label):
        low, high = PARAMETER_LIMITS[key]
        if not (low <= value <= high):
            errors.append(f"{label} が範囲外です: {value}（許容 {low}〜{high}）")

    check_range(params["pixel_pitch_um"], "pixel_pitch_um", "画素サイズ [µm]")
    check_range(params["source"]["wavelength_nm"], "wavelength_nm", "波長 [nm]")
    check_range(params["source"]["incident_angle_deg"], "incident_angle_deg",
                "入射角 [deg]")
    if params["ocl"]["enabled"]:
        check_range(params["ocl"]["height_um"], "ocl_height_um", "OCL高さ [µm]")
        if params["ocl"]["shape"] not in ("spherical_cap", "superellipse"):
            errors.append(f"未知のレンズ形状です: {params['ocl']['shape']}")
        if params["ocl"]["sharing"] not in ("single", "shared2", "shared4"):
            errors.append(f"未知のOCL共有方式です: {params['ocl']['sharing']}")
    if params["dti"]["enabled"]:
        check_range(params["dti"]["width_um"], "dti_width_um", "DTI幅 [µm]")
        if not (0.0 <= params["dti"]["depth_um"] <= params["layers"]["si_um"]):
            errors.append(
                f"DTI深さ {params['dti']['depth_um']} µm はSi厚 "
                f"{params['layers']['si_um']} µm 以下にしてください")
        if params["dti"]["placement"] not in ("all", "shared_only"):
            errors.append(f"未知のDTI配置です: {params['dti']['placement']}")
    if params["pd"]["top_depth_um"] >= params["layers"]["si_um"]:
        errors.append("PD面の深さはSi厚より浅くしてください")
    if params["mode"] != "2d":
        errors.append("現在は2Dモードのみ対応しています（3Dモードは開発中）")

    if errors:
        raise ValueError("\n".join(errors))


def collect_warnings(params):
    """実行は可能だが注意が必要な条件を日本語メッセージで返す。"""
    warnings = []
    resolution = params["resolution_pixels_per_um"]
    if params["dti"]["enabled"]:
        dti_cells = params["dti"]["width_um"] * resolution
        if dti_cells < 3.0:
            warnings.append(
                f"DTI幅 {params['dti']['width_um']} µm はメッシュ "
                f"{dti_cells:.1f} セル分しかなく精度が低下します。"
                "解像度を上げることを推奨します")
    wavelength_um = params["source"]["wavelength_nm"] / 1000.0
    si_n, _ = materials.silicon_nk(params["source"]["wavelength_nm"])
    cells_per_wavelength_in_si = resolution * wavelength_um / si_n
    if cells_per_wavelength_in_si < MIN_CELLS_PER_WAVELENGTH_IN_SI:
        recommended = math.ceil(
            MIN_CELLS_PER_WAVELENGTH_IN_SI * si_n / wavelength_um / 10.0) * 10
        warnings.append(
            f"Si内波長あたり {cells_per_wavelength_in_si:.1f} セルしかなく"
            f"精度が低下します。解像度 {recommended} 以上を推奨します")
    if params["ocl"]["sharing"] == "shared4":
        warnings.append(
            "shared4（4画素共有）の2Dモードは、2×2レンズ中央断面を"
            "2画素共有と同形状で近似しています。定量評価は3Dモードで行ってください")
    return warnings


def build_media(params):
    """波長に応じた {層名: mp.Medium} 辞書を作る。"""
    wavelength_nm = params["source"]["wavelength_nm"]
    wavelength_um = wavelength_nm / 1000.0
    si_n, si_k = materials.silicon_nk(wavelength_nm)
    mat = params["materials"]
    return {
        "ocl": materials.make_medium(mat["ocl_n"], 0.0, wavelength_um),
        "planarization": materials.make_medium(mat["planarization_n"], 0.0,
                                               wavelength_um),
        "color_filter": materials.make_medium(mat["color_filter_n"], 0.0,
                                              wavelength_um),
        "ar": materials.make_medium(mat["ar_n"], 0.0, wavelength_um),
        "silicon": materials.make_medium(si_n, si_k, wavelength_um),
        "dti": materials.make_medium(mat["dti_n"], 0.0, wavelength_um),
    }


def make_sources(params, coords):
    """斜入射に対応した平面波光源を作る。"""
    frequency = 1000.0 / params["source"]["wavelength_nm"]  # Meep単位（1/µm）
    theta_rad = math.radians(params["source"]["incident_angle_deg"])
    kx = frequency * math.sin(theta_rad)  # 空気中（n=1）の横方向波数

    def plane_wave_amplitude(position):
        return cmath.exp(2j * math.pi * kx * position.x)

    source = mp.Source(
        mp.GaussianSource(frequency,
                          fwidth=SOURCE_FRACTIONAL_BANDWIDTH * frequency),
        component=mp.Ez,  # 2DはEz偏光（s偏光）で評価する
        center=mp.Vector3(0, coords["source_y"]),
        size=mp.Vector3(coords["cell_width_um"], 0),
        amp_func=plane_wave_amplitude,
    )
    k_point = mp.Vector3(kx, 0, 0)  # Bloch周期境界で斜入射を表現
    return [source], k_point, frequency


def make_simulation(params, geometry, cell_size, boundary_layers, sources,
                    k_point):
    return mp.Simulation(
        cell_size=cell_size,
        geometry=geometry,
        boundary_layers=boundary_layers,
        sources=sources,
        k_point=k_point,
        resolution=params["resolution_pixels_per_um"],
        force_complex_fields=(k_point.x != 0.0),
    )


def add_pd_flux_monitors(sim, params, coords, frequency):
    """PD面のフラックスモニターを画素ごとに追加する。"""
    pitch = params["pixel_pitch_um"]
    num_pixels = coords["num_pixels"]
    cell_width = coords["cell_width_um"]
    monitors = []
    for i in range(num_pixels):
        center_x = -cell_width / 2.0 + pitch * (i + 0.5)
        region = mp.FluxRegion(
            center=mp.Vector3(center_x, coords["pd_monitor_y"]),
            size=mp.Vector3(pitch, 0))
        monitors.append(sim.add_flux(frequency, 0, 1, region))
    return monitors


def run_simulation(sim, coords, frequency, progress_path, phase_label,
                   start_time):
    """電場が減衰するまで実行し、進行状況を progress.json に書き出す。"""
    decay_point = mp.Vector3(0, coords["pd_monitor_y"])

    def write_progress(sim_instance):
        progress = {
            "status": "running",
            "phase": phase_label,
            "meep_time": sim_instance.meep_time(),
            "elapsed_seconds": round(time.time() - start_time, 1),
        }
        progress_path.write_text(json.dumps(progress, ensure_ascii=False))

    sim.run(
        mp.at_every(PROGRESS_WRITE_INTERVAL, write_progress),
        until_after_sources=mp.stop_when_fields_decayed(
            FIELD_DECAY_CHECK_INTERVAL, mp.Ez, decay_point,
            FIELD_DECAY_THRESHOLD))


def run_job(job_dir):
    """input.json を読んで2D FDTDを実行し、結果一式を書き出す。"""
    job_dir = Path(job_dir)
    progress_path = job_dir / "progress.json"
    start_time = time.time()

    user_params = json.loads((job_dir / "input.json").read_text())
    params = merge_defaults(user_params, DEFAULT_PARAMS)
    validate_params(params)
    warnings = collect_warnings(params)

    media = build_media(params)
    structure = structure_builder.build_structure_2d(params, media)
    coords = structure["coords"]
    sources, k_point, frequency = make_sources(params, coords)

    # 1回目: 参照計算（構造なし・空気のみ）で入射パワーを測る
    reference_sim = make_simulation(params, [], structure["cell_size"],
                                    structure["boundary_layers"], sources,
                                    k_point)
    reference_monitors = add_pd_flux_monitors(reference_sim, params, coords,
                                              frequency)
    run_simulation(reference_sim, coords, frequency, progress_path,
                   "reference", start_time)
    incident_flux_per_pixel = [mp.get_fluxes(m)[0] for m in reference_monitors]
    incident_flux_total = sum(incident_flux_per_pixel)
    reference_sim.reset_meep()

    # 2回目: 本計算（画素構造あり）
    sim = make_simulation(params, structure["geometry"],
                          structure["cell_size"],
                          structure["boundary_layers"], sources, k_point)
    pd_monitors = add_pd_flux_monitors(sim, params, coords, frequency)
    dft_fields = sim.add_dft_fields(
        [mp.Ez], frequency, 0, 1,
        center=mp.Vector3(0, (coords["y_min"] + coords["y_max"]) / 2.0),
        size=mp.Vector3(coords["cell_width_um"], coords["cell_height_um"]))
    run_simulation(sim, coords, frequency, progress_path, "structure",
                   start_time)

    pd_flux_per_pixel = [mp.get_fluxes(m)[0] for m in pd_monitors]
    efficiency_per_pixel = [flux / incident_flux_total
                            for flux in pd_flux_per_pixel]

    save_fields(sim, dft_fields, coords, job_dir)

    result = {
        "input": params,
        "warnings": warnings,
        "incident_flux_total": incident_flux_total,
        "pd_flux_per_pixel": pd_flux_per_pixel,
        "collection_efficiency_per_pixel": efficiency_per_pixel,
        "collection_efficiency_total": sum(efficiency_per_pixel),
        "polarization": "Ez（2D・s偏光）",
        "elapsed_seconds": round(time.time() - start_time, 1),
    }
    (job_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2))
    progress_path.write_text(json.dumps(
        {"status": "finished",
         "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))
    return result


def save_fields(sim, dft_fields, coords, job_dir):
    """|E|^2 分布と誘電率分布（構造の確認用）を fields.npz に保存する。"""
    ez = sim.get_dft_array(dft_fields, mp.Ez, 0)
    intensity = np.abs(ez) ** 2

    center = mp.Vector3(0, (coords["y_min"] + coords["y_max"]) / 2.0)
    size = mp.Vector3(coords["cell_width_um"], coords["cell_height_um"])
    epsilon = sim.get_array(center=center, size=size, component=mp.Dielectric)

    half_width = coords["cell_width_um"] / 2.0
    x_um = np.linspace(-half_width, half_width, intensity.shape[0])
    y_um = np.linspace(coords["y_min"], coords["y_max"], intensity.shape[1])
    np.savez_compressed(
        job_dir / "fields.npz",
        intensity=intensity, epsilon=epsilon, x_um=x_um, y_um=y_um,
        si_top_y=coords["si_top_y"], pd_monitor_y=coords["pd_monitor_y"])


def main():
    if len(sys.argv) != 2:
        print("使い方: python -m engine.fdtd_worker <job_dir>", file=sys.stderr)
        sys.exit(1)
    job_dir = Path(sys.argv[1])
    try:
        result = run_job(job_dir)
    except Exception as error:
        (job_dir / "progress.json").write_text(json.dumps(
            {"status": "failed", "error": str(error)}, ensure_ascii=False))
        raise
    print(json.dumps(
        {"collection_efficiency_per_pixel":
             result["collection_efficiency_per_pixel"]},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
