"""画素構造（積層・OCL・DTI）のMeepジオメトリ生成。

- 2Dモード: 画素のXZ断面を、Meepの2D座標 (x=横方向, y=深さ方向) で表す
- 3Dモード: Meepの3D座標 (x, y=横方向, z=深さ方向) で表す

座標単位はMeepの慣例に従い µm 基準（a = 1 µm）とする。
物理座標は「Siの上面（受光面）を 0」として組み立て、
最後にMeepのセル中心座標へ平行移動する。
"""

import math

import meep as mp

# 計算領域の余白（構造の外側に確保する空間）
PML_THICKNESS_UM = 0.6      # 吸収境界の厚さ
AIR_GAP_ABOVE_OCL_UM = 0.4  # OCL頂点と光源面の間の空気層
SOURCE_TO_PML_GAP_UM = 0.3  # 光源面と上側PMLの間隔

# レンズ形状の分割数（滑らかさとメッシュ精度のバランス）
LENS_POLYGON_POINTS = 128    # 2D断面ポリゴン
LENS_CONTOUR_POINTS_3D = 48  # 3Dレンズ輪郭（楕円）ポリゴン
NUM_LENS_SLICES_3D = 20      # 3Dレンズの高さ方向スライス数

# クロストーク評価（中央画素のみ照射）の設定
CROSSTALK_GRID_PIXELS = 3         # 3×3画素（2Dでは3画素）
CROSSTALK_LATERAL_MARGIN_UM = 0.5  # 最外画素と横方向PMLの間の余白

# 1セルに含める画素数（OCL共有方式ごと）
NUM_PIXELS_2D = {"single": 1, "shared2": 2, "shared4": 2}
UNIT_PIXELS_3D = {"single": (1, 1), "shared2": (2, 1), "shared4": (2, 2)}


# ---- 共通ヘルパー ----

def compute_stack_heights(params):
    """物理座標（Si上面 = 0）で各層の境界と計算領域の上下端を返す。

    OCLベース層は、レンズ底面の下に平らに残るレンズと同じ樹脂の層
    （光路長の調整用）。レンズ最低部からカラーフィルタまでの厚みは
    「ベース層 + 平坦化膜」になる。
    """
    layers = params["layers"]
    if params["ocl"]["enabled"]:
        ocl_height = params["ocl"]["height_um"]
        ocl_base = params["ocl"]["base_um"]
    else:
        ocl_height = 0.0
        ocl_base = 0.0
    ar_top = layers["ar_um"]
    cf_top = ar_top + layers["color_filter_um"]
    planarization_top = cf_top + layers["planarization_um"]
    lens_base = planarization_top + ocl_base
    source_height = lens_base + ocl_height + AIR_GAP_ABOVE_OCL_UM
    top = source_height + SOURCE_TO_PML_GAP_UM + PML_THICKNESS_UM
    # Siは下側PMLの中まで満たし、裏面反射のない半無限基板として扱う
    bottom = -layers["si_um"] - PML_THICKNESS_UM
    return {
        "ar_top": ar_top,
        "cf_top": cf_top,
        "planarization_top": planarization_top,
        "lens_base": lens_base,
        "source_height": source_height,
        "top": top,
        "bottom": bottom,
    }


def lens_profile_height_um(x_local_um, half_width_um, height_um, shape,
                           superellipse_exponent):
    """レンズ底面から測った高さを返す。x_local_um はレンズ中心からの横位置。"""
    if abs(x_local_um) >= half_width_um:
        return 0.0
    if shape == "spherical_cap":
        # 幅2w・高さhの球面キャップの曲率半径: R = (w^2 + h^2) / (2h)
        radius = (half_width_um ** 2 + height_um ** 2) / (2.0 * height_um)
        return math.sqrt(radius ** 2 - x_local_um ** 2) - (radius - height_um)
    if shape == "superellipse":
        p = superellipse_exponent
        t = abs(x_local_um) / half_width_um
        return height_um * (1.0 - t ** p) ** (1.0 / p)
    raise ValueError(f"未知のレンズ形状です: {shape}")


