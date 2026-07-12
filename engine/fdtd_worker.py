"""FDTD計算ワーカー。

使い方（conda環境 cis-pixel-optics で実行）:
    python -m engine.fdtd_worker <job_dir>

job_dir/input.json を読み、以下を出力する。
    progress.json  進行状況（実行中に逐次更新）
    result.json    集光効率・クロストークなどの数値結果
    fields.npz     電場強度分布 |E|^2 と構造（誘電率）分布
    sweep.csv      スイープ実行時の集計表

集光効率は「参照計算（構造なし・空気のみ）の入射パワー」に対する
「PD面（Si内の指定深さの水平面）を通過するパワー」の比で定義する。
クロストーク評価では中央画素だけを照射し、周辺画素PDへの漏れ比率を求める。
"""

import cmath
import copy
import csv
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

# Si内波長あたりのセル数がこの値を下回ると精度警告を出す
MIN_CELLS_PER_WAVELENGTH_IN_SI = 10.0

# 3Dモードでボクセル数がこの値を超えると計算時間の警告を出す
HEAVY_3D_VOXEL_COUNT = 5.0e6

# 入力パラメータの許容範囲（requirements.md 4章）
PARAMETER_LIMITS = {
    "pixel_pitch_um": (0.5, 2.5),
    "ocl_height_um": (0.1, 1.5),
    "ocl_base_um": (0.0, 2.0),
    "wavelength_nm": (400.0, 700.0),
    "incident_angle_deg": (0.0, 35.0),
    "dti_width_um": (0.05, 0.3),
}

# スイープ可能なパラメータ（input.json内のドット区切りパス → 表示名）
SWEEP_PARAMETER_LABELS = {
    "source.incident_angle_deg": "入射角 [deg]",
    "source.wavelength_nm": "波長 [nm]",
    "pixel_pitch_um": "画素サイズ [µm]",
    "ocl.height_um": "OCL高さ [µm]",
    "ocl.superellipse_exponent": "スーパー楕円指数",
    "ocl.base_um": "OCLベース層厚 [µm]",
    "ocl.offset_um": "OCL偏心 [µm]",
    "dti.offset_um": "DTIオフセット [µm]",
    "layers.color_filter_um": "カラーフィルタ膜厚 [µm]",
}

DEFAULT_PARAMS = {
    "mode": "2d",         # 2d / 3d
    "crosstalk": False,   # True: 3×3画素（2Dは3画素）・中央照射
    "pixel_pitch_um": 1.0,
    "ocl": {
        "enabled": True,
        "height_um": 0.5,
        "shape": "spherical_cap",  # spherical_cap / superellipse
        "superellipse_exponent": 2.5,
        "sharing": "single",       # single / shared2 / shared4
        "offset_um": 0.0,  # レンズ中心の偏心（+X方向、PD・DTIは動かない）
        # レンズ底面の下に残る同一樹脂の平坦層（光路長の調整用）。
        # レンズ最低部からカラーフィルタまでの厚み = base_um + 平坦化膜厚
        "base_um": 0.0,
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
        "offset_um": 0.0,  # DTI格子の位置ずれ（+X方向、PD・OCLは動かない）
    },
    "source": {
        "wavelength_nm": 550.0,
        "incident_angle_deg": 0.0,
        "azimuth_deg": 0.0,  # 3Dの入射方位角（0=X方向へ傾ける）
    },
    "pd": {
        "top_depth_um": 0.5,  # Si上面からPD面までの深さ
    },
    "view": {
        "depth_um": None,  # 真上ビューの深さ（未指定ならPD面と同じ）
    },
    # Si内は波長が1/n（550 nmで約135 nm）に縮むため、空気基準ではなく
    # Si内波長を10セル以上で分解できる値にする（フレネル検証で確認済み）
    "resolution_pixels_per_um": 100,
    # スイープ指定（任意）: {"parameter": ドット区切りパス, "values": [数値...]}
    "sweep": None,
}


# ---- パラメータ処理 ----

def merge_defaults(user_params, defaults):
    """既定値辞書にユーザー入力を重ねる（入れ子辞書対応）。"""
    merged = {}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict):
            merged[key] = merge_defaults(user_params.get(key, {}), default_value)
        else:
            merged[key] = user_params.get(key, default_value)
    return merged


def set_nested_value(params, dotted_key, value):
    """ドット区切りパス（例 source.wavelength_nm）で値を書き込む。"""
    keys = dotted_key.split(".")
    target = params
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value


