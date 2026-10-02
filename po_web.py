"""Automatisation Playwright : création de compte Pocket Option, connexion (récupération du SSID) et
vérification d'un ID dans l'espace partenaire (pocketpartners).

Principes de robustesse
  * jamais d'exception vers l'appelant : chaque opération retourne un résultat avec un STATUT ;
  * plusieurs sélecteurs candidats par champ, remplaçables par un fichier JSON (PO_SELECTORS_FILE) ;
  * le résultat est lu par SONDAGE (URL, erreurs du formulaire, cookie de session) et non par attente fixe ;
  * une nouvelle tentative n'a lieu qu'AVANT l'envoi du formulaire (jamais de double inscription) ;
  * navigateur toujours fermé (finally), nombre de navigateurs simultanés limité ;
  * capture d'écran de diagnostic en cas d'échec ; aucun mot de passe ni SSID dans les journaux ;
  * un CAPTCHA / contrôle anti-robot est DÉTECTÉ et signalé, jamais contourné : l'appelant propose alors le lien manuel.
"""
import asyncio
import importlib.util
import json
import logging
import re
import secrets
import string
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

import ssid as ssidlib

log = logging.getLogger("web")

DEFAULT_SELECTORS: Dict[str, Dict[str, List[str]]] = {
    "common": {
        "banner": ["#onetrust-accept-btn-handler", 'button:has-text("Accept all")', 'button:has-text("Accept")',
                   'button:has-text("Tout accepter")', 'button:has-text("Accepter")'],
        "captcha": ['iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]', 'iframe[src*="turnstile"]',
                    ".g-recaptcha", ".h-captcha", ".cf-turnstile"],
        "error": [".error", ".errors", ".invalid-feedback", ".form-error", '[class*="error" i]', '[role="alert"]'],
    },
    "register": {
        "email": ['input[name="email"]', 'input[type="email"]', "#email"],
        "password": ['input[name="password"]', 'input[type="password"]', "#password"],
        "password2": ['input[name="password_confirm"]', 'input[name="confirm_password"]',
                      'input[name="passwordConfirm"]', 'input[name="password_repeat"]'],
        "terms": ['input[type="checkbox"][name*="agree" i]', 'input[type="checkbox"][name*="terms" i]',
                  'input[type="checkbox"][name*="accept" i]', 'input[type="checkbox"]'],
        "submit": ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign up")',
                   'button:has-text("Register")', 'button:has-text("Inscription")', "button:has-text(\"S'inscrire\")"],
    },
    "login": {
        "email": ['input[name="email"]', 'input[type="email"]', "#email"],
        "password": ['input[name="password"]', 'input[type="password"]', "#password"],
        "submit": ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")',
                   'button:has-text("Log in")', 'button:has-text("Login")', 'button:has-text("Connexion")'],
    },
    "partners": {
        "email": ['input[name="email"]', 'input[type="email"]', 'input[name="login"]', "#email"],
        "password": ['input[name="password"]', 'input[type="password"]', "#password"],
        "submit": ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")',
                   'button:has-text("Log in")', 'button:has-text("Login")'],
        "search": ['input[type="search"]', 'input[placeholder*="search" i]', 'input[placeholder*="rechercher" i]',
                   'input[name="search"]'],
        "next": ['a[rel="next"]', 'button[aria-label="Next"]', 'a[aria-label="Next"]', '.pagination .next a',
                 'button:has-text("Next")', 'a:has-text("Next")'],
    },
}

EXISTS_WORDS = ("already", "exists", "déjà", "deja", "is taken", "already registered", "already in use", "est utilisé")
INVALID_EMAIL_WORDS = ("invalid email", "email invalide", "valid email", "e-mail invalide", "adresse e-mail invalide",
                       "enter a valid")
CONFIRM_WORDS = ("confirm your email", "check your email", "check your inbox", "verify your email", "verification link",
                 "confirmez votre", "vérifiez votre e-mail", "lien de confirmation")
TWOFA_WORDS = ("two-factor", "2fa", "verification code", "code de vérification", "authenticator")
CHALLENGE_WORDS = ("verify you are human", "checking your browser", "just a moment", "vérifiez que vous êtes humain")