def lens_profile_height_decentered(x_local_um, half_width_um, height_um,
                                   shape, superellipse_exponent,
                                   apex_offset_um):
    """頂点位置を横にずらした非対称レンズの高さを返す（偏心レンズ）。

    フットプリント（底面の両端 ±half_width）は固定のまま、頂点だけが
    apex_offset_um の位置に動く。頂点の左右をそれぞれ別の半幅を持つ
    プロファイルでつなぐため、片側の裾が緩く、反対側が急になる。
    """
    if x_local_um >= apex_offset_um:
        side_half_width = half_width_um - apex_offset_um
    else:
        side_half_width = half_width_um + apex_offset_um
    if side_half_width <= 0.0:
        return 0.0
    return lens_profile_height_um(x_local_um - apex_offset_um,
                                  side_half_width, height_um, shape,
                                  superellipse_exponent)


def lens_contour_scale(z_from_base_um, half_width_um, height_um, shape,
                       superellipse_exponent):
    """高さzでのレンズ輪郭の縮小率（0〜1）を返す（3Dスライス生成用）。

    プロファイル関数の逆関数。輪郭の半幅 = half_width × 縮小率。
    """
    if z_from_base_um >= height_um:
        return 0.0
    if shape == "spherical_cap":
        radius = (half_width_um ** 2 + height_um ** 2) / (2.0 * height_um)
        r = math.sqrt(max(
            radius ** 2 - (z_from_base_um + radius - height_um) ** 2, 0.0))
        return min(r / half_width_um, 1.0)
    if shape == "superellipse":
        p = superellipse_exponent
        return (1.0 - (z_from_base_um / height_um) ** p) ** (1.0 / p)
    raise ValueError(f"未知のレンズ形状です: {shape}")


def pixel_boundary_positions_um(pixel_pitch_um, num_pixels, placement,
                                sharing, unit_pixels=None,
                                center_offset_um=0.0):
    """DTIを置く画素境界の位置リストを返す（1軸分、セル中心を0とする）。

    「共有単位境界のみ」では、共有レンズ単位（unit_pixels画素）ごとの
    境界にだけ溝を置く。
    """
    if unit_pixels is None:
        unit_pixels = num_pixels
    half_extent = pixel_pitch_um * num_pixels / 2.0
    positions = []
    for i in range(num_pixels + 1):
        x = center_offset_um - half_extent + i * pixel_pitch_um
        is_shared_unit_boundary = (i % unit_pixels == 0)
        if placement == "shared_only" and sharing != "single":
            if not is_shared_unit_boundary:
                continue
        positions.append(x)
    return positions


# ---- 2Dモード ----

def build_lens_prism_2d(center_x_um, base_y_um, half_width_um, height_um,
                        shape, superellipse_exponent, medium,
                        apex_offset_um=0.0):
    """レンズ断面ポリゴンからMeepのPrism（2D多角形）を作る。

    apex_offset_um は偏心量（フットプリント固定のまま頂点位置をずらす）。
    """
    vertices = []
    for i in range(LENS_POLYGON_POINTS + 1):
        x_local = -half_width_um + (2.0 * half_width_um) * i / LENS_POLYGON_POINTS
        y = lens_profile_height_decentered(
            x_local, half_width_um, height_um, shape,
            superellipse_exponent, apex_offset_um)
        vertices.append(mp.Vector3(center_x_um + x_local, base_y_um + y, 0))
    # 底面の直線で閉じる（終点→始点は自動で結ばれる）
    return mp.Prism(vertices, height=mp.inf, axis=mp.Vector3(0, 0, 1),
                    material=medium)


def lens_layout_2d(params, crosstalk):
    """2Dモードのレンズ中心位置と半幅、画素数を返す。

    レンズのフットプリント（底面の範囲）は画素に固定する。
    偏心（ocl.offset_um）は頂点位置のずれとしてプリズム生成時に反映する。
    """
    pitch = params["pixel_pitch_um"]
    sharing = params["ocl"]["sharing"]
    if crosstalk:
        # 受光内訳・クロストーク評価は共有レンズ単位×3（中央単位のみ照射）。
        # 1画素1レンズなら3画素、2画素/4画素共有なら6画素のセルになる
        unit_pixels = NUM_PIXELS_2D[sharing]
        unit_width = pitch * unit_pixels
        num_pixels = CROSSTALK_GRID_PIXELS * unit_pixels
        lens_centers = [unit_width * (i - (CROSSTALK_GRID_PIXELS - 1) / 2.0)
                        for i in range(CROSSTALK_GRID_PIXELS)]
        lens_half_width = unit_width / 2.0
        return num_pixels, lens_centers, lens_half_width
    num_pixels = NUM_PIXELS_2D[sharing]
    if sharing == "single":
        lens_centers = [pitch * (i - (num_pixels - 1) / 2.0)
                        for i in range(num_pixels)]
        lens_half_width = pitch / 2.0
    else:
        # shared2/shared4は2画素分の幅を持つ1枚のレンズ
        # （shared4の2Dモードは2×2レンズ中央断面の近似）
        lens_centers = [0.0]
        lens_half_width = pitch
    return num_pixels, lens_centers, lens_half_width


