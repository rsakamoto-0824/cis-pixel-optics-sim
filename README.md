# cis-pixel-optics-sim

CMOSイメージセンサー画素の集光特性を評価する光学シミュレーションアプリ。
FDTD法（Meep）で画素構造（画素サイズ・On Chip Lens形状・共有方式など）の光伝搬を厳密計算し、
集光効率・入射角依存性（CRA特性）・隣接画素クロストーク・光の収束状態（断面／真上ビュー）を
ローカルWeb UIで評価できる。

## 何をするものか

- 対象構造: 裏面照射（BSI）画素。画素サイズ 0.5〜2.5 µm、1画素／2画素共有／4画素共有OCL
- 振れるパラメータ: 画素サイズ、OCL高さ・曲率、波長、入射角、各層の膜厚・屈折率
- 計算モード: 2D断面（高速・分オーダー）／3D（真上ビュー・共有OCL評価、数十分オーダー）
- 詳細は [requirements.md](requirements.md)（要件）と [design.md](design.md)（設計）を参照

## 実行方法（開発完了後に確定）

環境は2つに分かれる（理由は design.md 1章を参照）。

```bash
# 1. アプリ本体環境（Python 3.14.5以上）
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt

# 2. 計算エンジン環境（Meep用 Python 3.13、miniforge/conda）
conda create -n meep-engine -c conda-forge python=3.13 pymeep
# ※パッケージ名・手順は環境構築タスク（P1-3-1）で確定する

# 3. アプリ起動
python app/main.py
# ブラウザで http://localhost:8000 を開く
```

## 必要な環境変数

なし（すべてローカル実行。APIキー等の秘密情報は使用しない）

## 注意事項

- 計算はすべてローカルPC内で完結する。構造データ・結果を外部サービスへ送信しない
- 3Dモードは1条件あたり数十分かかる場合がある。広いパラメータスイープは2Dモードで先に絞り込むこと
- 本リポジトリはPrivateで運用する
- 課題は [issues.md](issues.md) で管理する。実行タスクはAppleリマインダーで管理する