def validate_params(params):
    """パラメータの範囲チェック。問題があれば日本語メッセージで例外を出す。"""
    errors = []

    def check_range(value, key, label):
        low, high = PARAMETER_LIMITS[key]
        if not (low <= value <= high):
            errors.append(f"{label} が範囲外です: {value}（許容 {low}〜{high}）")

    if params["mode"] not in ("2d", "3d"):
        errors.append(f"未知の計算モードです: {params['mode']}")
    check_range(params["pixel_pitch_um"], "pixel_pitch_um", "画素サイズ [µm]")
    check_range(params["source"]["wavelength_nm"], "wavelength_nm", "波長 [nm]")
    check_range(params["source"]["incident_angle_deg"], "incident_angle_deg",
                "入射角 [deg]")
    if params["ocl"]["enabled"]:
        check_range(params["ocl"]["height_um"], "ocl_height_um", "OCL高さ [µm]")
        check_range(params["ocl"]["base_um"], "ocl_base_um",
                    "OCLベース層厚 [µm]")
        if params["ocl"]["shape"] not in ("spherical_cap", "superellipse"):
            errors.append(f"未知のレンズ形状です: {params['ocl']['shape']}")
        if params["ocl"]["sharing"] not in ("single", "shared2", "shared4"):
            errors.append(f"未知のOCL共有方式です: {params['ocl']['sharing']}")
    if (params["crosstalk"] and params["mode"] == "3d"
            and params["ocl"]["sharing"] != "single"):
        errors.append("3Dの受光内訳・クロストーク評価は1画素1レンズのみ対応しています")

    max_offset = params["pixel_pitch_um"] / 2.0
    for offset, label in ((params["ocl"]["offset_um"], "OCL偏心"),
                          (params["dti"]["offset_um"], "DTIオフセット")):
        if abs(offset) > max_offset:
            errors.append(
                f"{label} {offset} µm は画素サイズの半分"
                f"（±{max_offset} µm）以内にしてください")
        if offset != 0.0 and params["mode"] == "3d":
            errors.append(f"{label}は現在2Dモードのみ対応しています")
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

    sweep = params.get("sweep")
    if sweep:
        parameter = sweep.get("parameter")
        values = sweep.get("values")
        if parameter not in SWEEP_PARAMETER_LABELS:
            errors.append(f"スイープできないパラメータです: {parameter}")
        elif not values or not isinstance(values, list):
            errors.append("スイープの値リストが空です")
        else:
            # 各ケースを実際に組み立てて範囲チェックする
            for value in values:
                case_params = copy.deepcopy(params)
                case_params["sweep"] = None
                try:
                    set_nested_value(case_params, parameter, float(value))
                    validate_params(case_params)
                except ValueError as case_error:
                    errors.append(f"スイープ値 {value}: {case_error}")
                    break

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
    if params["mode"] == "2d" and params["ocl"]["sharing"] == "shared4":
        warnings.append(
            "shared4（4画素共有）の2Dモードは、2×2レンズ中央断面を"
            "2画素共有と同形状で近似しています。定量評価は3Dモードで行ってください")
    if params["mode"] == "3d":
        voxels = estimate_3d_voxel_count(params)
        if voxels > HEAVY_3D_VOXEL_COUNT:
            warnings.append(
                f"3Dモードの計算セルは約 {voxels / 1e6:.0f} Mボクセルです。"
                "計算に数十分以上かかる場合があります。まず解像度を下げて"
                "傾向を確認することを推奨します")
    return warnings


def estimate_3d_voxel_count(params):
    pitch = params["pixel_pitch_um"]
    nx, ny = structure_builder.UNIT_PIXELS_3D[params["ocl"]["sharing"]]
    if params["crosstalk"]:
        nx = ny = structure_builder.CROSSTALK_GRID_PIXELS
    heights = structure_builder.compute_stack_heights(params)
    cell_height = heights["top"] - heights["bottom"]
    resolution = params["resolution_pixels_per_um"]
    return (pitch * nx * resolution) * (pitch * ny * resolution) \
        * (cell_height * resolution)


# ---- 計算の部品 ----

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