def build_structure_2d(params, media):
    """2Dモードの計算セル・ジオメトリ・主要座標をまとめて返す。

    戻り値の座標はすべてMeepセル座標（セル中心が原点）。
    """
    pitch = params["pixel_pitch_um"]
    sharing = params["ocl"]["sharing"]
    crosstalk = params["crosstalk"]
    heights = compute_stack_heights(params)

    num_pixels, lens_centers, lens_half_width = lens_layout_2d(params,
                                                               crosstalk)
    pixels_width = pitch * num_pixels
    if crosstalk:
        # 中央照射のためBloch周期は使えず、横方向もPMLで閉じる
        lateral_extra = CROSSTALK_LATERAL_MARGIN_UM + PML_THICKNESS_UM
        cell_width = pixels_width + 2.0 * lateral_extra
        boundary_layers = [mp.PML(PML_THICKNESS_UM)]
    else:
        cell_width = pixels_width
        boundary_layers = [mp.PML(PML_THICKNESS_UM, direction=mp.Y)]

    cell_height = heights["top"] - heights["bottom"]
    y_offset = (heights["top"] + heights["bottom"]) / 2.0

    def to_cell_y(physical_y):
        return physical_y - y_offset

    geometry = []

    # 積層（下から）。厚さ0の層はスキップする
    si_block_height = params["layers"]["si_um"] + PML_THICKNESS_UM
    geometry.append(mp.Block(
        size=mp.Vector3(mp.inf, si_block_height, mp.inf),
        center=mp.Vector3(0, to_cell_y(-si_block_height / 2.0)),
        material=media["silicon"]))

    layer_stack = [
        ("ar", 0.0, heights["ar_top"]),
        ("color_filter", heights["ar_top"], heights["cf_top"]),
        ("planarization", heights["cf_top"], heights["planarization_top"]),
        # OCLベース層（レンズと同じ樹脂。光路長の調整用）
        ("ocl", heights["planarization_top"], heights["lens_base"]),
    ]
    for name, layer_bottom, layer_top in layer_stack:
        thickness = layer_top - layer_bottom
        if thickness <= 0.0:
            continue
        geometry.append(mp.Block(
            size=mp.Vector3(mp.inf, thickness, mp.inf),
            center=mp.Vector3(0, to_cell_y((layer_bottom + layer_top) / 2.0)),
            material=media[name]))

    # 周期境界ではセル外にはみ出した形状の周期像が自動では作られないため、
    # DTIオフセット指定時は±セル幅にずらした複製を置いて周期像を再現する。
    # （OCL偏心はフットプリント固定でセルからはみ出さないため複製不要）
    if not crosstalk and params["dti"]["offset_um"] != 0.0:
        wrap_shifts = [-cell_width, 0.0, cell_width]
    else:
        wrap_shifts = [0.0]

    if params["ocl"]["enabled"]:
        for center_x in lens_centers:
            geometry.append(build_lens_prism_2d(
                center_x, to_cell_y(heights["lens_base"]),
                lens_half_width, params["ocl"]["height_um"],
                params["ocl"]["shape"],
                params["ocl"]["superellipse_exponent"], media["ocl"],
                apex_offset_um=params["ocl"]["offset_um"]))

    # DTI: Si上面から指定深さまでの縦溝。Siより後に置いてSiを上書きする
    # 位置オフセット（dti.offset_um）でDTI格子だけを横にずらせる
    dti = params["dti"]
    unit_pixels = NUM_PIXELS_2D[sharing]
    if dti["enabled"] and dti["depth_um"] > 0.0:
        boundary_xs = pixel_boundary_positions_um(
            pitch, num_pixels, dti["placement"], sharing,
            unit_pixels=unit_pixels)
        for x in boundary_xs:
            for shift in wrap_shifts:
                geometry.append(mp.Block(
                    size=mp.Vector3(dti["width_um"], dti["depth_um"], mp.inf),
                    center=mp.Vector3(x + dti["offset_um"] + shift,
                                      to_cell_y(-dti["depth_um"] / 2.0)),
                    material=media["dti"]))

    pixel_centers = [pitch * (i - (num_pixels - 1) / 2.0)
                     for i in range(num_pixels)]
    coords = {
        "cell_width_um": cell_width,
        "cell_height_um": cell_height,
        "num_pixels": num_pixels,
        "unit_pixels": unit_pixels,
        "pixel_centers_x": pixel_centers,
        # クロストーク評価では中央の共有レンズ単位の幅だけを照射する
        "source_width_um": (pitch * unit_pixels if crosstalk
                            else cell_width),
        "source_y": to_cell_y(heights["source_height"]),
        "si_top_y": to_cell_y(0.0),
        "pd_monitor_y": to_cell_y(-params["pd"]["top_depth_um"]),
        # 断面図で層を塗り分けるための境界座標（セル座標）
        "ar_top_y": to_cell_y(heights["ar_top"]),
        "cf_top_y": to_cell_y(heights["cf_top"]),
        "planarization_top_y": to_cell_y(heights["planarization_top"]),
        "y_min": to_cell_y(heights["bottom"]),
        "y_max": to_cell_y(heights["top"]),
    }
    return {
        "cell_size": mp.Vector3(cell_width, cell_height),
        "geometry": geometry,
        "boundary_layers": boundary_layers,
        "coords": coords,
    }


