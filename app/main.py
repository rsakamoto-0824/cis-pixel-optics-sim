"""ローカルWeb UIサーバー（FastAPI）。

起動方法（conda環境 cis-pixel-optics、リポジトリ直下で）:
    python -m app.main
ブラウザで http://localhost:8000 を開く。
"""

import meep as mp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from app import job_manager, result_plotter
from app.constants import HOST, JOBS_DIR, PORT, STATIC_DIR
from engine import fdtd_worker

mp.verbosity(0)  # Meepのログはworker.log側に集約し、サーバーログを汚さない

app = FastAPI(title="cis-pixel-optics-sim")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/jobs")
async def create_job(request: Request):
    """パラメータを検証してジョブを起動する。"""
    user_params = await request.json()
    try:
        params = fdtd_worker.merge_defaults(user_params,
                                            fdtd_worker.DEFAULT_PARAMS)
        fdtd_worker.validate_params(params)
        warnings = fdtd_worker.collect_warnings(params)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    job_id = job_manager.create_job(params)
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
    csv_path = JOBS_DIR / job_id / "sweep.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="CSVがありません")
    return FileResponse(csv_path, media_type="text/csv",
                        filename=f"sweep_{job_id}.csv")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not job_manager.cancel_job(job_id):
        raise HTTPException(status_code=400, detail="中断できませんでした")
    return {"cancelled": True}


@app.post("/api/preview")
async def preview_structure(request: Request):
    """FDTDを実行せずに構造断面図を生成する（入力ミス防止用）。"""
    user_params = await request.json()
    try:
        epsilon, x_um, y_um, si_top_y = fdtd_worker.compute_epsilon_preview(
            user_params)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    png_bytes = result_plotter.plot_structure_preview(
        epsilon, x_um, y_um, si_top_y)
    return Response(content=png_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
