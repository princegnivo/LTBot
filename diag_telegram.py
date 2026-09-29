"""
diag_telegram.py — mesure où part le temps quand tu cliques sur un bouton.
Lancer :  python3 diag_telegram.py
"""
import socket, time, requests, config

HOST = "api.telegram.org"
print("== DNS ==")
t = time.time()
infos = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
print(f"résolution : {time.time()-t:.2f}s")
for fam, _, _, _, addr in infos:
    name = "IPv6" if fam == socket.AF_INET6 else "IPv4"
    t = time.time()
    try:
        s = socket.create_connection((addr[0], 443), timeout=8); s.close()
        print(f"{name} {addr[0]} : connexion TCP en {time.time()-t:.2f}s")
    except Exception as e:
        print(f"{name} {addr[0]} : ÉCHEC après {time.time()-t:.2f}s ({e})")

url = f"https://{HOST}/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
print("\n== getMe, nouvelle connexion à chaque fois (ancien comportement) ==")
for _ in range(3):
    t = time.time(); requests.get(url, timeout=20); print(f"{time.time()-t:.2f}s")
print("\n== getMe, connexion réutilisée (nouveau comportement) ==")
s = requests.Session()
for _ in range(3):
    t = time.time(); s.get(url, timeout=20); print(f"{time.time()-t:.2f}s")
print("\nSi 'IPv6 ÉCHEC/lent' ou si l'ancien mode est bien plus lent que le nouveau : "
      "c'était la cause. Si tout est lent (> 1.5 s) : problème réseau/VPN.")
