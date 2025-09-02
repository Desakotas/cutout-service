from fastapi import FastAPI, HTTPException, Response, Request
from pydantic import BaseModel
import requests, io, hmac, hashlib, os
from rembg import remove
from PIL import Image

app = FastAPI()

API_TOKEN = os.getenv("API_TOKEN", "")
HMAC_SECRET = os.getenv("HMAC_SECRET", "")

class Req(BaseModel):
    src: str
    size: int | None = 1024  # max side length before matting

def verify_auth(req: Request, src: str):
    # Bearer check (optional)
    if API_TOKEN:
        auth = req.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != API_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # HMAC check (optional)
    if HMAC_SECRET:
        got = req.headers.get("x-edge-hmac", "")
        expect = hmac.new(HMAC_SECRET.encode(), src.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(got, expect):
            raise HTTPException(status_code=401, detail="Bad HMAC")

@app.post("/cutout")
def cutout(r: Req, request: Request):
    verify_auth(request, r.src)
    try:
        # Fetch image
        resp = requests.get(r.src, timeout=20)
        resp.raise_for_status()
        im = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        # Downscale (saves compute & memory)
        if r.size and r.size > 0:
            im.thumbnail((r.size, r.size))

        # Matting
        out = remove(im)  # RGBA with alpha

        # Encode PNG
        buf = io.BytesIO()
        out.save(buf, format="PNG", compress_level=6)
        return Response(buf.getvalue(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"cutout failed: {e}")