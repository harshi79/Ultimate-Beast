# modules/owa_scanner.py
import asyncio
import sys
import time
import re

from core.common import (
    banner, clear, session_dir, Writer, Stats, select_file,
    read_lines, dedupe, ask_threads, Fore, Style, ROOT
)
from core.proxy import ProxyPool, load_pool_interactive

try:
    import aiodns
    import aiohttp
    from aiohttp_socks import ProxyConnector
    HAS_AIO = True
except ImportError:
    HAS_AIO = False

OFFICE_MX = {'office365.com', 'outlook.com', 'protection.outlook.com', 'microsoftonline.com'}
GODADDY_MX = {'secureserver.net'}
CONSUMER = {'live.com', 'hotmail.com', 'outlook.com', 'msn.com'}

async def check_mx(resolver, domain):
    try:
        ans = await resolver.query_dns(domain, 'MX')
    except Exception:
        return None, None
    for mx in ans:
        s = mx.host.lower()
        if any(p in s for p in OFFICE_MX):
            return 'office', s
        if any(p in s for p in GODADDY_MX):
            return 'godaddy', s
    return None, None

async def check_autodiscover(session, domain):
    url = f"https://autodiscover.{domain}/autodiscover/autodiscover.xml"
    try:
        async with session.head(url, ssl=False, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=3)) as r:
            if r.status in (200, 302, 401):
                return True
    except Exception:
        pass
    return False

async def check_email(resolver, session, email, sem, writer, stats):
    async with sem:
        if '@' not in email:
            stats.add("checked"); return
        domain = email.split('@', 1)[1].lower()

        if domain in CONSUMER:
            writer.write("outlook.txt", email)
            stats.add("outlook"); stats.add("checked")
            print(f"{Fore.CYAN}[+]{Style.RESET_ALL} {email} {Fore.LIGHTBLACK_EX}| consumer{Style.RESET_ALL}")
            return

        mx_task = asyncio.create_task(check_mx(resolver, domain))
        ad_task = asyncio.create_task(check_autodiscover(session, domain))
        provider, mx = await mx_task
        ad = await ad_task

        stats.add("checked")
        if provider == 'office' or ad:
            writer.write("office.txt", email)
            stats.add("office")
            print(f"{Fore.BLUE}[+]{Style.RESET_ALL} {email} {Fore.LIGHTBLACK_EX}| office | {mx or 'autodiscover'}{Style.RESET_ALL}")
        elif provider == 'godaddy':
            writer.write("godaddy.txt", email)
            stats.add("godaddy")
            print(f"{Fore.YELLOW}[+]{Style.RESET_ALL} {email} {Fore.LIGHTBLACK_EX}| godaddy{Style.RESET_ALL}")

async def bulk(emails, pool: ProxyPool, threads, writer, stats, name="owa"):
    resolver = aiodns.DNSResolver()
    sem = asyncio.Semaphore(threads)

    if len(pool):
        # pick one proxy for connector — aiohttp_socks uses one connector per session
        p = pool.get()
        try:
            connector = ProxyConnector.from_url(p, ssl=False, limit=threads, limit_per_host=50)
        except Exception:
            connector = aiohttp.TCPConnector(limit=threads, limit_per_host=50, ssl=False)
    else:
        connector = aiohttp.TCPConnector(limit=threads, limit_per_host=50, ssl=False)

    timeout = aiohttp.ClientTimeout(total=5, connect=3, sock_read=3)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [check_email(resolver, session, e, sem, writer, stats) for e in emails]
        await asyncio.gather(*tasks, return_exceptions=True)

def main():
    clear()
    banner("owa scanner")

    if not HAS_AIO:
        print(f"{Fore.RED}[!] missing aiodns / aiohttp / aiohttp_socks{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}pip install aiodns aiohttp aiohttp-socks{Style.RESET_ALL}")
        input("enter...")
        return

    path = select_file("select email list (one per line)")
    if not path:
        print(f"{Fore.RED}[!] no file{Style.RESET_ALL}"); input("enter..."); return

    emails = [l for l in read_lines(path) if '@' in l]
    emails, dupes = dedupe(emails)
    if not emails:
        print(f"{Fore.RED}[!] no valid emails{Style.RESET_ALL}"); input("enter..."); return

    pool = load_pool_interactive()
    threads = ask_threads(500)

    out = session_dir("owa")
    writer = Writer(out)
    stats = Stats(total=len(emails), keys=["office", "outlook", "godaddy"])

    print(f"{Fore.CYAN}[*] emails {len(emails)} | dupes {dupes} | threads {threads} "
          f"| proxies {len(pool)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[*] results -> {out}{Style.RESET_ALL}\n")

    start = time.time()
    try:
        asyncio.run(bulk(emails, pool, threads, writer, stats))
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] interrupted{Style.RESET_ALL}")

    writer.close()
    elapsed = time.time() - start
    print(f"\n{Fore.GREEN}[+] done in {elapsed:.1f}s | office {stats.get('office')} "
          f"| outlook {stats.get('outlook')} | godaddy {stats.get('godaddy')}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[+] {out}{Style.RESET_ALL}")
    input("enter...")

if __name__ == "__main__":
    main()
