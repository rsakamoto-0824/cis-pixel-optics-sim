"""ローカルWeb UIサーバー（FastAPI）。

起動方法（conda環境 cis-pixel-optics、リポジトリ直下で）:
    python -m app.main
ブラウザで http://localhost:8000 を開く。
"""

import csv
import io

import meep as mp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from app import job_manager, result_plotter
from app.constants import (HOST, JOB_NAME_MAX_LENGTH, JOBS_DIR, PORT,
                           STATIC_DIR)
from engine import fdtd_worker

mp.verbosity(0)  # Meepのログはworker.log側に集約し、サーバーログを汚さない

# CSV一括計算のテンプレート（1行目=列名、2行目以降=記入例）。
# 空欄の列は画面の入力値が使われる。使える列名の一覧はヘルプに記載
BATCH_TEMPLATE_ROWS = [
    ["label", "pixel_pitch_um", "ocl.height_um", "materials.ocl_n",
     "source.wavelength_nm", "source.incident_angle_deg", "ocl.offset_um"],
    ["条件1", "1.0", "0.5", "1.58", "550", "0", "0"],
    ["条件2", "1.0", "0.6", "1.7", "550", "10", "0.1"],
]
BATCH_TEMPLATE_FILENAME = "batch_template.csv"

app = FastAPI(title="cis-pixel-optics-sim")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # ローカルアプリは更新が頻繁なため、ブラウザに古い画面を残さない
    # （no-cache = 毎回サーバーへ確認。変更がなければ304で軽量に済む）
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def build_default_job_name(params):
    """内容が一目で分かる既定のジョブ名を作る（後から画面で変更可能）。"""
    parts = []
    if params.get("batch"):
        parts.append(f"CSV一括{len(params['batch']['cases'])}条件")
    elif params.get("sweep"):
        label = fdtd_worker.SWEEP_PARAMETER_LABELS.get(
            params["sweep"]["parameter"], params["sweep"]["parameter"])
        parts.append(f"スイープ {label}")
    if params["mode"] == "3d":
        parts.append("3D真上ビュー")
    if params["crosstalk"]:
        parts.append("受光内訳")
    parts.append(f"{params['pixel_pitch_um']:g}µm画素")
    parts.append(f"{params['source']['wavelength_nm']:g}nm")
    angle = params["source"]["incident_angle_deg"]
    if angle:
        parts.append(f"CRA{angle:g}°")
    return " ".join(parts)


@app.post("/api/jobs")
async def create_job(request: Request):
    """パラメータを検証してジョブを起動する。"""
    user_params = await request.json()
    batch_csv_text = user_params.pop("batch_csv", None)
    try:
        if batch_csv_text:
            user_params["batch"] = fdtd_worker.parse_batch_csv(batch_csv_text)
        params = fdtd_worker.merge_defaults(user_params,
                                            fdtd_worker.DEFAULT_PARAMS)
        fdtd_worker.validate_params(params)
        warnings = fdtd_worker.collect_warnings(params)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    job_id = job_manager.create_job(params, build_default_job_name(params))
    return {"job_id": job_id, "warnings": warnings}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": job_manager.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    status = job_manager.get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    status["result"] = job_manager.get_job_result(job_id)
    return status


@app.get("/api/jobs/{job_id}/image")
def get_job_image(job_id: str):
    """断面図PNGを返す（初回アクセス時に生成する）。"""
    job_dir = JOBS_DIR / job_id
    image_path = job_dir / "cross_section.png"
    if not image_path.exists():
        if not (job_dir / "fields.npz").exists():
            raise HTTPException(status_code=404,
                                detail="計算結果がまだありません")
        result_plotter.plot_cross_section(job_dir)
    return FileResponse(image_path)


