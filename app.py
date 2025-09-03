from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import requests, io, time, logging
from PIL import Image, ImageFile

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PIL safety settings
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

app = FastAPI(title="Cutout Service", version="1.0.0")

# Global variables for lazy initialization
SESSION = None
REMOVE_FN = None
MODEL_NAME = "u2net"          # Light and stable model
MAX_SIDE = 512                # Max dimension to limit memory/time

def get_session_and_remove():
    global SESSION, REMOVE_FN
    if SESSION is None or REMOVE_FN is None:
        from rembg import new_session, remove
        SESSION = new_session(MODEL_NAME)
        REMOVE_FN = remove
    return SESSION, REMOVE_FN

class CutoutReq(BaseModel):
    src: str = Field(..., description="Public image URL")
    size: Optional[int] = Field(MAX_SIDE, description="Max side before matting")

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/warm")
def warm():
    t0 = time.time()
    get_session_and_remove()
    return {"status": "warmed", "took_ms": int((time.time() - t0) * 1000)}

@app.post("/cutout")
def cutout(r: CutoutReq):
    try:
        # Download image
        logger.info(f"Downloading image from: {r.src}")
        resp = requests.get(
            r.src,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (cutout-service)"},
            allow_redirects=True,
        )
        resp.raise_for_status()
        
        # Validate content type
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "image" not in ctype:
            raise HTTPException(status_code=502, detail="Download failed: non-image content")

        # Decode image
        im = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        logger.info(f"Original image size: {im.size}, mode: {im.mode}")

        # Resize if needed
        target = min(int(r.size or MAX_SIDE), MAX_SIDE)
        im.thumbnail((target, target), Image.LANCZOS)
        logger.info(f"Resized to: {im.size}")

        # Apply background removal
        session, remove_fn = get_session_and_remove()
        out = remove_fn(im, session=session)
        logger.info(f"After rembg: size={out.size}, mode={out.mode}")

        # Check transparency and handle edge cases
        if out.mode == 'RGBA':
            # Get alpha channel statistics
            alpha = out.split()[-1]
            alpha_data = list(alpha.getdata())
            non_zero = sum(1 for a in alpha_data if a > 10)  # Count non-transparent pixels
            total_pixels = len(alpha_data)
            transparency_ratio = non_zero / total_pixels if total_pixels > 0 else 0
            
            logger.info(f"Transparency ratio: {transparency_ratio:.2%} ({non_zero}/{total_pixels} pixels)")
            
            # If image is mostly transparent, it might be a rembg failure
            if transparency_ratio < 0.1:  # Less than 10% non-transparent
                logger.warning("Image is mostly transparent, possible rembg failure")
                
                # Option 1: Composite on white background for visibility
                white_bg = Image.new("RGBA", out.size, (255, 255, 255, 255))
                white_bg.paste(out, (0, 0), out)
                out = white_bg
                logger.info("Composited on white background for visibility")
                
                # Option 2: Return original image (uncomment if preferred)
                # logger.info("Returning original image without background removal")
                # out = im

        # Encode as PNG
        buf = io.BytesIO()
        out.save(buf, format="PNG", compress_level=6, optimize=True)
        png_bytes = buf.getvalue()
        logger.info(f"Final PNG size: {len(png_bytes)} bytes")
        
        return app.responses.Response(
            content=png_bytes, 
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Transparency-Ratio": str(transparency_ratio) if 'transparency_ratio' in locals() else "unknown"
            }
        )
        
    except HTTPException:
        raise
    except requests.RequestException as e:
        logger.error(f"Request error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to download image: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in cutout: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Cutout processing failed: {str(e)}")