def incident_wave_vector(params):
    """空気中（n=1）の横方向波数 (kx, ky) と周波数を返す。"""
    frequency = 1000.0 / params["source"]["wavelength_nm"]  # Meep単位（1/µm）
    theta_rad = math.radians(params["source"]["incident_angle_deg"])
    phi_rad = math.radians(params["source"]["azimuth_deg"])
    kx = frequency * math.sin(theta_rad) * math.cos(phi_rad)
    ky = frequency * math.sin(theta_rad) * math.sin(phi_rad)
    return kx, ky, frequency


def run_until_decayed(sim, decay_point, decay_component, progress_path,
                      phase_label, start_time):
    """電場が減衰するまで実行し、進行状況を progress.json に書き出す。"""

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
            FIELD_DECAY_CHECK_INTERVAL, decay_component, decay_point,
            FIELD_DECAY_THRESHOLD))


def crosstalk_summary(efficiency_per_pixel, center_indices):
    """中央（照射した共有単位）の効率合計と、周辺への漏れ合計を返す。"""
    center = sum(efficiency_per_pixel[i] for i in center_indices)
    neighbors_total = sum(efficiency_per_pixel) - center
    return center, neighbors_total


# ---- 2Dモード ----

def run_case_2d(params, case_dir, progress_path, phase_prefix, start_time):
    media = build_media(params)
    structure = structure_builder.build_structure_2d(params, media)
    coords = structure["coords"]
    kx, _, frequency = incident_wave_vector(params)

    def plane_wave_amplitude(position):
        return cmath.exp(2j * math.pi * kx * position.x)

    sources = [mp.Source(
        mp.GaussianSource(frequency,
                          fwidth=SOURCE_FRACTIONAL_BANDWIDTH * frequency),
        component=mp.Ez,  # 2DはEz偏光（s偏光）で評価する
        center=mp.Vector3(0, coords["source_y"]),
        size=mp.Vector3(coords["source_width_um"], 0),
        amp_func=plane_wave_amplitude,
    )]
    # クロストーク評価は有限幅の照射のため周期境界（Bloch）を使わない
    k_point = False if params["crosstalk"] else mp.Vector3(kx, 0, 0)

    def make_simulation(geometry):
        return mp.Simulation(
            cell_size=structure["cell_size"],
            geometry=geometry,
            boundary_layers=structure["boundary_layers"],
            sources=sources,
            k_point=k_point,
            resolution=params["resolution_pixels_per_um"],
            force_complex_fields=(kx != 0.0))

    def add_pd_monitors(sim):
        pitch = params["pixel_pitch_um"]
        monitors = []
        for center_x in coords["pixel_centers_x"]:
            region = mp.FluxRegion(
                center=mp.Vector3(center_x, coords["pd_monitor_y"]),
                size=mp.Vector3(pitch, 0))
            monitors.append(sim.add_flux(frequency, 0, 1, region))
        return monitors

    decay_point = mp.Vector3(0, coords["pd_monitor_y"])

    # 1回目: 参照計算（構造なし・空気のみ）で入射パワーを測る
    reference_sim = make_simulation([])
    reference_monitors = add_pd_monitors(reference_sim)
    run_until_decayed(reference_sim, decay_point, mp.Ez, progress_path,
                      f"{phase_prefix}reference", start_time)
    incident_flux_total = sum(mp.get_fluxes(m)[0]
                              for m in reference_monitors)
    reference_sim.reset_meep()

    # 2回目: 本計算（画素構造あり）
    sim = make_simulation(structure["geometry"])
    pd_monitors = add_pd_monitors(sim)
    field_center = mp.Vector3(0, (coords["y_min"] + coords["y_max"]) / 2.0)
    field_size = mp.Vector3(coords["cell_width_um"], coords["cell_height_um"])
    dft_fields = sim.add_dft_fields([mp.Ez], frequency, 0, 1,
                                    center=field_center, size=field_size)
    run_until_decayed(sim, decay_point, mp.Ez, progress_path,
                      f"{phase_prefix}structure", start_time)

    pd_flux_per_pixel = [mp.get_fluxes(m)[0] for m in pd_monitors]
    efficiency_per_pixel = [flux / incident_flux_total
                            for flux in pd_flux_per_pixel]

    intensity = np.abs(sim.get_dft_array(dft_fields, mp.Ez, 0)) ** 2
    epsilon = sim.get_array(center=field_center, size=field_size,
                            component=mp.Dielectric)
    half_width = coords["cell_width_um"] / 2.0
    np.savez_compressed(
        case_dir / "fields.npz",
        intensity=intensity, epsilon=epsilon,
        x_um=np.linspace(-half_width, half_width, intensity.shape[0]),
        y_um=np.linspace(coords["y_min"], coords["y_max"],
                         intensity.shape[1]),
        si_top_y=coords["si_top_y"], pd_monitor_y=coords["pd_monitor_y"],
        ar_top_y=coords["ar_top_y"], cf_top_y=coords["cf_top_y"],
        planarization_top_y=coords["planarization_top_y"])

    result = {
        "incident_flux_total": incident_flux_total,
        "pd_flux_per_pixel": pd_flux_per_pixel,
        "collection_efficiency_per_pixel": efficiency_per_pixel,
        "collection_efficiency_total": sum(efficiency_per_pixel),
        "polarization": "Ez（2D・s偏光）",
    }
    if params["crosstalk"]:
        # 中央の共有レンズ単位に属する画素（例: 6画素なら中央の2画素）
        unit_pixels = coords["unit_pixels"]
        center_indices = list(range(unit_pixels, 2 * unit_pixels))
        center, neighbors_total = crosstalk_summary(efficiency_per_pixel,
                                                    center_indices)
        result.update({
            "unit_pixels": unit_pixels,
            "center_pixel_indices": center_indices,
            "collection_efficiency_center": center,
            "crosstalk_total": neighbors_total,
        })
    return result