@app.get("/api/jobs/{job_id}/topview")
def get_job_top_view(job_id: str):
    """真上ビューPNGを返す（3Dモードのみ。初回アクセス時に生成する）。"""
    job_dir = JOBS_DIR / job_id
    image_path = job_dir / "top_view.png"
    if not image_path.exists():
        if not (job_dir / "fields.npz").exists():
            raise HTTPException(status_code=404,
                                detail="計算結果がまだありません")
        if result_plotter.plot_top_view(job_dir) is None:
            raise HTTPException(status_code=404,
                                detail="真上ビューは3Dモードのみ出力されます")
    return FileResponse(image_path)


@app.get("/api/jobs/{job_id}/sweep-plot")
def get_job_sweep_plot(job_id: str):
    """スイープ結果グラフPNGを返す（初回アクセス時に生成する）。"""
    job_dir = JOBS_DIR / job_id
    image_path = job_dir / "sweep_plot.png"
    if not image_path.exists():
        result = job_manager.get_job_result(job_id)
        if not result or result.get("type") != "sweep":
            raise HTTPException(status_code=404,
                                detail="スイープ結果がありません")
        result_plotter.plot_sweep(job_dir, result["sweep"])
    return FileResponse(image_path)


@app.get("/api/jobs/{job_id}/csv")
def get_job_csv(job_id: str):
    """スイープまたはCSV一括計算の結果CSVを返す。"""
    for file_name in ("batch.csv", "sweep.csv"):
        csv_path = JOBS_DIR / job_id / file_name
        if csv_path.exists():
            kind = file_name.removesuffix(".csv")
            return FileResponse(csv_path, media_type="text/csv",
                                filename=f"{kind}_{job_id}.csv")
    raise HTTPException(status_code=404, detail="CSVがありません")


@app.get("/api/batch-template")
def get_batch_template():
    """CSV一括計算のテンプレートCSVを返す（Excel対応のBOM付きUTF-8）。"""
    buffer = io.StringIO()
    csv.writer(buffer).writerows(BATCH_TEMPLATE_ROWS)
    headers = {"Content-Disposition":
               f'attachment; filename="{BATCH_TEMPLATE_FILENAME}"'}
    return Response(content=buffer.getvalue().encode("utf-8-sig"),
                    media_type="text/csv", headers=headers)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not job_manager.cancel_job(job_id):
        raise HTTPException(status_code=400, detail="中断できませんでした")
    return {"cancelled": True}


@app.post("/api/jobs/{job_id}/name")
async def rename_job(job_id: str, request: Request):
    """ジョブ名を変更する。"""
    body = await request.json()
    new_name = str(body.get("name", "")).strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="ジョブ名が空です")
    if len(new_name) > JOB_NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"ジョブ名は{JOB_NAME_MAX_LENGTH}文字以内にしてください")
    if not job_manager.rename_job(job_id, new_name):
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return {"name": new_name}


@app.post("/api/jobs/bulk-delete")
async def bulk_delete_jobs(request: Request):
    """複数ジョブの一括削除。

    body: {"job_ids": [...]} で選択分、{"all": true} で全件。
    実行中のジョブはスキップして件数を返す。
    """
    body = await request.json()
    if body.get("all"):
        job_ids = job_manager.list_all_job_ids()
    else:
        job_ids = body.get("job_ids") or []
        if not job_ids:
            raise HTTPException(status_code=400,
                                detail="削除するジョブが選択されていません")
    deleted_count, skipped_count = job_manager.delete_jobs_bulk(job_ids)
    return {"deleted": deleted_count, "skipped": skipped_count}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    """ジョブを削除する（実行中は不可）。"""
    deleted, reason = job_manager.delete_job(job_id)
    if not deleted:
        status_code = 404 if reason == "ジョブが見つかりません" else 400
        raise HTTPException(status_code=status_code, detail=reason)
    return {"deleted": True}


@app.post("/api/preview")
async def preview_structure(request: Request):
    """FDTDを実行せずに構造断面図を生成する（入力ミス防止用）。"""
    user_params = await request.json()
    try:
        epsilon, x_um, y_um, layer_info = fdtd_worker.compute_epsilon_preview(
            user_params)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    png_bytes = result_plotter.plot_structure_preview(
        epsilon, x_um, y_um, layer_info)
    return Response(content=png_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
