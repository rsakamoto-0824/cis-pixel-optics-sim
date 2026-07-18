"""計算結果・構造プレビューの画像生成（matplotlib）。"""

import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# 日本語ラベルの文字化け対策（macOS標準→Windows標準の順で探す）
plt.rcParams["font.family"] = ["Hiragino Sans", "Yu Gothic", "Meiryo",
                               "sans-serif"]

CROSS_SECTION_DPI = 150

# カラーフィルタ層の強調表示（構造図で位置が分かるようにする）
COLOR_FILTER_BAND_COLOR = "#ff8c00"
COLOR_FILTER_BAND_ALPHA = 0.35

# 構造図の色分け: 空気（誘電率≈1）は背景色にして、OCLなどの材料と
# 見分けやすくする。この値未満の誘電率を空気とみなす
AIR_EPSILON_THRESHOLD = 1.05
AIR_BACKGROUND_COLOR = "white"
STRUCTURE_COLORMAP = "viridis"


def draw_structure(ax, epsilon_transposed, extent):
    """構造（誘電率）を描く。空気は背景色で塗り、材料だけに色を付ける。"""
    masked = np.ma.masked_less(epsilon_transposed, AIR_EPSILON_THRESHOLD)
    colormap = plt.get_cmap(STRUCTURE_COLORMAP).copy()
    colormap.set_bad(AIR_BACKGROUND_COLOR)
    return ax.imshow(masked, origin="lower", extent=extent,
                     cmap=colormap, aspect="equal")


def draw_color_filter_band(ax, cf_bottom_y, cf_top_y, si_top_y):
    """カラーフィルタ層を半透明の帯で塗り、凡例用のラベルを付ける。

    座標はセル座標で受け取り、Si上面=0の物理座標に直して描く。
    厚さ0（層なし）のときは何も描かない。
    """
    bottom = float(cf_bottom_y) - float(si_top_y)
    top = float(cf_top_y) - float(si_top_y)
    if top - bottom <= 0.0:
        return
    ax.axhspan(bottom, top, color=COLOR_FILTER_BAND_COLOR,
               alpha=COLOR_FILTER_BAND_ALPHA, label="カラーフィルタ")


def plot_cross_section(job_dir):
    """fields.npz から構造と|E|^2の断面図PNGを作り、そのパスを返す。"""
    job_dir = Path(job_dir)
    output_path = job_dir / "cross_section.png"

    data = np.load(job_dir / "fields.npz")
    # 縦軸はSi上面を0とした物理座標で表示する
    si_top_y = float(data["si_top_y"])
    x_um = data["x_um"]
    y_um = data["y_um"] - si_top_y
    pd_y = float(data["pd_monitor_y"]) - si_top_y
    extent = [x_um[0], x_um[-1], y_um[0], y_um[-1]]

    figure, (ax_structure, ax_intensity, ax_silicon) = plt.subplots(
        1, 3, figsize=(13, 6))

    draw_structure(ax_structure, data["epsilon"].T, extent)
    if "cf_top_y" in data:
        draw_color_filter_band(ax_structure, data["ar_top_y"],
                               data["cf_top_y"], si_top_y)
        ax_structure.legend(loc="lower right", fontsize=8)
    ax_structure.set_title("構造（誘電率）")
    ax_structure.set_xlabel("x [µm]")
    ax_structure.set_ylabel("y [µm]（Si上面 = 0）")

    image = ax_intensity.imshow(data["intensity"].T, origin="lower",
                                extent=extent, cmap="inferno", aspect="equal")
    ax_intensity.contour(data["epsilon"].T, levels=3, origin="lower",
                         extent=extent, colors="white", linewidths=0.4)
    ax_intensity.axhline(pd_y, color="cyan", linewidth=0.8, linestyle="--",
                         label="PD面")
    ax_intensity.set_title("|E|² 断面分布（全体）")
    ax_intensity.set_xlabel("x [µm]")
    ax_intensity.legend(loc="lower right")
    figure.colorbar(image, ax=ax_intensity, shrink=0.8)

    # Si内部のみを切り出し、Si内の最大値で色スケールを取り直す
    # （レンズ付近の明るさに埋もれず、Si内の光の分布が見える）
    si_rows = (data["y_um"] - si_top_y) <= 0.0
    intensity_si = data["intensity"][:, si_rows]
    epsilon_si = data["epsilon"][:, si_rows]
    y_si = y_um[si_rows]
    extent_si = [x_um[0], x_um[-1], y_si[0], y_si[-1]]
    image_si = ax_silicon.imshow(intensity_si.T, origin="lower",
                                 extent=extent_si, cmap="inferno",
                                 aspect="equal")
    ax_silicon.contour(epsilon_si.T, levels=3, origin="lower",
                       extent=extent_si, colors="white", linewidths=0.4)
    ax_silicon.axhline(pd_y, color="cyan", linewidth=0.8, linestyle="--",
                       label="PD面")
    ax_silicon.set_title("|E|² Si内部（拡大スケール）")
    ax_silicon.set_xlabel("x [µm]")
    ax_silicon.legend(loc="lower right")
    figure.colorbar(image_si, ax=ax_silicon, shrink=0.8)

    figure.savefig(output_path, dpi=CROSS_SECTION_DPI, bbox_inches="tight")
    plt.close(figure)
    return output_path


