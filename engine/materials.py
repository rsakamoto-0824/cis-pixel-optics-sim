"""材料の屈折率モデル。

Siの複素屈折率は結晶Siの公表値を丸めた暫定テーブルを使う
（正式な出典は課題 I-003 で確定する）。
その他の材料は分散が小さいため定数近似から開始する。
"""

import math

import meep as mp
import numpy as np

# 非分散材料の既定屈折率（input.json でユーザーが上書き可能）
DEFAULT_OCL_N = 1.58
# OCL表面の反射防止膜。理想値は sqrt(空気 × OCL) = sqrt(1.58) ≈ 1.26
DEFAULT_OCL_COAT_N = 1.26
DEFAULT_PLANARIZATION_N = 1.50
DEFAULT_COLOR_FILTER_N = 1.55
DEFAULT_AR_N = 1.46        # 反射防止膜・埋め込み酸化膜（SiO2相当）
DEFAULT_DTI_FILL_N = 1.46  # DTI充填材（SiO2相当）

# Siの複素屈折率テーブル（波長nm, n, k）。可視域の暫定値（課題 I-003）
SILICON_NK_TABLE = [
    (400.0, 5.57, 0.387),
    (450.0, 4.67, 0.148),
    (500.0, 4.30, 0.073),
    (550.0, 4.08, 0.031),
    (600.0, 3.94, 0.019),
    (650.0, 3.85, 0.014),
    (700.0, 3.78, 0.011),
]


def silicon_nk(wavelength_nm):
    """Siの複素屈折率 (n, k) をテーブルの線形補間で返す。"""
    wavelengths = [row[0] for row in SILICON_NK_TABLE]
    n_values = [row[1] for row in SILICON_NK_TABLE]
    k_values = [row[2] for row in SILICON_NK_TABLE]
    n = float(np.interp(wavelength_nm, wavelengths, n_values))
    k = float(np.interp(wavelength_nm, wavelengths, k_values))
    return n, k


def make_medium(n, k, wavelength_um):
    """複素屈折率 (n + ik) から、指定波長で正しい吸収を持つMeep媒質を作る。

    Meepは複素誘電率 eps_r + i*eps_i を、epsilon = eps_r と
    D_conductivity = 2*pi*f*eps_i/eps_r の組で表現する（単一波長で有効）。
    """
    eps_r = n * n - k * k
    eps_i = 2.0 * n * k
    if k == 0.0:
        return mp.Medium(epsilon=eps_r)
    frequency = 1.0 / wavelength_um  # Meep単位系（a = 1 µm, c = 1）
    conductivity = 2.0 * math.pi * frequency * eps_i / eps_r
    return mp.Medium(epsilon=eps_r, D_conductivity=conductivity)


def silicon_absorption_factor(wavelength_nm, depth_um):
    """Si内を depth_um 進んだときのパワー減衰率 exp(-alpha*d) を返す。

    フレネル検証で「界面透過率×吸収減衰」の解析値を作るために使う。
    """
    _, k = silicon_nk(wavelength_nm)
    wavelength_um = wavelength_nm / 1000.0
    alpha_per_um = 4.0 * math.pi * k / wavelength_um
    return math.exp(-alpha_per_um * depth_um)