# ---- 3Dモード ----

def run_case_3d(params, case_dir, progress_path, phase_prefix, start_time):
    media = build_media(params)
    structure = structure_builder.build_structure_3d(params, media)
    coords = structure["coords"]
    kx, ky, frequency = incident_wave_vector(params)

    def plane_wave_amplitude(position):
        return cmath.exp(2j * math.pi * (kx * position.x + ky * position.y))

    sources = [mp.Source(
        mp.GaussianSource(frequency,
                          fwidth=SOURCE_FRACTIONAL_BANDWIDTH * frequency),
        component=mp.Ex,  # 3DはX方向偏光の平面波で評価する
        center=mp.Vector3(0, 0, coords["source_z"]),
        size=mp.Vector3(coords["source_width_x_um"],
                        coords["source_width_y_um"], 0),
        amp_func=plane_wave_amplitude,
    )]
    oblique = (kx != 0.0 or ky != 0.0)
    k_point = False if params["crosstalk"] else mp.Vector3(kx, ky, 0)

    def make_simulation(geometry):
        return mp.Simulation(
            cell_size=structure["cell_size"],
            geometry=geometry,
            boundary_layers=structure["boundary_layers"],
            sources=sources,
            k_point=k_point,
            resolution=params["resolution_pixels_per_um"],
            force_complex_fields=oblique)

    def add_pd_monitors(sim):
        pitch = params["pixel_pitch_um"]
        monitors = []
        for center_x, center_y in coords["pixel_centers_xy"]:
            region = mp.FluxRegion(
                center=mp.Vector3(center_x, center_y,
                                  coords["pd_monitor_z"]),
                size=mp.Vector3(pitch, pitch, 0))
            monitors.append(sim.add_flux(frequency, 0, 1, region))
        return monitors

    decay_point = mp.Vector3(0, 0, coords["pd_monitor_z"])
    field_components = [mp.Ex, mp.Ey, mp.Ez]

    # 断面（XZ、y=0）と真上ビュー（XY、指定深さ）の2枚のDFTモニター
    z_mid = (coords["z_min"] + coords["z_max"]) / 2.0
    xz_center = mp.Vector3(0, 0, z_mid)
    xz_size = mp.Vector3(coords["cell_x_um"], 0, coords["cell_height_um"])
    view_depth = params["view"]["depth_um"]
    if view_depth is None:
        view_depth = params["pd"]["top_depth_um"]
    view_z = coords["si_top_z"] - view_depth
    xy_center = mp.Vector3(0, 0, view_z)
    xy_size = mp.Vector3(coords["cell_x_um"], coords["cell_y_um"], 0)

    # 1回目: 参照計算（構造なし・空気のみ）
    reference_sim = make_simulation([])
    reference_monitors = add_pd_monitors(reference_sim)
    run_until_decayed(reference_sim, decay_point, mp.Ex, progress_path,
                      f"{phase_prefix}reference", start_time)
    incident_flux_total = sum(mp.get_fluxes(m)[0]
                              for m in reference_monitors)
    reference_sim.reset_meep()

    # 2回目: 本計算（画素構造あり）
    sim = make_simulation(structure["geometry"])
    pd_monitors = add_pd_monitors(sim)
    dft_xz = sim.add_dft_fields(field_components, frequency, 0, 1,
                                center=xz_center, size=xz_size)
    dft_xy = sim.add_dft_fields(field_components, frequency, 0, 1,
                                center=xy_center, size=xy_size)
    run_until_decayed(sim, decay_point, mp.Ex, progress_path,
                      f"{phase_prefix}structure", start_time)

    pd_flux_per_pixel = [mp.get_fluxes(m)[0] for m in pd_monitors]
    efficiency_per_pixel = [flux / incident_flux_total
                            for flux in pd_flux_per_pixel]

    def total_intensity(dft_object):
        return sum(np.abs(sim.get_dft_array(dft_object, component, 0)) ** 2
                   for component in field_components)

    intensity_xz = total_intensity(dft_xz)
    intensity_xy = total_intensity(dft_xy)
    epsilon_xz = sim.get_array(center=xz_center, size=xz_size,
                               component=mp.Dielectric)
    epsilon_xy = sim.get_array(center=xy_center, size=xy_size,
                               component=mp.Dielectric)

    half_x = coords["cell_x_um"] / 2.0
    half_y = coords["cell_y_um"] / 2.0
    # 断面は2Dモードと同じキー名で保存し、描画処理を共用する
    np.savez_compressed(
        case_dir / "fields.npz",
        intensity=intensity_xz, epsilon=epsilon_xz,
        x_um=np.linspace(-half_x, half_x, intensity_xz.shape[0]),
        y_um=np.linspace(coords["z_min"], coords["z_max"],
                         intensity_xz.shape[1]),
        si_top_y=coords["si_top_z"], pd_monitor_y=coords["pd_monitor_z"],
        ar_top_y=coords["ar_top_z"], cf_top_y=coords["cf_top_z"],
        planarization_top_y=coords["planarization_top_z"],
        intensity_xy=intensity_xy, epsilon_xy=epsilon_xy,
        xy_x_um=np.linspace(-half_x, half_x, intensity_xy.shape[0]),
        xy_y_um=np.linspace(-half_y, half_y, intensity_xy.shape[1]),
        view_depth_um=view_depth)

    result = {
        "incident_flux_total": incident_flux_total,
        "pd_flux_per_pixel": pd_flux_per_pixel,
        "collection_efficiency_per_pixel": efficiency_per_pixel,
        "collection_efficiency_total": sum(efficiency_per_pixel),
        "polarization": "Ex（3D・X偏光）",
        "num_pixels_x": coords["num_pixels_x"],
        "num_pixels_y": coords["num_pixels_y"],
        "view_depth_um": view_depth,
    }
    if params["crosstalk"]:
        center_indices = [len(efficiency_per_pixel) // 2]
        center, neighbors_total = crosstalk_summary(efficiency_per_pixel,
                                                    center_indices)
        result.update({
            "unit_pixels": 1,
            "center_pixel_indices": center_indices,
            "collection_efficiency_center": center,
            "crosstalk_total": neighbors_total,
        })
    return result


# ---- ジョブ実行 ----

def run_single_case(params, case_dir, progress_path, phase_prefix,
                    start_time):
    case_dir.mkdir(parents=True, exist_ok=True)
    if params["mode"] == "3d":
        return run_case_3d(params, case_dir, progress_path, phase_prefix,
                           start_time)
    return run_case_2d(params, case_dir, progress_path, phase_prefix,
                       start_time)


def run_sweep(params, job_dir, progress_path, start_time):
    """スイープ実行: 値ごとに計算し、一覧とCSVを出力する。"""
    parameter = params["sweep"]["parameter"]
    values = [float(v) for v in params["sweep"]["values"]]
    label = SWEEP_PARAMETER_LABELS[parameter]

    sweep_results = []
    for index, value in enumerate(values):
        case_params = copy.deepcopy(params)
        case_params["sweep"] = None
        set_nested_value(case_params, parameter, value)
        phase_prefix = f"sweep {index + 1}/{len(values)} ({label}={value}) "
        case_result = run_single_case(
            case_params, job_dir / f"case_{index:02d}", progress_path,
            phase_prefix, start_time)
        entry = {
            "value": value,
            "collection_efficiency_total":
                case_result["collection_efficiency_total"],
            "collection_efficiency_per_pixel":
                case_result["collection_efficiency_per_pixel"],
        }
        if params["crosstalk"]:
            entry["crosstalk_total"] = case_result["crosstalk_total"]
            entry["collection_efficiency_center"] = \
                case_result["collection_efficiency_center"]
        sweep_results.append(entry)

    write_sweep_csv(job_dir / "sweep.csv", label, sweep_results,
                    params["crosstalk"])
    return {
        "parameter": parameter,
        "label": label,
        "values": values,
        "results": sweep_results,
    }


def write_sweep_csv(csv_path, label, sweep_results, crosstalk):
    num_pixels = len(sweep_results[0]["collection_efficiency_per_pixel"])
    header = [label, "collection_efficiency_total"]
    header += [f"efficiency_pixel_{i + 1}" for i in range(num_pixels)]
    if crosstalk:
        header += ["collection_efficiency_center", "crosstalk_total"]
    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for entry in sweep_results:
            row = [entry["value"], entry["collection_efficiency_total"]]
            row += entry["collection_efficiency_per_pixel"]
            if crosstalk:
                row += [entry["collection_efficiency_center"],
                        entry["crosstalk_total"]]
            writer.writerow(row)


def run_job(job_dir):
    """input.json を読んでFDTDを実行し、結果一式を書き出す。"""
    job_dir = Path(job_dir)
    progress_path = job_dir / "progress.json"
    start_time = time.time()

    user_params = json.loads((job_dir / "input.json").read_text())
    params = merge_defaults(user_params, DEFAULT_PARAMS)
    validate_params(params)
    warnings = collect_warnings(params)

    result = {
        "input": params,
        "warnings": warnings,
    }
    if params["sweep"]:
        result["type"] = "sweep"
        result["sweep"] = run_sweep(params, job_dir, progress_path,
                                    start_time)
    else:
        result["type"] = "single"
        result.update(run_single_case(params, job_dir, progress_path, "",
                                      start_time))

    result["elapsed_seconds"] = round(time.time() - start_time, 1)
    (job_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2))
    progress_path.write_text(json.dumps(
        {"status": "finished",
         "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))
    return result


def compute_epsilon_preview(user_params):
    """FDTDを実行せずに構造（誘電率分布）だけを計算する（プレビュー用）。

    3Dモードでも断面（XZ）のプレビューを返す。
    戻り値: (epsilon2次元配列, 横座標, 縦座標, 層境界の辞書)
    層境界の辞書はセル座標の si_top / ar_top / cf_top を持つ。
    """
    params = merge_defaults(user_params, DEFAULT_PARAMS)
    validate_params(params)
    media = build_media(params)

    if params["mode"] == "3d":
        structure = structure_builder.build_structure_3d(params, media)
        coords = structure["coords"]
        center = mp.Vector3(0, 0, (coords["z_min"] + coords["z_max"]) / 2.0)
        size = mp.Vector3(coords["cell_x_um"], 0, coords["cell_height_um"])
        lateral_um = coords["cell_x_um"]
        vertical_range = (coords["z_min"], coords["z_max"])
        layer_info = {"si_top": coords["si_top_z"],
                      "ar_top": coords["ar_top_z"],
                      "cf_top": coords["cf_top_z"]}
    else:
        structure = structure_builder.build_structure_2d(params, media)
        coords = structure["coords"]
        center = mp.Vector3(0, (coords["y_min"] + coords["y_max"]) / 2.0)
        size = mp.Vector3(coords["cell_width_um"], coords["cell_height_um"])
        lateral_um = coords["cell_width_um"]
        vertical_range = (coords["y_min"], coords["y_max"])
        layer_info = {"si_top": coords["si_top_y"],
                      "ar_top": coords["ar_top_y"],
                      "cf_top": coords["cf_top_y"]}

    sim = mp.Simulation(
        cell_size=structure["cell_size"],
        geometry=structure["geometry"],
        boundary_layers=structure["boundary_layers"],
        resolution=params["resolution_pixels_per_um"])
    sim.init_sim()
    epsilon = sim.get_array(center=center, size=size,
                            component=mp.Dielectric)
    x_um = np.linspace(-lateral_um / 2.0, lateral_um / 2.0, epsilon.shape[0])
    y_um = np.linspace(vertical_range[0], vertical_range[1],
                       epsilon.shape[1])
    return epsilon, x_um, y_um, layer_info


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
    summary = {"type": result["type"]}
    if result["type"] == "single":
        summary["collection_efficiency_total"] = \
            result["collection_efficiency_total"]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