# ---- 3Dモード ----

def build_lens_slices_3d(center_x, center_y, base_z, half_width_x,
                         half_width_y, height, shape, superellipse_exponent,
                         medium):
    """3Dレンズを高さ方向のスライス（楕円柱Prism）の積み重ねで近似する。"""
    slices = []
    slice_thickness = height / NUM_LENS_SLICES_3D
    for j in range(NUM_LENS_SLICES_3D):
        z_center_of_slice = (j + 0.5) * slice_thickness
        scale = lens_contour_scale(z_center_of_slice, half_width_x, height,
                                   shape, superellipse_exponent)
        if scale <= 0.0:
            break
        ax = half_width_x * scale
        ay = half_width_y * scale
        vertices = []
        for i in range(LENS_CONTOUR_POINTS_3D):
            angle = 2.0 * math.pi * i / LENS_CONTOUR_POINTS_3D
            vertices.append(mp.Vector3(center_x + ax * math.cos(angle),
                                       center_y + ay * math.sin(angle),
                                       base_z + j * slice_thickness))
        slices.append(mp.Prism(vertices, height=slice_thickness,
                               axis=mp.Vector3(0, 0, 1), material=medium))
    return slices


def lens_layout_3d(params):
    """3Dモードのレンズ中心 (x, y) と半幅 (x, y)、画素数 (nx, ny) を返す。"""
    pitch = params["pixel_pitch_um"]
    sharing = params["ocl"]["sharing"]
    if params["crosstalk"]:
        n = CROSSTALK_GRID_PIXELS
        centers = [(pitch * (ix - (n - 1) / 2.0), pitch * (iy - (n - 1) / 2.0))
                   for ix in range(n) for iy in range(n)]
        return (n, n), centers, (pitch / 2.0, pitch / 2.0)
    nx, ny = UNIT_PIXELS_3D[sharing]
    if sharing == "single":
        centers = [(0.0, 0.0)]
        half_widths = (pitch / 2.0, pitch / 2.0)
    else:
        # 共有OCLは共有単位全体を覆う1枚のレンズ
        centers = [(0.0, 0.0)]
        half_widths = (pitch * nx / 2.0, pitch * ny / 2.0)
    return (nx, ny), centers, half_widths