# ======================================================================================== utilitaires purs
def load_selectors(path: str = "") -> Dict[str, Dict[str, List[str]]]:
    """Sélecteurs par défaut, remplacés clé par clé par le fichier JSON de l'opérateur."""
    sel = {g: {k: list(v) for k, v in d.items()} for g, d in DEFAULT_SELECTORS.items()}
    if path:
        try:
            for g, d in json.loads(Path(path).read_text(encoding="utf-8")).items():
                for k, v in d.items():
                    sel.setdefault(g, {})[k] = [v] if isinstance(v, str) else list(v)
        except (OSError, ValueError, AttributeError) as e:
            log.warning("fichier de sélecteurs ignoré (%s)", e)
    return sel


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


def mask_email(email: str) -> str:
    user, _, dom = email.partition("@")
    return (user[:2] + "***@" + dom) if user else email


def strong_password(n: int = 14) -> str:
    """Mot de passe aléatoire (lettres + chiffres, sans caractères ambigus) : au moins 2 majuscules, 2 minuscules, 2 chiffres."""
    up, lo, di = "ABCDEFGHJKMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789"
    chars = [secrets.choice(up) for _ in range(2)] + [secrets.choice(lo) for _ in range(2)] \
        + [secrets.choice(di) for _ in range(2)] + [secrets.choice(up + lo + di) for _ in range(max(0, n - 6))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


_ID_PATTERNS = (
    r'"?(?:uid|user_id|userId|trader_id|traderId)"?\s*[:=]\s*"?(\d{5,12})',
    r'data-(?:uid|user-id)=["\'](\d{5,12})',
    r"\bUser\s*ID\b[^0-9]{0,30}(\d{5,12})",
    r"\bID\s*[:#]?\s*(\d{5,12})\b",
)


def extract_trader_id(html: str) -> str:
    for pat in _ID_PATTERNS:
        m = re.search(pat, html or "", re.I)
        if m:
            return m.group(1)
    return ""


def normalize_trader_id(raw: str) -> str:
    """« ID: 12 345 678 » -> « 12345678 » ; chaîne vide si ce n'est pas un ID plausible (5 à 12 chiffres)."""
    digits = re.sub(r"\D", "", raw or "")
    return digits if 5 <= len(digits) <= 12 else ""


def classify_signup(st: dict) -> Optional[str]:
    """st : on_form, has_session, error_text, text (minuscules). Retourne un statut ou None (continuer à attendre)."""
    if any(w in st["text"] for w in CONFIRM_WORDS) and not st["has_session"]:
        return "confirm_email"
    if not st["on_form"]:
        return "ok" if st["has_session"] else "created_nosession"
    err = st["error_text"]
    if err:
        if any(w in err for w in EXISTS_WORDS):
            return "exists"
        if any(w in err for w in INVALID_EMAIL_WORDS):
            return "invalid_email"
        return "form_error"
    return None


def classify_login(st: dict) -> Optional[str]:
    if st["has_session"] and not st["on_form"]:
        return "ok"
    if any(w in st["text"] for w in TWOFA_WORDS) and not st["has_session"]:
        return "twofa"
    if any(w in st["text"] for w in CONFIRM_WORDS):
        return "confirm_email"
    if st["on_form"] and st["error_text"]:
        return "bad_credentials"
    return None


def _num(s: str) -> Optional[float]:
    m = re.search(r"-?\d[\d\s.,]*", s or "")
    if not m:
        return None
    t = m.group(0).strip().replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) <= 2 else t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def find_trader(tables: List[dict], trader_id: str) -> Tuple[bool, Optional[float]]:
    """Cherche l'ID exact dans les tableaux lus ; retourne (trouvé, somme des dépôts si la colonne est identifiable)."""
    for tb in tables:
        headers = [h.lower() for h in tb.get("headers", [])]
        dep_idx = None
        for i, h in enumerate(headers):                       # colonne « somme / montant des dépôts » de préférence
            if any(w in h for w in ("deposit", "dépôt", "depot")) and any(w in h for w in ("sum", "amount", "total", "$", "montant", "somme")):
                dep_idx = i
                break
        if dep_idx is None:
            for i, h in enumerate(headers):
                if any(w in h for w in ("deposit", "dépôt", "depot")) and not any(w in h for w in ("count", "nombre", "qty", "number", "#")):
                    dep_idx = i
                    break
        for row in tb.get("rows", []):
            if any(re.fullmatch(r"#?\s*%s\s*" % re.escape(trader_id), c or "") for c in row):
                dep = _num(row[dep_idx]) if dep_idx is not None and dep_idx < len(row) else None
                return True, dep
    return False, None


