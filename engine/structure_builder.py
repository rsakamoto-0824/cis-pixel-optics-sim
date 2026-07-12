"""画素構造（積層・OCL・DTI）のMeepジオメトリ生成。

2Dモードでは画素のXZ断面を、Meepの2D座標 (x=横方向, y=深さ方向) で表す。
座標単位はMeepの慣例に従い µm 基準（a = 1 µm）とする。

物理座標は「Siの上面（受光面）を y = 0」として組み立て、
最後にMeepのセル中心座標へ平行移動する。
"""

import math

import meep as mp

# 計算領域の余白（構造の外側に確保する空間）
PML_THICKNESS_UM = 0.6      # 上下の吸収境界の厚さ
AIR_GAP_ABOVE_OCL_UM = 0.4  # OCL頂点と光源面の間の空気層
SOURCE_TO_PML_GAP_UM = 0.3  # 光源面と上側PMLの間隔

# レンズ断面ポリゴンの分割数（滑らかさとメッシュ精度のバランス）
LENS_POLYGON_POINTS = 128

# 2Dモードで1セルに含める画素数（OCL共有方式ごと）
NUM_PIXELS_2D = {"single": 1, "shared2": 2, "shared4": 2}


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


def build_lens_prism(center_x_um, base_y_um, half_width_um, height_um,
                     shape, superellipse_exponent, medium):
    """レンズ断面ポリゴンからMeepのPrism（2D多角形）を作る。"""
    vertices = []
    for i in range(LENS_POLYGON_POINTS + 1):
        x_local = -half_width_um + (2.0 * half_width_um) * i / LENS_POLYGON_POINTS
        y = lens_profile_height_um(x_local, half_width_um, height_um, shape,
                                   superellipse_exponent)
        vertices.append(mp.Vector3(center_x_um + x_local, base_y_um + y, 0))
    # 底面の直線で閉じる（終点→始点は自動で結ばれる）
    return mp.Prism(vertices, height=mp.inf, axis=mp.Vector3(0, 0, 1),
                    material=medium)


def pixel_boundary_positions_um(pixel_pitch_um, num_pixels, placement, sharing):
    """DTIを置く画素境界のx座標リストを返す（セル中心を0とする）。

    横方向は周期境界のため、セル両端の境界は片側半分ずつ配置しても
    周期像とつながって1本の溝になる。
    """
    cell_half_width = pixel_pitch_um * num_pixels / 2.0
    positions = []
    for i in range(num_pixels + 1):
        x = -cell_half_width + i * pixel_pitch_um
        is_shared_unit_boundary = (i == 0 or i == num_pixels)
        if placement == "shared_only" and sharing != "single":
            if not is_shared_unit_boundary:
                continue
        positions.append(x)
    return positions


def build_structure_2d(params, media):
    """2Dモードの計算セル・ジオメトリ・主要座標をまとめて返す。

    media は materials.make_medium で作った {層名: mp.Medium} の辞書。
    戻り値の座標はすべてMeepセル座標（セル中心が原点）。
    """
    pitch = params["pixel_pitch_um"]
    sharing = params["ocl"]["sharing"]
    num_pixels = NUM_PIXELS_2D[sharing]
    cell_width = pitch * num_pixels

    layers = params["layers"]
    si_thickness = layers["si_um"]
    ocl_height = params["ocl"]["height_um"] if params["ocl"]["enabled"] else 0.0

    # 物理座標（Si上面 = 0）で各面の高さを決める
    ar_top = layers["ar_um"]
    cf_top = ar_top + layers["color_filter_um"]
    planarization_top = cf_top + layers["planarization_um"]
    lens_base = planarization_top
    lens_apex = lens_base + ocl_height
    source_y = lens_apex + AIR_GAP_ABOVE_OCL_UM
    y_max = source_y + SOURCE_TO_PML_GAP_UM + PML_THICKNESS_UM
    # Siは下側PMLの中まで満たし、裏面反射のない半無限基板として扱う
    y_min = -si_thickness - PML_THICKNESS_UM

    cell_height = y_max - y_min
    y_offset = (y_max + y_min) / 2.0

    def to_cell_y(physical_y):
        return physical_y - y_offset

    geometry = []

    # 積層（下から）。厚さ0の層はスキップする
    si_block_height = si_thickness + PML_THICKNESS_UM
    geometry.append(mp.Block(
        size=mp.Vector3(mp.inf, si_block_height, mp.inf),
        center=mp.Vector3(0, to_cell_y(-si_block_height / 2.0)),
        material=media["silicon"]))

    layer_stack = [
        ("ar", 0.0, ar_top),
        ("color_filter", ar_top, cf_top),
        ("planarization", cf_top, planarization_top),
    ]
    for name, bottom, top in layer_stack:
        thickness = top - bottom
        if thickness <= 0.0:
            continue
        geometry.append(mp.Block(
            size=mp.Vector3(mp.inf, thickness, mp.inf),
            center=mp.Vector3(0, to_cell_y((bottom + top) / 2.0)),
            material=media[name]))

    # OCL: 共有方式に応じてレンズ幅と個数を決める
    if params["ocl"]["enabled"]:
        if sharing == "single":
            lens_centers = [(-cell_width / 2.0) + pitch * (i + 0.5)
                            for i in range(num_pixels)]
            lens_half_width = pitch / 2.0
        else:
            # shared2/shared4は2画素分の幅を持つ1枚のレンズ
            # （shared4の2Dモードは2×2レンズ中央断面の近似）
            lens_centers = [0.0]
            lens_half_width = pitch
        for center_x in lens_centers:
            geometry.append(build_lens_prism(
                center_x, to_cell_y(lens_base), lens_half_width, ocl_height,
                params["ocl"]["shape"], params["ocl"]["superellipse_exponent"],
                media["ocl"]))

    # DTI: Si上面から指定深さまでの縦溝。Siより後に置いてSiを上書きする
    dti = params["dti"]
    if dti["enabled"] and dti["depth_um"] > 0.0:
        boundary_xs = pixel_boundary_positions_um(
            pitch, num_pixels, dti["placement"], sharing)
        for x in boundary_xs:
            geometry.append(mp.Block(
                size=mp.Vector3(dti["width_um"], dti["depth_um"], mp.inf),
                center=mp.Vector3(x, to_cell_y(-dti["depth_um"] / 2.0)),
                material=media["dti"]))

    coords = {
        "cell_width_um": cell_width,
        "cell_height_um": cell_height,
        "num_pixels": num_pixels,
        "source_y": to_cell_y(source_y),
        "si_top_y": to_cell_y(0.0),
        "pd_monitor_y": to_cell_y(-params["pd"]["top_depth_um"]),
        "y_min": to_cell_y(y_min),
        "y_max": to_cell_y(y_max),
    }
    return {
        "cell_size": mp.Vector3(cell_width, cell_height),
        "geometry": geometry,
        "boundary_layers": [mp.PML(PML_THICKNESS_UM, direction=mp.Y)],
        "coords": coords,
    }
