"""Faux sites (inscription, connexion, espace partenaire) servis en local pour tester Playwright pour de vrai."""
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

PHPSESS = 'a:4:{s:10:"session_id";s:32:"%s";s:10:"ip_address";s:7:"1.2.3.4";s:10:"user_agent";s:4:"test";s:13:"last_activity";i:1700000000;}%s'


def trader_id_for(email: str) -> str:
    return str(10000000 + int(hashlib.md5(email.encode()).hexdigest(), 16) % 80000000)


def session_for(email: str) -> str:
    h = hashlib.md5(email.encode()).hexdigest()
    return PHPSESS % (h, h)


def page(body: str, title: str = "x") -> bytes:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode()


BANNER = '<div><button id="onetrust-accept-btn-handler" onclick="this.parentNode.remove()">Accept all</button></div>'


def form(action, err="", extra="", terms=True, pw2=False, title="Sign up"):
    return page(f"""{BANNER}<h1>{title}</h1><p>Already have an account? <a href="/login">Log in</a></p>
    {'<div class="error">%s</div>' % err if err else ''}
    <form method="post" action="{action}">
      <input type="email" name="email"><input type="password" name="password">
      {'<input type="password" name="password_confirm">' if pw2 else ''}
      {'<label><input type="checkbox" name="agree_terms"> I accept the terms</label>' if terms else ''}
      {extra}<button type="submit">{title}</button></form>""")


ROWS = {1: [("11111111", "2024-05-01", "DZ", "2", "$1,250.50"), ("111111111", "2024-05-02", "FR", "1", "$10.00"),
            ("22222222", "2024-05-03", "SN", "0", "$0.00")],
        2: [("33333333", "2024-05-04", "CI", "1", "$75.00")]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code=200, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to, cookie=None, who=None):
        self.send_response(302)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if who:
            self.send_header("Set-Cookie", f"who={quote(who, safe='')}; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        S = self.server
        if u.path == "/register":
            self._send(200, form("/register", pw2="confirm" in q.get("variant", [""])))
        elif u.path == "/login":
            self._send(200, form("/login", terms=False, title="Log in"))
        elif u.path == "/cabinet/":
            if not self._cookie("ci_session"):
                return self._redirect("/login")
            from urllib.parse import unquote
            email = unquote(self._cookie("who"))
            idtxt = "" if "noid" in email else f'<div class="profile">ID: {trader_id_for(email)}</div>'
            self._send(200, page(f"<h1>Trading cabinet</h1>{idtxt}"))
        elif u.path == "/cabinet/profile/":
            from urllib.parse import unquote
            email = unquote(self._cookie("who")) or S.last_email
            self._send(200, page(f"<h1>Profile</h1><p>User ID {trader_id_for(email)}</p>"))
        elif u.path == "/welcome":
            self._send(200, page("<h1>Welcome! Your account has been created.</h1>"))
        elif u.path == "/partners/login":
            self._send(200, form("/partners/login", terms=False, title="Partner login"))
        elif u.path == "/partners/stats":
            if self._cookie("pp") != "1":
                return self._redirect("/partners/login")
            n = int(q.get("page", ["1"])[0])
            rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td><td>{e}</td></tr>" for a, b, c, d, e in ROWS.get(n, []))
            nxt = '<a rel="next" href="/partners/stats?page=2">Next</a>' if n == 1 else ""
            self._send(200, page(f"""<h1>Statistics</h1><table><thead><tr><th>Trader ID</th><th>Registration</th>
              <th>Country</th><th>Deposits count</th><th>Deposits sum ($)</th></tr></thead><tbody>{rows}</tbody></table>{nxt}"""))
        else:
            self._send(404, page("not found"))

    def do_POST(self):
        u = urlparse(self.path)
        S = self.server
        data = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        email = data.get("email", [""])[0]
        pw = data.get("password", [""])[0]
        S.last_email = email or S.last_email
        if u.path == "/register":
            S.counts["register_post"] += 1
            if "agree_terms" not in data:
                return self._send(200, form("/register", err="You must accept the terms"))
            if "exists" in email:
                return self._send(200, form("/register", err="This email is already registered"))
            if "bademail" in email:
                return self._send(200, form("/register", err="Invalid email address"))
            if "captcha" in email:
                return self._send(200, form("/register", extra='<div class="g-recaptcha" style="width:300px;height:78px;display:block">captcha</div>'))
            if "confirm" in email:
                return self._send(200, page("<h1>Please check your email: we sent you a verification link.</h1>"))
            if "nocookie" in email:
                return self._redirect("/welcome")
            if "slow" in email:
                time.sleep(1.5)
            self._redirect("/cabinet/", f"ci_session={quote(session_for(email), safe='')}; Path=/", email)
        elif u.path == "/login":
            S.counts["login_post"] += 1
            if pw == "wrong":
                return self._send(200, form("/login", err="Invalid email or password", terms=False, title="Log in"))
            if "2fa" in email:
                return self._send(200, page("<h1>Enter the verification code from your authenticator app</h1>"))
            if "confirm" in email:
                return self._send(200, page("<h1>Please confirm your email first</h1>"))
            self._redirect("/cabinet/", f"ci_session={quote(session_for(email), safe='')}; Path=/", email)
        elif u.path == "/partners/login":
            S.counts["partner_login"] += 1
            if pw == "badpartner":
                return self._send(200, form("/partners/login", err="Invalid credentials", terms=False, title="Partner login"))
            self._redirect("/partners/stats", "pp=1; Path=/")
        else:
            self._send(404, page("not found"))


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def start():
    srv = _Server(("127.0.0.1", 0), Handler)
    srv.counts = {"register_post": 0, "login_post": 0, "partner_login": 0}
    srv.last_email = ""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
