"""Diagnostic des pages web utilisées par le bot :  python probe_web.py   (ou :  python probe_web.py --show)

Ouvre chaque adresse (inscription, connexion, pocketpartners) dans Chromium et affiche : durée, code HTTP, adresse finale
(après redirections), titre de la page, et si une protection anti-robot est détectée. Une capture d'écran de chaque page est
enregistrée dans data/diag/. Rien n'est saisi ni envoyé : c'est de la lecture seule.

  --show   ouvre une vraie fenêtre (WEB_HEADLESS=0) : vous voyez ce que voit le bot.
"""
import asyncio
import dataclasses
import sys
import time

from config import load_config
import po_web

CHALLENGE_HINTS = ("just a moment", "un instant", "verify you are human", "checking your browser", "attention required",
                   "access denied", "captcha")


async def probe(label: str, url: str, web: "po_web.WebAutomation") -> None:
    if not url:
        print(f"⚪ {label} : adresse non configurée dans .env")
        return
    t0 = time.time()
    try:
        async with web.session() as (ctx, page):
            resp = None
            try:
                resp = await page.goto(url, wait_until="commit", timeout=web.cfg.web_timeout * 1000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=20000)
                    dom = "DOM chargé"
                except Exception:
                    dom = "DOM lent (>20 s)"
            except Exception as e:
                print(f"❌ {label} : {web._explain(e)}  [{time.time() - t0:.1f}s]\n   adresse : {url}")
                return
            await page.wait_for_timeout(1500)
            title = (await page.title()) or ""
            body = ""
            try:
                body = (await page.inner_text("body"))[:600].lower()
            except Exception:
                pass
            blocked = any(h in title.lower() or h in body for h in CHALLENGE_HINTS)
            shot = await web._diag(page, "probe-" + label.split()[0].lower())
            status = resp.status if resp else "?"
            icon = "⚠️" if blocked or (isinstance(status, int) and status >= 400) else "✅"
            print(f"{icon} {label} : HTTP {status} · {dom} · {time.time() - t0:.1f}s\n"
                  f"   titre : {title[:80]!r}\n   adresse finale : {page.url[:110]}"
                  + ("\n   ⛔ page de contrôle anti-robot détectée : le bot ne la contourne pas (voir HEBERGEMENT.md)" if blocked else "")
                  + (f"\n   capture : {shot}" if shot else ""))
    except Exception as e:
        print(f"❌ {label} : {web._explain(e)}")


async def main() -> None:
    cfg = load_config()
    if "--show" in sys.argv:
        cfg = dataclasses.replace(cfg, web_headless=False)
    web = po_web.WebAutomation(cfg, cfg.data_dir)
    if not web.available():
        print("❌ Playwright n'est pas installé : pip install -r requirements.txt && python -m playwright install chromium")
        return
    print(f"Navigateur : {'visible' if not cfg.web_headless else 'invisible'} · délai {cfg.web_timeout}s · "
          f"proxy {'oui' if cfg.web_proxy else 'non'}\n")
    for label, url in (("inscription (PO_REGISTER_URL)", cfg.po_register_url), ("connexion (PO_LOGIN_URL)", cfg.po_login_url),
                       ("pocketpartners (connexion)", cfg.partners_login_url), ("pocketpartners (stats)", cfg.partners_stats_url)):
        await probe(label, url, web)


if __name__ == "__main__":
    asyncio.run(main())