def plot_top_view(job_dir):
    """fields.npz の真上ビュー（XY平面 |E|^2）PNGを作り、パスを返す。

    真上ビューのデータは3Dモードのみ保存される。無い場合は None を返す。
    """
    job_dir = Path(job_dir)
    output_path = job_dir / "top_view.png"

    data = np.load(job_dir / "fields.npz")
    if "intensity_xy" not in data:
        return None

    x_um, y_um = data["xy_x_um"], data["xy_y_um"]
    extent = [x_um[0], x_um[-1], y_um[0], y_um[-1]]

    figure, ax = plt.subplots(figsize=(6, 5))
    image = ax.imshow(data["intensity_xy"].T, origin="lower", extent=extent,
                      cmap="inferno", aspect="equal")
    ax.contour(data["epsilon_xy"].T, levels=3, origin="lower", extent=extent,
               colors="white", linewidths=0.4)
    depth = float(data["view_depth_um"])
    ax.set_title(f"|E|² 真上ビュー（Si上面から {depth:.2f} µm）")
    ax.set_xlabel("x [µm]")
    ax.set_ylabel("y [µm]")
    figure.colorbar(image, ax=ax, shrink=0.85)

    figure.savefig(output_path, dpi=CROSS_SECTION_DPI, bbox_inches="tight")
    plt.close(figure)
    return output_path


def plot_sweep(job_dir, sweep):
    """スイープ結果の折れ線グラフPNGを作り、パスを返す。

    sweep は result.json 内の sweep ブロック（label / results を含む辞書）。
    """
    job_dir = Path(job_dir)
    output_path = job_dir / "sweep_plot.png"

    values = [entry["value"] for entry in sweep["results"]]
    efficiency = [entry["collection_efficiency_total"] * 100.0
                  for entry in sweep["results"]]

    figure, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(values, efficiency, marker="o", label="集光効率（合計）")
    if "crosstalk_total" in sweep["results"][0]:
        crosstalk = [entry["crosstalk_total"] * 100.0
                     for entry in sweep["results"]]
        center = [entry["collection_efficiency_center"] * 100.0
                  for entry in sweep["results"]]
        ax.plot(values, center, marker="s", label="集光効率（中央画素）")
        ax.plot(values, crosstalk, marker="^", label="クロストーク（漏れ合計）")
    ax.set_xlabel(sweep["label"])
    ax.set_ylabel("比率 [%]")
    ax.set_title("パラメータスイープ結果")
    ax.grid(True, alpha=0.3)
    ax.legend()

    figure.savefig(output_path, dpi=CROSS_SECTION_DPI, bbox_inches="tight")
    plt.close(figure)
    return output_path


def plot_structure_preview(epsilon, x_um, y_um, layer_info):
    """構造プレビュー（誘電率分布）のPNGバイト列を返す。

    layer_info はセル座標の {"si_top", "ar_top", "cf_top"} を持つ辞書。
    """
    si_top_y = layer_info["si_top"]
    y_um = y_um - si_top_y
    extent = [x_um[0], x_um[-1], y_um[0], y_um[-1]]

    figure, ax = plt.subplots(figsize=(5, 6))
    image = draw_structure(ax, epsilon.T, extent)
    ax.axhline(0.0, color="gray", linewidth=0.8, linestyle="--",
               label="Si上面")
    draw_color_filter_band(ax, layer_info["ar_top"], layer_info["cf_top"],
                           si_top_y)
    ax.set_title("構造プレビュー（誘電率）")
    ax.set_xlabel("x [µm]")
    ax.set_ylabel("y [µm]（Si上面 = 0）")
    ax.legend(loc="lower right")
    figure.colorbar(image, ax=ax, shrink=0.8, label="誘電率")

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=CROSS_SECTION_DPI,
                   bbox_inches="tight")
    plt.close(figure)
    return buffer.getvalue()