def build_structure_3d(params, media):
    """3Dモードの計算セル・ジオメトリ・主要座標をまとめて返す。"""
    pitch = params["pixel_pitch_um"]
    sharing = params["ocl"]["sharing"]
    crosstalk = params["crosstalk"]
    heights = compute_stack_heights(params)

    (num_x, num_y), lens_centers, lens_half_widths = lens_layout_3d(params)
    pixels_width_x = pitch * num_x
    pixels_width_y = pitch * num_y
    if crosstalk:
        lateral_extra = CROSSTALK_LATERAL_MARGIN_UM + PML_THICKNESS_UM
        cell_x = pixels_width_x + 2.0 * lateral_extra
        cell_y = pixels_width_y + 2.0 * lateral_extra
        boundary_layers = [mp.PML(PML_THICKNESS_UM)]
    else:
        cell_x = pixels_width_x
        cell_y = pixels_width_y
        boundary_layers = [mp.PML(PML_THICKNESS_UM, direction=mp.Z)]

    cell_height = heights["top"] - heights["bottom"]
    z_offset = (heights["top"] + heights["bottom"]) / 2.0

    def to_cell_z(physical_z):
        return physical_z - z_offset

    geometry = []

    si_block_height = params["layers"]["si_um"] + PML_THICKNESS_UM
    geometry.append(mp.Block(
        size=mp.Vector3(mp.inf, mp.inf, si_block_height),
        center=mp.Vector3(0, 0, to_cell_z(-si_block_height / 2.0)),
        material=media["silicon"]))

    layer_stack = [
        ("ar", 0.0, heights["ar_top"]),
        ("color_filter", heights["ar_top"], heights["cf_top"]),
        ("planarization", heights["cf_top"], heights["planarization_top"]),
        # OCLベース層（レンズと同じ樹脂。光路長の調整用）
        ("ocl", heights["planarization_top"], heights["lens_base"]),
    ]
    for name, layer_bottom, layer_top in layer_stack:
        thickness = layer_top - layer_bottom
        if thickness <= 0.0:
            continue
        geometry.append(mp.Block(
            size=mp.Vector3(mp.inf, mp.inf, thickness),
            center=mp.Vector3(0, 0,
                              to_cell_z((layer_bottom + layer_top) / 2.0)),
            material=media[name]))

    if params["ocl"]["enabled"]:
        for center_x, center_y in lens_centers:
            geometry.extend(build_lens_slices_3d(
                center_x, center_y, to_cell_z(heights["lens_base"]),
                lens_half_widths[0], lens_half_widths[1],
                params["ocl"]["height_um"], params["ocl"]["shape"],
                params["ocl"]["superellipse_exponent"], media["ocl"]))

    # DTI: 画素境界に沿った格子状の溝（X方向・Y方向の壁の組み合わせ）
    dti = params["dti"]
    unit_x, unit_y = UNIT_PIXELS_3D[sharing]
    if dti["enabled"] and dti["depth_um"] > 0.0:
        trench_center_z = to_cell_z(-dti["depth_um"] / 2.0)
        for x in pixel_boundary_positions_um(pitch, num_x, dti["placement"],
                                             sharing, unit_pixels=unit_x):
            geometry.append(mp.Block(
                size=mp.Vector3(dti["width_um"], pixels_width_y,
                                dti["depth_um"]),
                center=mp.Vector3(x, 0, trench_center_z),
                material=media["dti"]))
        for y in pixel_boundary_positions_um(pitch, num_y, dti["placement"],
                                             sharing, unit_pixels=unit_y):
            geometry.append(mp.Block(
                size=mp.Vector3(pixels_width_x, dti["width_um"],
                                dti["depth_um"]),
                center=mp.Vector3(0, y, trench_center_z),
                material=media["dti"]))

    pixel_centers = [
        (pitch * (ix - (num_x - 1) / 2.0), pitch * (iy - (num_y - 1) / 2.0))
        for iy in range(num_y) for ix in range(num_x)]
    coords = {
        "cell_x_um": cell_x,
        "cell_y_um": cell_y,
        "cell_height_um": cell_height,
        "num_pixels_x": num_x,
        "num_pixels_y": num_y,
        "pixel_centers_xy": pixel_centers,
        "source_width_x_um": pitch if crosstalk else cell_x,
        "source_width_y_um": pitch if crosstalk else cell_y,
        "source_z": to_cell_z(heights["source_height"]),
        "si_top_z": to_cell_z(0.0),
        "pd_monitor_z": to_cell_z(-params["pd"]["top_depth_um"]),
        # 断面図で層を塗り分けるための境界座標（セル座標）
        "ar_top_z": to_cell_z(heights["ar_top"]),
        "cf_top_z": to_cell_z(heights["cf_top"]),
        "planarization_top_z": to_cell_z(heights["planarization_top"]),
        "z_min": to_cell_z(heights["bottom"]),
        "z_max": to_cell_z(heights["top"]),
    }
    return {
        "cell_size": mp.Vector3(cell_x, cell_y, cell_height),
        "geometry": geometry,
        "boundary_layers": boundary_layers,
        "coords": coords,
    }
