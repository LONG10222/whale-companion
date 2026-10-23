"""Disposable bounded GIF/WebP/APNG preview worker. Input bytes arrive on stdin."""
import base64
import io
import json
import sys
from PIL import Image, ImageOps

MAX_BYTES = 12 * 1024 ** 2
MAX_PIXELS = 20_000_000
MAX_DECODE_PIXELS = 64_000_000
MAX_FRAMES = 120


def main():
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('image too large')
    with Image.open(io.BytesIO(data)) as image:
        pixels = image.width * image.height
        if image.format not in {'GIF', 'WEBP', 'PNG'} or pixels > MAX_PIXELS:
            raise ValueError('unsupported image')
        # Metadata traversal is also inside the time/memory-limited process.
        frames = getattr(image, 'n_frames', 1)
        decoded = max(1, min(frames, MAX_FRAMES, MAX_DECODE_PIXELS // max(1, pixels)))
        indices = sorted({0, decoded // 2, decoded - 1})
        previews = []
        for index in indices:
            image.seek(index)
            frame = ImageOps.exif_transpose(image).convert('RGBA')
            frame.thumbnail((560, 560))
            previews.append(frame)
        canvas = Image.new('RGB', (sum(p.width for p in previews), max(p.height for p in previews)), 'white')
        x = 0
        for frame in previews:
            canvas.paste(frame, (x, 0), frame)
            x += frame.width
        output = io.BytesIO()
        canvas.save(output, format='JPEG', quality=88)
        raw = output.getvalue()
        if len(raw) > 2 * 1024 ** 2:
            raise ValueError('preview too large')
        result = {'frames': frames, 'sampled_frames': len(indices), 'decoded_frame_limit': decoded,
                  'animation_truncated': decoded < frames, 'jpeg': base64.b64encode(raw).decode('ascii')}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit(2)
