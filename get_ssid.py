"""Récupère votre SSID Pocket Option (repris de PocketOptionAPI-v2 : capture des cookies via pywebview).

Usage :  python get_ssid.py demo     |     python get_ssid.py real
Une fenêtre s'ouvre : connectez-vous à Pocket Option ; le SSID s'affiche ensuite dans le terminal.
Envoyez-le à votre bot avec  /ssid demo <SSID>  (ou mettez-le dans .env). Ne le partagez jamais.
"""
import sys
import time
import urllib.parse

import requests
import webview  # pip install pywebview

DEMO = (sys.argv[1] if len(sys.argv) > 1 else "demo").lower() != "real"
URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low"
NEEDED = ("ci_session", "afUserId", "ttcsid", "_scid", "_scid_r", "_twpid", "lo_uid")
result = {}


def read_cookies(window):
    for _ in range(250):
        cookies = {}
        for c in window.get_cookies():
            name, _, value = c.output().split(";")[0].split("-Cookie: ")[1].partition("=")
            cookies[name] = value
        if all(k in cookies for k in NEEDED):
            r = requests.get(URL, cookies=cookies, timeout=20)
            if r.status_code == 200 and 'demoSessionId":"' in r.text:
                sess = r.text.split('demoSessionId":"')[1].split('"')[0]
                uid = r.text.split('uid":')[1].split(",")[0]
                if DEMO:
                    result["ssid"] = '42["auth",{"session":"%s","isDemo":1,"uid":%s,"platform":2,"isFastHistory":true,"isOptimized":true}]' % (sess, uid)
                else:
                    real = urllib.parse.unquote(cookies["ci_session"]).replace('"', '\\"')
                    result["ssid"] = '42["auth",{"session":"%s","isDemo":0,"uid":%s,"platform":2,"isFastHistory":true,"isOptimized":true}]' % (real, uid)
                window.destroy()
                return
        time.sleep(1)
    window.destroy()


if __name__ == "__main__":
    w = webview.create_window("Connexion Pocket Option", URL)
    webview.start(read_cookies, w, private_mode=False)
    print(result.get("ssid") or "SSID introuvable (connexion non terminée ?)")
