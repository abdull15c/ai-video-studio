import logging
import os
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from .ffmpeg_util import run_ffmpeg

logger = logging.getLogger(__name__)


def _resolve_title_font_path():
    candidates = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates.extend(
            [
                os.path.join(windir, "Fonts", "impact.ttf"),
                os.path.join(windir, "Fonts", "arialbd.ttf"),
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Library/Fonts/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Impact.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
        )
    for pf in candidates:
        if pf and os.path.isfile(pf):
            return pf
    return "arialbd.ttf"

def create_thumbnail(project_id, topic):
    logger.info("Thumbnail, project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"
    
    base_img_path = f"{project_dir}/images/scene_1.jpg"
    base_video_path = f"{project_dir}/raw_videos/scene_1.mp4"
    thumb_path = f"{project_dir}/THUMBNAIL_{project_id}.jpg"
    
    # ЕСЛИ КАРТИНКИ НЕТ, НО ЕСТЬ ВИДЕО -> ВЫРЕЗАЕМ ПЕРВЫЙ КАДР ЧЕРЕЗ FFMPEG!
    if not os.path.exists(base_img_path) and os.path.exists(base_video_path):
        os.makedirs(f"{project_dir}/images", exist_ok=True)
        logger.info("Извлечение кадра из видео для превью")
        try:
            run_ffmpeg(
                ["ffmpeg", "-y", "-i", base_video_path, "-vframes", "1", "-q:v", "2", base_img_path],
                context="thumbnail extract frame",
            )
        except subprocess.CalledProcessError:
            logger.warning("FFmpeg не смог извлечь кадр — рисуем заглушку")
    
    if not os.path.exists(base_img_path):
        logger.info("Нет картинки/видео — рисуем превью с нуля")
        img = Image.new('RGB', (1280, 720), color=(20, 20, 30))
    else:
        img = Image.open(base_img_path).convert('RGBA')
        img = img.resize((1280, 720), Image.Resampling.LANCZOS)
        
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(1.3)
        enhancer_cont = ImageEnhance.Contrast(img)
        img = enhancer_cont.enhance(1.1)

    # Темный градиент для читаемости текста
    gradient = Image.new('RGBA', (1280, 720), color=(0, 0, 0, 0))
    draw_grad = ImageDraw.Draw(gradient)
    for x in range(800):
        alpha = int(255 * (1 - (x / 800)))
        draw_grad.line([(x, 0), (x, 720)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, gradient).convert('RGB')

    draw = ImageDraw.Draw(img)
    
    font_path = _resolve_title_font_path()
    try:
        font = ImageFont.truetype(font_path, 90)
    except OSError:
        font = ImageFont.load_default()

    words = topic.upper().split()
    lines, line = [], ""
    for w in words:
        if len(line) + len(w) < 15: line += w + " "
        else:
            lines.append(line.strip())
            line = w + " "
    if line: lines.append(line.strip())
    
    y = 350 - (len(lines) * 50)
    for l in lines:
        for offset in [(3,3), (-3,-3), (3,-3), (-3,3), (0,4), (4,0)]:
            draw.text((80+offset[0], y+offset[1]), l, font=font, fill=(0,0,0))
        draw.text((80, y), l, font=font, fill=(255, 230, 50))
        y += 100

    img.save(thumb_path, quality=95)
    logger.info("Превью готово: %s", thumb_path)
    return True
