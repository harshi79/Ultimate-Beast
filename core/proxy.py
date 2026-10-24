# core/proxy.py
import re
import time
import random
import threading
from pathlib import Path
from colorama import Fore, Style

class ProxyPool:
    """
    Proxy pool with rotation, cooldown on failures, and health stats.
    Accepts formats:
        host:port
        host:port:user:pass
        user:pass@host:port
        scheme://host:port
        scheme://user:pass@host:port
    """

    def __init__(self, proxies=None, default_scheme="http", max_fails=3, cooldown=120):
        self.default_scheme = default_scheme
        self.max_fails = max_fails
        self.cooldown = cooldown
        self._lock = threading.Lock()
        self._proxies = []
        self._fails = {}
        self._cooldown_until = {}
        self._uses = {}
        if proxies:
            for p in proxies:
                self.add(p)

    # ── loading ─────────────────────────────
    @classmethod
    def from_file(cls, path, default_scheme="http"):
        pool = cls(default_scheme=default_scheme)
        p = Path(path)
        if not p.exists():
            return pool
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pool.add(line)
        return pool

    def add(self, raw):
        url = self._format(raw)
        if not url:
            return
        with self._lock:
            if url not in self._proxies:
                self._proxies.append(url)
                self._fails[url] = 0
                self._cooldown_until[url] = 0
                self._uses[url] = 0

    def _format(self, raw):
        raw = raw.strip()
        if not raw:
            return None
        # already a full url
        if re.match(r'^(http|https|socks4|socks5)://', raw, re.I):
            return raw
        # user:pass@host:port
        if '@' in raw:
            return f"{self.default_scheme}://{raw}"
        parts = raw.split(':')
        if len(parts) == 2:
            # host:port
            return f"{self.default_scheme}://{parts[0]}:{parts[1]}"
        if len(parts) == 4:
            # host:port:user:pass
            host, port, user, pw = parts
            return f"{self.default_scheme}://{user}:{pw}@{host}:{port}"
        return None

    # ── selection ───────────────────────────
    def __len__(self):
        with self._lock:
            return len(self._proxies)

    def alive(self):
        now = time.time()
        with self._lock:
            return [p for p in self._proxies if self._cooldown_until[p] <= now]

    def get(self):
        """Return a random healthy proxy URL, or None."""
        now = time.time()
        with self._lock:
            if not self._proxies:
                return None
            healthy = [p for p in self._proxies if self._cooldown_until[p] <= now]
            if not healthy:
                return None
            p = random.choice(healthy)
            self._uses[p] += 1
            return p

    def requests_dict(self):
        """Return requests-style proxies dict or None."""
        p = self.get()
        if not p:
            return None
        return {"http": p, "https": p}

    # ── feedback ────────────────────────────
    def mark_fail(self, url):
        if not url:
            return
        with self._lock:
            self._fails[url] = self._fails.get(url, 0) + 1
            if self._fails[url] >= self.max_fails:
                self._cooldown_until[url] = time.time() + self.cooldown

    def mark_ok(self, url):
        if not url:
            return
        with self._lock:
            self._fails[url] = 0
            self._cooldown_until[url] = 0

    def summary(self):
        now = time.time()
        with self._lock:
            total = len(self._proxies)
            dead = sum(1 for p in self._proxies if self._cooldown_until[p] > now)
            return f"total {total} | alive {total - dead} | cooldown {dead}"

    def stats(self):
        with self._lock:
            return {p: {"uses": self._uses[p], "fails": self._fails[p]} for p in self._proxies}


def load_pool_interactive():
    """Ask the user how to load proxies."""
    from .common import select_file, Fore, Style

    print(f"{Fore.CYAN}[proxy] 1 = file, 2 = direct, 3 = paste list{Style.RESET_ALL}")
    c = input(f"{Fore.CYAN}select [2]: {Style.RESET_ALL}").strip() or "2"

    if c == "1":
        path = select_file("select proxy file")
        if not path:
            print(f"{Fore.YELLOW}[!] no file, going direct{Style.RESET_ALL}")
            return ProxyPool()
        scheme = input(f"{Fore.CYAN}[proxy] default scheme (http/socks5) [http]: {Style.RESET_ALL}").strip() or "http"
        pool = ProxyPool.from_file(path, default_scheme=scheme)
        print(f"{Fore.GREEN}[+] loaded {len(pool)} proxies | {pool.summary()}{Style.RESET_ALL}")
        return pool

    if c == "3":
        print(f"{Fore.CYAN}paste one per line, blank line to finish:{Style.RESET_ALL}")
        lines = []
        while True:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                break
            lines.append(line)
        scheme = input(f"{Fore.CYAN}[proxy] default scheme [http]: {Style.RESET_ALL}").strip() or "http"
        pool = ProxyPool(default_scheme=scheme)
        for l in lines:
            pool.add(l)
        print(f"{Fore.GREEN}[+] loaded {len(pool)} proxies | {pool.summary()}{Style.RESET_ALL}")
        return pool

    print(f"{Fore.YELLOW}[!] direct connection{Style.RESET_ALL}")
    return ProxyPool()
