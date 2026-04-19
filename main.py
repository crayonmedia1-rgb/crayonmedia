from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, tempfile, os, uuid, shutil
from typing import List, Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WORK_DIR = "/tmp/shorts"
os.makedirs(WORK_DIR, exist_ok=True)

def run_ffmpeg(args: list) -> tuple[bool, str]:
    cmd = ["ffmpeg", "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stderr

@app.get("/")
def root():
    return {"status": "ok", "message": "Shorts API running"}

@app.post("/generate")
async def generate(
    mode: str = Form(...),
    orig_sound: str = Form("true"),
    slide_dur: int = Form(3),
    hl_start: int = Form(0),
    hl_end: int = Form(60),
    caption_text: str = Form(""),
    videos: List[UploadFile] = File(default=[]),
    images: List[UploadFile] = File(default=[]),
    bgm: Optional[UploadFile] = File(default=None),
    bg_file: Optional[UploadFile] = File(default=None),
):
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    out_path = os.path.join(job_dir, "out.mp4")
    has_sound = orig_sound.lower() == "true"

    vf_base = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

    try:
        # BGM 저장
        bgm_path = None
        if bgm and bgm.filename:
            bgm_path = os.path.join(job_dir, "bgm.mp3")
            with open(bgm_path, "wb") as f:
                f.write(await bgm.read())

        if mode == "merge":
            video_paths = []
            for i, v in enumerate(videos):
                p = os.path.join(job_dir, f"v{i}.mp4")
                with open(p, "wb") as f:
                    f.write(await v.read())
                video_paths.append(p)
            if not video_paths:
                return JSONResponse({"error": "영상 파일이 없어요"}, status_code=400)

            list_txt = os.path.join(job_dir, "list.txt")
            with open(list_txt, "w") as f:
                for p in video_paths:
                    f.write(f"file '{p}'\n")

            args = ["-f", "concat", "-safe", "0", "-i", list_txt]
            if bgm_path:
                args += ["-i", bgm_path]
            vf = f"{vf_base},format=yuv420p"
            if bgm_path and has_sound:
                args += ["-filter_complex", "[1:a]volume=0.25[b];[0:a][b]amix=inputs=2:duration=first[a]",
                         "-map", "0:v", "-map", "[a]"]
            elif bgm_path:
                args += ["-map", "0:v", "-map", "1:a", "-shortest"]
            elif has_sound:
                args += ["-map", "0:v", "-map", "0:a"]
            else:
                args += ["-an"]
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", out_path]

        elif mode == "highlight":
            if not videos:
                return JSONResponse({"error": "영상 파일이 없어요"}, status_code=400)
            vp = os.path.join(job_dir, "src.mp4")
            with open(vp, "wb") as f:
                f.write(await videos[0].read())
            dur = max(1, hl_end - hl_start)
            args = ["-ss", str(hl_start), "-t", str(dur), "-i", vp]
            if bgm_path:
                args += ["-i", bgm_path]
            vf = f"{vf_base},format=yuv420p"
            if bgm_path and has_sound:
                args += ["-filter_complex", "[1:a]volume=0.25[b];[0:a][b]amix=inputs=2:duration=first[a]",
                         "-map", "0:v", "-map", "[a]"]
            elif bgm_path:
                args += ["-map", "0:v", "-map", "1:a", "-shortest"]
            elif not has_sound:
                args += ["-an"]
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", out_path]

        elif mode == "slideshow":
            if not images:
                return JSONResponse({"error": "사진이 없어요"}, status_code=400)
            img_paths = []
            for i, img in enumerate(images):
                p = os.path.join(job_dir, f"img{i}.jpg")
                with open(p, "wb") as f:
                    f.write(await img.read())
                img_paths.append(p)
            list_txt = os.path.join(job_dir, "imgs.txt")
            with open(list_txt, "w") as f:
                for p in img_paths:
                    f.write(f"file '{p}'\nduration {slide_dur}\n")
            args = ["-f", "concat", "-safe", "0", "-i", list_txt]
            if bgm_path:
                args += ["-i", bgm_path, "-map", "0:v", "-map", "1:a", "-shortest"]
            vf = f"{vf_base},format=yuv420p"
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", out_path]

        elif mode == "caption":
            lines = [l.strip() for l in caption_text.split("\n") if l.strip()] or ["쇼츠"]
            sec = 3
            total = len(lines) * sec
            draw = ",".join([
                f"drawtext=text='{l.replace(chr(39), '')}':fontsize=54:fontcolor=white:borderw=4:bordercolor=black@0.8:x=(w-tw)/2:y=(h-th)/2:enable='between(t\\,{i*sec}\\,{(i+1)*sec})'"
                for i, l in enumerate(lines)
            ])
            bg_path = None
            if bg_file and bg_file.filename:
                bg_path = os.path.join(job_dir, "bg_input")
                with open(bg_path, "wb") as f:
                    f.write(await bg_file.read())
                is_video = bg_file.content_type.startswith("video")
                if is_video:
                    args = ["-stream_loop", "-1", "-t", str(total), "-i", bg_path]
                else:
                    args = ["-loop", "1", "-t", str(total), "-i", bg_path]
            else:
                args = ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=1080x1920:r=30:d={total}"]
            if bgm_path:
                args += ["-i", bgm_path, "-map", "0:v", "-map", "1:a", "-shortest"]
            vf = f"{vf_base},{draw},format=yuv420p"
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", out_path]

        else:
            return JSONResponse({"error": f"알 수 없는 모드: {mode}"}, status_code=400)

        ok, stderr = run_ffmpeg(args)
        if not ok or not os.path.exists(out_path):
            return JSONResponse({"error": "FFmpeg 처리 실패", "detail": stderr[-500:]}, status_code=500)

        return FileResponse(out_path, media_type="video/mp4", filename="shorts.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # 응답 후 정리는 백그라운드에서
        pass

