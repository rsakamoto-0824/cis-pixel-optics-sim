# cis-pixel-optics-sim

CMOSイメージセンサー画素の集光特性を評価する光学シミュレーションアプリ。
FDTD法（Meep）で画素構造（画素サイズ・On Chip Lens形状・共有方式など）の光伝搬を厳密計算し、
集光効率・入射角依存性（CRA特性）・隣接画素クロストーク・光の収束状態（断面／真上ビュー）を
ローカルWeb UIで評価できる。

## 何をするものか

- 対象構造: 裏面照射（BSI）画素。画素サイズ 0.5〜2.5 µm、1画素／2画素共有／4画素共有OCL
- 振れるパラメータ: 画素サイズ、OCL高さ・曲率、波長、入射角、各層の膜厚・屈折率、DTIの幅・深さ
- 計算モード: 2D断面（高速・分オーダー）／3D（真上ビュー・共有OCL評価、数十分オーダー）
- 詳細は [requirements.md](requirements.md)（要件）と [design.md](design.md)（設計）を参照

## 実行方法

Python 3.12の単一環境で動かす（本プロジェクト限定のPythonバージョンルール例外。design.md 1章を参照）。

```bash
# 1. 環境作成（初回のみ。condaが無ければ brew install --cask miniforge）
conda create -y -n cis-pixel-optics -c conda-forge python=3.12 pymeep matplotlib-base
conda activate cis-pixel-optics

# 2. 物理検証（フレネル解析解との比較 + メッシュ収束確認、約1分）
python -m tests.validate_fresnel

# 3. 動作確認サンプル（2D断面、垂直入射とCRA 25°、約30秒）
python -m tests.run_sample_2d
# 結果は jobs/sample_2d_cra00/ と jobs/sample_2d_cra25/ に出力される

# 4. 任意パラメータでの計算（input.jsonの書式は engine/fdtd_worker.py を参照）
python -m engine.fdtd_worker <ジョブフォルダ>
```

Web UI（`python app/main.py`）は開発中。現在は計算エンジン（2Dモード）まで動作する。

## 必要な環境変数

なし（すべてローカル実行。APIキー等の秘密情報は使用しない）

## 注意事項

- 計算はすべてローカルPC内で完結する。構造データ・結果を外部サービスへ送信しない
- 3Dモードは1条件あたり数十分かかる場合がある。広いパラメータスイープは2Dモードで先に絞り込むこと
- 本リポジトリはPrivateで運用する
- 課題は [issues.md](issues.md) で管理する。実行タスクはAppleリマインダーで管理する
