"""Met toutes les bannières au MÊME format (800 × 384).

  1. Déposez vos images d'origine (menu.jpg, welcome.jpg, tokens.jpg, bonus.jpg, friends.jpg, deposit.jpg, settings.jpg,
     community.jpg) dans assets/banners/_originales/
  2. python normalize_banners.py
  3. Les versions au bon format sont écrites dans assets/banners/ (utilisées par le bot).

Règles : la hauteur est ramenée à 384 ; une image trop haute est recadrée au centre ; une image trop étroite est prolongée
vers la GAUCHE (là où se trouve le décor ; le texte, à droite, ne bouge pas) avec le fond sombre de l'image, en dégradé.
"""
import sys
from pathlib import Path

from PIL import Image, ImageStat

W, H = 800, 384
SRC = Path(__file__).resolve().parent / "assets" / "banners" / "_originales"
DST = SRC.parent


def normalize(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    min_ratio = 1.6
    if w / h < min_ratio:                                   # trop haute : recadrage vertical centré
        new_h = int(w / min_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
        w, h = img.size
    scale = H / h
    img = img.resize((max(1, round(w * scale)), H), Image.LANCZOS)
    w = img.size[0]
    if w >= W:                                              # trop large : on garde la droite (texte)
        return img.crop((w - W, 0, w, H))
    pad = W - w
    bg = tuple(int(v) for v in ImageStat.Stat(img).median)   # couleur de fond = médiane de l'image (le fond est sombre)
    canvas = Image.new("RGB", (W, H), bg)
    fade = 24                                                # raccord doux entre le fond uni et le bord gauche de l'image
    mask = Image.new("L", img.size, 255)
    for x in range(min(fade, w)):
        mask.paste(int(255 * x / fade), (x, 0, x + 1, H))
    canvas.paste(img, (pad, 0), mask)
    return canvas


def main() -> int:
    files = sorted(p for p in SRC.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not files:
        print(f"Aucune image dans {SRC}")
        return 1
    for p in files:
        out = DST / (p.stem + ".jpg")
        normalize(Image.open(p)).save(out, "JPEG", quality=95)
        print(f"✓ {p.name} -> {out.name} ({W}x{H})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