def tokens_for(packs: tuple, amount: float) -> int:
    """Jetons pour un dépôt de `amount` $ : le palier exact, sinon le ratio du plus grand palier atteint."""
    best = None
    for usd, tok in packs:
        if amount + 1e-9 >= usd:
            best = (usd, tok)
    return int(amount * best[1] / best[0]) if best else 0


# ======================================================================================== résultats
@dataclass
class WebResult:
    status: str                    # ok | exists | invalid_email | form_error | confirm_email | created_nosession | captcha
    #                                timeout | bad_credentials | twofa | unavailable | error
    detail: str = ""
    trader_id: str = ""
    session: str = ""              # valeur décodée du cookie de session
    submitted: bool = False        # le formulaire a été envoyé (un nouvel essai pourrait créer un doublon)
    diag: str = ""                 # chemin de la capture d'écran de diagnostic

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def ssid(self, demo: bool) -> str:
        return ssidlib.build_ssid(self.session, int(self.trader_id) if self.trader_id else 0, demo) if self.session else ""


@dataclass
class PartnerResult:
    status: str                    # found | not_found | config_error | unavailable | error
    detail: str = ""
    deposits: Optional[float] = None
    diag: str = ""


class _Transient(Exception):
    """Échec avant l'envoi du formulaire : on peut réessayer sans risque."""


