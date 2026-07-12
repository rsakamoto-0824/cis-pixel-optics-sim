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
    ax_intensity.axhline(pd_y, color="cyan", linewidth=0.8, linestyle="--",
                         label="PD面")
    ax_intensity.set_title("|E|² 断面分布")
    ax_intensity.set_xlabel("x [µm]")
    ax_intensity.legend(loc="lower right")
    figure.colorbar(image, ax=ax_intensity, shrink=0.8)

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


def plot_structure_preview(epsilon, x_um, y_um, si_top_y):
    """構造プレビュー（誘電率分布）のPNGバイト列を返す。"""
    y_um = y_um - si_top_y
    extent = [x_um[0], x_um[-1], y_um[0], y_um[-1]]

    figure, ax = plt.subplots(figsize=(5, 6))
    image = ax.imshow(epsilon.T, origin="lower", extent=extent,
                      cmap="viridis", aspect="equal")
    ax.axhline(0.0, color="white", linewidth=0.8, linestyle="--",
               label="Si上面")
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
