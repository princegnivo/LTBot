"""Images du bot : bannières d'écran, image aléatoire sous les signaux (IMG1), cartes de résultat (IMG2).

Arborescence (dossier `assets/`, modifiable avec ASSETS_DIR) :
    assets/banners/  menu · welcome · tokens · bonus · friends · deposit · settings · community   (.jpg)
    assets/IMG1/     images .jpg envoyées (au hasard) sous chaque signal
    assets/IMG2/     fonds VIERGES .jpg sur lesquels le bot écrit le résultat d'une session
    assets/share/    (facultatif) fonds vierges pour l'image « total des utilisateurs » (sinon IMG2)
    assets/fonts/    polices (LiberationSans-Bold.ttf fournie)

Tout est tolérant : dossier vide ou image illisible = le bot continue sans image, jamais d'erreur.
"""
import io
import logging
import random
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("visuals")

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
EXTS = {".jpg", ".jpeg", ".png"}
FILE_IDS: dict = {}                      # chemin -> file_id Telegram (évite de renvoyer le fichier à chaque fois)
_last: dict = {}                         # dossier -> dernière image tirée (évite deux fois de suite la même)

REF_W = 1364                              # largeur de référence de la mise en page des cartes (1364 × 768)
GREEN = (40, 235, 140)
RED = (255, 85, 85)
WHITE = (240, 240, 240)
GREY = (165, 165, 165)


def configure(assets_dir: str = "") -> None:
    global ASSETS
    ASSETS = Path(assets_dir).expanduser() if assets_dir else ROOT / "assets"


def images_in(folder: str) -> List[Path]:
    d = ASSETS / folder
    try:
        return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith("."))
    except OSError:
        return []


def banner_path(name: str) -> Optional[Path]:
    for ext in (".jpg", ".jpeg", ".png"):
        p = ASSETS / "banners" / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def pick(folder: str) -> Optional[Path]:
    """Image aléatoire d'un dossier (jamais deux fois de suite la même quand il y en a plusieurs)."""
    files = images_in(folder)
    if not files:
        return None
    if len(files) > 1 and _last.get(folder) in files:
        files = [p for p in files if p != _last[folder]]
    _last[folder] = random.choice(files)
    return _last[folder]


# ------------------------------------------------------------------------------ rendu
def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    cands = [ASSETS / "fonts" / name, ROOT / "assets" / "fonts" / name,
             Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"),
             Path("/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf"),
             Path("/usr/share/fonts/truetype/liberation/" + name),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for p in cands:
        try:
            if p.is_file():
                return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:                                  # très anciens Pillow
        return ImageFont.load_default()


def _open(path: Optional[Path]):
    from PIL import Image
    if path is not None:
        try:
            return Image.open(path).convert("RGB")
        except Exception as e:                         # image corrompue : repli sur un fond uni
            log.warning("image illisible %s : %s", path, e)
    return _gradient(1364, 768)


def _gradient(w: int, h: int):
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        v = int(8 + 22 * y / h)
        for x in range(w):
            px[x, y] = (v, v + 6, v + 2)
    return img


def _text(draw, xy, txt, font, fill, anchor, shadow: int = 3):
    x, y = xy
    draw.text((x + shadow, y + shadow), txt, font=font, fill=(0, 0, 0), anchor=anchor)       # ombre : lisible sur tout fond
    draw.text((x, y), txt, font=font, fill=fill, anchor=anchor)


def _jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def money(v: float, sign: bool = False) -> str:
    s = f"{abs(v):,.2f}".replace(",", " ")
    return (("+" if v >= 0 else "-") if sign else ("-" if v < 0 else "")) + "$" + s


def render_result(bg: Optional[Path], handle: str, pair: str, pnl: float, stake: float, duration: str,
                  label: str, step: str) -> bytes:
    """Carte de résultat d'une session (même mise en page que l'exemple, quelle que soit la taille du fond)."""
    from PIL import ImageDraw
    img = _open(bg)
    W, H = img.size
    k = W / REF_W
    d = ImageDraw.Draw(img)
    x = int(W - 64 * k)
    col = GREEN if pnl >= 0 else RED
    pct = (pnl / stake * 100) if stake else 0.0
    _text(d, (x, int(70 * k)), handle, _font(int(36 * k)), WHITE, "rm")
    _text(d, (x, int(H * 0.38)), pair, _font(int(50 * k)), WHITE, "rm")
    _text(d, (x, int(H * 0.54)), f"{'+' if pct >= 0 else '-'}{abs(pct):.0f}%", _font(int(150 * k)), col, "rm", 4)
    f_amt, f_sub = _font(int(54 * k)), _font(int(33 * k))
    sub = f" sur la mise {money(stake).replace('.00', '')}"
    sub_w = d.textlength(sub, font=f_sub)
    y = int(H * 0.69)
    _text(d, (x, y), sub, f_sub, WHITE, "rm")
    _text(d, (int(x - sub_w), y), money(pnl, True), f_amt, WHITE, "rm")
    _text(d, (x, int(H * 0.78)), f"{duration} · {label} · {step}", _font(int(30 * k), False), GREY, "rm", 2)
    return _jpeg(img)


def render_total(bg: Optional[Path], handle: str, total: float, title: str = "Les utilisateurs ont tradé") -> bytes:
    """Carte « total » : pseudo du bot, titre et gros montant centrés."""
    from PIL import ImageDraw
    img = _open(bg)
    W, H = img.size
    k = W / REF_W
    d = ImageDraw.Draw(img)
    cx = W // 2
    _text(d, (cx, int(H * 0.13)), handle, _font(int(38 * k)), WHITE, "mm")
    _text(d, (cx, int(H * 0.38)), title, _font(int(48 * k)), WHITE, "mm")
    amount = money(total, True).replace(" ", "\u202f").replace(".00", "")
    size = int(200 * k)
    f = _font(size)
    while d.textlength(amount, font=f) > W * 0.88 and size > 40:       # très gros montants : on réduit pour tenir
        size -= 8
        f = _font(size)
    _text(d, (cx, int(H * 0.60)), amount, f, GREEN if total >= 0 else RED, "mm", 5)
    return _jpeg(img)