# ======================================================================================== navigateur
class WebAutomation:
    def __init__(self, cfg, base_dir: Path):
        self.cfg = cfg
        self.dir = Path(base_dir)
        self.sel = load_selectors(cfg.selectors_file)
        self.sem = asyncio.Semaphore(max(1, cfg.web_concurrency))
        self.diag_dir = self.dir / "diag"

    # ---- disponibilité -------------------------------------------------------------------
    def available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    async def diagnose(self) -> Tuple[bool, str]:
        """Lance et ferme un navigateur : Playwright installé ET Chromium présent ?"""
        if not self.available():
            return False, "Playwright non installé (pip install playwright)"
        try:
            async with self.session() as (_, page):
                await page.set_content("<p>ok</p>")
            return True, "navigateur OK"
        except Exception as e:
            return False, self._explain(e)

    @staticmethod
    def _explain(e: Exception) -> str:
        m = str(e)
        if "Executable doesn't exist" in m or "playwright install" in m:
            return "Chromium absent : exécutez « playwright install chromium »"
        if "net::ERR" in m:
            return "site injoignable : " + m.split("net::")[-1].split()[0]
        return (m.splitlines()[0] if m else e.__class__.__name__)[:160]

    @asynccontextmanager
    async def session(self, storage_state: Optional[str] = None):
        """(contexte, page) ; tout est fermé quoi qu'il arrive ; le nombre de navigateurs simultanés est limité."""
        from playwright.async_api import async_playwright
        async with self.sem:
            pw = await async_playwright().start()
            browser = ctx = None
            try:
                kw = {"headless": self.cfg.web_headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
                if self.cfg.web_proxy:
                    kw["proxy"] = {"server": self.cfg.web_proxy}
                browser = await pw.chromium.launch(**kw)
                ctx = await browser.new_context(storage_state=storage_state, locale="fr-FR",
                                                viewport={"width": 1280, "height": 900})
                ctx.set_default_timeout(self.cfg.web_timeout * 1000)
                page = await ctx.new_page()
                yield ctx, page
            finally:
                for closer in (ctx and ctx.close, browser and browser.close, pw.stop):
                    if closer:
                        try:
                            await asyncio.wait_for(closer(), 15)
                        except Exception:
                            pass

    async def _diag(self, page, name: str) -> str:
        try:
            self.diag_dir.mkdir(parents=True, exist_ok=True)
            path = self.diag_dir / f"{int(time.time())}-{name}.png"
            await page.screenshot(path=str(path), full_page=True)
            old = sorted(self.diag_dir.glob("*.png"))[:-40]
            for p in old:
                p.unlink(missing_ok=True)
            return str(path)
        except Exception:
            return ""

    # ---- lecture de la page --------------------------------------------------------------
    async def _first(self, page, cands: List[str], wait: float = 0.0):
        """Premier élément VISIBLE parmi les candidats (attente jusqu'à `wait` secondes)."""
        deadline = time.monotonic() + wait
        while True:
            for c in cands:
                try:
                    loc = page.locator(c).first
                    if await loc.is_visible():
                        return loc
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.25)

    async def _dismiss_banner(self, page) -> None:
        loc = await self._first(page, self.sel["common"]["banner"])
        if loc:
            try:
                await loc.click(timeout=2000)
            except Exception:
                pass

    async def _fill(self, loc, value: str) -> None:
        await loc.fill(value)
        try:
            if await loc.input_value() != value:                # champs « maîtrisés » par JS : saisie touche par touche
                await loc.click()
                await loc.press_sequentially(value, delay=15)
        except Exception:
            pass

    async def _state(self, page, ctx, group: str) -> dict:
        """Photographie de la page : formulaire encore visible ? erreur affichée ? cookie de session présent ?"""
        sel = self.sel
        try:
            text = (await page.inner_text("body")).lower()
        except Exception:
            text = ""
        err = ""
        for c in sel["common"]["error"]:
            try:
                loc = page.locator(c)
                for i in range(min(await loc.count(), 4)):
                    if await loc.nth(i).is_visible():
                        err += " " + (await loc.nth(i).inner_text()).lower()
            except Exception:
                continue
        cookie = ""
        try:
            for ck in await ctx.cookies():
                if ck["name"] == self.cfg.po_session_cookie and ck.get("value"):
                    cookie = ck["value"]
        except Exception:
            pass
        return {"text": text, "error_text": err.strip(), "has_session": bool(cookie), "cookie": cookie,
                "on_form": bool(await self._first(page, sel[group]["email"])),
                "captcha": bool(await self._first(page, sel["common"]["captcha"])),
                "challenge": any(w in text for w in CHALLENGE_WORDS)}

    async def _poll(self, page, ctx, group: str, classify, timeout: float):
        deadline = time.monotonic() + timeout
        st = {}
        while time.monotonic() < deadline:
            st = await self._state(page, ctx, group)
            if st["challenge"]:
                return "captcha", st
            verdict = classify(st)
            if verdict:
                return verdict, st
            await asyncio.sleep(0.5)
        return ("captcha" if st.get("captcha") else "timeout"), st

    async def _trader_id(self, page) -> str:
        tid = extract_trader_id(await page.content())
        if not tid and self.cfg.po_profile_url:
            try:
                await page.goto(self.cfg.po_profile_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(800)
                tid = extract_trader_id(await page.content())
            except Exception:
                pass
        return tid

    # ---- création de compte ---------------------------------------------------------------
    async def signup(self, email: str, password: str, accept_terms: bool) -> WebResult:
        if not self.available():
            return WebResult("unavailable", "Playwright non installé")
        if not self.cfg.po_register_url:
            return WebResult("unavailable", "PO_REGISTER_URL non configuré")
        if not accept_terms:
            return WebResult("error", "conditions non acceptées par l'utilisateur")
        last = ""
        for attempt in range(2):
            try:
                return await self._signup_once(email, password)
            except _Transient as e:                            # avant l'envoi : nouvel essai possible
                last = str(e)
                await asyncio.sleep(2.0 * (attempt + 1))
            except Exception as e:
                return WebResult("unavailable" if "Executable doesn't exist" in str(e) else "error", self._explain(e))
        return WebResult("error", last or "échec avant l'envoi du formulaire")

    async def _signup_once(self, email: str, password: str) -> WebResult:
        reg = self.sel["register"]
        async with self.session() as (ctx, page):
            try:
                await page.goto(self.cfg.po_register_url, wait_until="domcontentloaded")
                await self._dismiss_banner(page)
                f_email = await self._first(page, reg["email"], wait=15)
                f_pass = await self._first(page, reg["password"], wait=5)
                if not f_email or not f_pass:
                    diag = await self._diag(page, "signup-form")
                    st = await self._state(page, ctx, "register")
                    if st["captcha"] or st["challenge"]:
                        return WebResult("captcha", "contrôle anti-robot avant le formulaire", diag=diag)
                    raise _Transient("formulaire d'inscription introuvable")
                await self._fill(f_email, email)
                await self._fill(f_pass, password)
                f_pass2 = await self._first(page, reg["password2"])
                if f_pass2:
                    await self._fill(f_pass2, password)
                for c in reg["terms"]:                          # case « conditions » : cochée uniquement ici, après consentement
                    box = page.locator(c).first
                    if await box.count():
                        try:
                            await box.check(force=True, timeout=3000)
                            break
                        except Exception:
                            continue
            except _Transient:
                raise
            except Exception as e:
                if "Executable doesn't exist" in str(e):
                    raise
                raise _Transient(self._explain(e))
            # ---- envoi : plus de nouvel essai possible à partir d'ici
            try:
                btn = await self._first(page, reg["submit"])
                if btn:
                    await btn.click()
                else:
                    await f_pass.press("Enter")
                verdict, st = await self._poll(page, ctx, "register", classify_signup, self.cfg.web_timeout)
                diag = "" if verdict == "ok" else await self._diag(page, "signup-" + verdict)
                res = WebResult(verdict, st.get("error_text", "")[:200], submitted=True, diag=diag)
                if verdict in ("ok", "created_nosession"):
                    res.status = "ok" if st.get("cookie") else "created_nosession"
                    res.session = unquote(st["cookie"]) if st.get("cookie") else ""
                    res.trader_id = await self._trader_id(page)
                return res
            except Exception as e:
                return WebResult("timeout", self._explain(e), submitted=True, diag=await self._diag(page, "signup-error"))

    # ---- connexion : récupération de la session (SSID) ------------------------------------
    async def login(self, email: str, password: str) -> WebResult:
        if not self.available():
            return WebResult("unavailable", "Playwright non installé")
        last = ""
        for attempt in range(2):
            try:
                return await self._login_once(email, password)
            except _Transient as e:
                last = str(e)
                await asyncio.sleep(2.0 * (attempt + 1))
            except Exception as e:
                return WebResult("unavailable" if "Executable doesn't exist" in str(e) else "error", self._explain(e))
        return WebResult("error", last or "échec de la connexion au site")

    async def _login_once(self, email: str, password: str) -> WebResult:
        lg = self.sel["login"]
        async with self.session() as (ctx, page):
            try:
                await page.goto(self.cfg.po_login_url, wait_until="domcontentloaded")
                await self._dismiss_banner(page)
                f_email = await self._first(page, lg["email"], wait=15)
                f_pass = await self._first(page, lg["password"], wait=5)
                if not f_email or not f_pass:
                    st = await self._state(page, ctx, "login")
                    if st["captcha"] or st["challenge"]:
                        return WebResult("captcha", "contrôle anti-robot", diag=await self._diag(page, "login-captcha"))
                    raise _Transient("formulaire de connexion introuvable")
                await self._fill(f_email, email)
                await self._fill(f_pass, password)
            except _Transient:
                raise
            except Exception as e:
                raise _Transient(self._explain(e))
            try:
                btn = await self._first(page, lg["submit"])
                await (btn.click() if btn else f_pass.press("Enter"))
                verdict, st = await self._poll(page, ctx, "login", classify_login, self.cfg.web_timeout)
                diag = "" if verdict == "ok" else await self._diag(page, "login-" + verdict)
                res = WebResult(verdict, st.get("error_text", "")[:200], submitted=True, diag=diag)
                if verdict == "ok":
                    res.session = unquote(st["cookie"])
                    res.trader_id = await self._trader_id(page)
                return res
            except Exception as e:
                return WebResult("timeout", self._explain(e), submitted=True, diag=await self._diag(page, "login-error"))


# ======================================================================================== espace partenaire
class PartnerChecker:
    """Vérifie qu'un ID Pocket Option figure bien dans VOS filleuls (pocketpartners › Statistics › Traders)."""

    _JS = """() => {
      const out = [];
      document.querySelectorAll('table').forEach(t => {
        const headers = [...t.querySelectorAll('thead th, tr:first-child th')].map(x => x.innerText.trim());
        const rows = [...t.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
                       .filter(r => r.length);
        out.push({headers, rows});
      });
      if (!out.length) {
        const rows = [...document.querySelectorAll('[role=row]')]
          .map(r => [...r.querySelectorAll('[role=cell],[role=gridcell]')].map(c => c.innerText.trim())).filter(r => r.length);
        out.push({headers: [...document.querySelectorAll('[role=columnheader]')].map(x => x.innerText.trim()), rows});
      }
      return out;
    }"""

    def __init__(self, cfg, web: WebAutomation, max_pages: int = 10):
        self.cfg, self.web, self.max_pages = cfg, web, max_pages
        self.state_file = web.dir / "partner_state.json"
        self._cache: Dict[str, Tuple[float, PartnerResult]] = {}
        self._lock = asyncio.Lock()

    def configured(self) -> bool:
        return bool(self.cfg.partners_email and self.cfg.partners_password and self.cfg.partners_stats_url)

    async def check(self, trader_id: str, fresh: bool = False) -> PartnerResult:
        if not self.configured():
            return PartnerResult("config_error", "identifiants pocketpartners absents du .env")
        if not self.web.available():
            return PartnerResult("unavailable", "Playwright non installé")
        hit = self._cache.get(trader_id)
        if hit and not fresh and time.time() - hit[0] < (60 if hit[1].status == "found" else 20):
            return hit[1]
        async with self._lock:                                 # une seule session partenaire à la fois
            res = PartnerResult("error", "échec")
            for attempt in range(2):
                try:
                    res = await self._check_once(trader_id)
                    break
                except Exception as e:
                    res = PartnerResult("unavailable" if "Executable doesn't exist" in str(e) else "error",
                                        self.web._explain(e))
                    await asyncio.sleep(1.5 * (attempt + 1))
            if res.status in ("found", "not_found"):
                self._cache[trader_id] = (time.time(), res)
            return res

    async def _needs_login(self, page) -> bool:
        return bool(await self.web._first(page, self.web.sel["partners"]["password"]))

    async def _login(self, page, ctx) -> bool:
        pt = self.web.sel["partners"]
        await page.goto(self.cfg.partners_login_url, wait_until="domcontentloaded")
        await self.web._dismiss_banner(page)
        f_email = await self.web._first(page, pt["email"], wait=15)
        f_pass = await self.web._first(page, pt["password"], wait=5)
        if not f_email or not f_pass:
            return False
        await self.web._fill(f_email, self.cfg.partners_email)
        await self.web._fill(f_pass, self.cfg.partners_password)
        btn = await self.web._first(page, pt["submit"])
        await (btn.click() if btn else f_pass.press("Enter"))
        for _ in range(int(self.cfg.web_timeout * 2)):
            await asyncio.sleep(0.5)
            if not await self._needs_login(page):
                return True
        return False

    async def _check_once(self, trader_id: str) -> PartnerResult:
        pt = self.web.sel["partners"]
        storage = str(self.state_file) if self.state_file.exists() else None
        async with self.web.session(storage_state=storage) as (ctx, page):
            await page.goto(self.cfg.partners_stats_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(600)
            if await self._needs_login(page):                   # session expirée ou première connexion
                if not await self._login(page, ctx):
                    return PartnerResult("config_error", "connexion pocketpartners refusée (identifiants ?)",
                                         diag=await self.web._diag(page, "partners-login"))
                try:
                    await ctx.storage_state(path=str(self.state_file))
                    self.state_file.chmod(0o600)
                except Exception:
                    pass
                await page.goto(self.cfg.partners_stats_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(600)
                if await self._needs_login(page):
                    return PartnerResult("config_error", "pocketpartners redemande la connexion",
                                         diag=await self.web._diag(page, "partners-relogin"))
            search = await self.web._first(page, pt["search"])
            if search:                                          # filtre côté site quand il existe
                try:
                    await search.fill(trader_id)
                    await search.press("Enter")
                    await page.wait_for_timeout(1200)
                except Exception:
                    pass
            seen = None
            for _ in range(self.max_pages):
                try:
                    await page.wait_for_selector("table tbody tr, [role=row]", timeout=self.cfg.web_timeout * 1000)
                except Exception:
                    break
                tables = await page.evaluate(self._JS)
                found, dep = find_trader(tables, trader_id)
                if found:
                    return PartnerResult("found", deposits=dep)
                sig = json.dumps(tables)[:2000]
                nxt = await self.web._first(page, pt["next"])
                if not nxt or sig == seen:
                    break
                seen = sig
                try:
                    await nxt.click(timeout=3000)
                    await page.wait_for_timeout(900)
                except Exception:
                    break
            return PartnerResult("not_found", diag=await self.web._diag(page, "partners-notfound"))
