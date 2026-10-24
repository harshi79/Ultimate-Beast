# core/common.py
import os, sys, re, time, ctypes, threading, random
from datetime import datetime
from pathlib import Path
from colorama import init, Fore, Style

init(autoreset=True)

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "Results"
RESULTS.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
#  terminal
# ──────────────────────────────────────────────

def set_title(t):
    try:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetConsoleTitleW(t)
        else:
            sys.stdout.write(f"\x1b]2;{t}\x07")
            sys.stdout.flush()
    except Exception:
        pass

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

BANNER = r"""
██╗   ██╗██████╗ ██████╗ ██╗  ██╗
██║   ██║██╔══██╗██╔══██╗██║  ██║
██║   ██║██████╔╝██║  ██║███████║
██║   ██║██╔═══╝ ██║  ██║██╔══██║
╚██████╔╝██║     ██████╔╝██║  ██║
 ╚═════╝ ╚═╝     ╚═════╝ ╚═╝  ╚═╝
"""

def banner(sub=""):
    print(Fore.LIGHTBLUE_EX + BANNER + Style.RESET_ALL)
    if sub:
        print(Fore.WHITE + f"  {sub}" + Style.RESET_ALL)
    print()

def session_dir(tag):
    now = datetime.now()
    base = f"{tag}_{now:%Y-%m-%d_%H-%M-%S}"
    p = RESULTS / base
    i = 1
    while p.exists():
        p = RESULTS / f"{base}_{i}"
        i += 1
    p.mkdir(parents=True)
    return p

# ──────────────────────────────────────────────
#  instant writer
# ──────────────────────────────────────────────

class Writer:
    def __init__(self, base, fsync_every=25):
        self.base = Path(base)
        self.fsync_every = fsync_every
        self._fh = {}
        self._n = {}
        self._lock = threading.Lock()

    def write(self, name, line):
        if not line.endswith("\n"):
            line += "\n"
        with self._lock:
            fh = self._fh.get(name)
            if fh is None:
                p = self.base / name
                p.parent.mkdir(parents=True, exist_ok=True)
                fh = open(p, "a", encoding="utf-8", errors="ignore")
                self._fh[name] = fh
                self._n[name] = 0
            try:
                fh.write(line)
                fh.flush()
                self._n[name] += 1
                if self._n[name] % self.fsync_every == 0:
                    os.fsync(fh.fileno())
            except Exception:
                pass

    def close(self):
        with self._lock:
            for fh in self._fh.values():
                try:
                    fh.flush()
                    os.fsync(fh.fileno())
                    fh.close()
                except Exception:
                    pass
            self._fh.clear()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

# ──────────────────────────────────────────────
#  stats
# ──────────────────────────────────────────────

class Stats:
    def __init__(self, total=0, keys=None):
        self.total = total
        self.start = time.time()
        self._lock = threading.Lock()
        self.data = {"checked": 0, "valid": 0, "invalid": 0, "errors": 0}
        for k in (keys or []):
            self.data.setdefault(k, 0)

    def add(self, key, n=1):
        with self._lock:
            self.data[key] = self.data.get(key, 0) + n

    def get(self, key):
        with self._lock:
            return self.data.get(key, 0)

    def cpm(self):
        el = time.time() - self.start
        return int(self.data["checked"] / el * 60) if el > 1 else 0

    def line(self):
        d = self.data
        extra = ""
        for k, v in d.items():
            if k not in ("checked", "valid", "invalid", "errors"):
                extra += f"{Fore.CYAN}{k} {v}{Style.RESET_ALL} | "
        return (f"{Fore.GREEN}valid {d['valid']}{Style.RESET_ALL} | "
                f"{Fore.RED}bad {d['invalid']}{Style.RESET_ALL} | "
                f"{Fore.YELLOW}err {d['errors']}{Style.RESET_ALL} | "
                + extra +
                f"{d['checked']}/{self.total} | "
                f"{Fore.MAGENTA}{self.cpm()} cpm{Style.RESET_ALL}")

    def title(self, name):
        return (f"{name} | valid {self.data['valid']} | bad {self.data['invalid']} "
                f"| checked {self.data['checked']}/{self.total} | cpm {self.cpm()}")

# ──────────────────────────────────────────────
#  helpers
# ──────────────────────────────────────────────

def select_file(title="select txt file"):
    try:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askopenfilename(
            title=title,
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        root.destroy()
        return path or None
    except Exception:
        return None

def read_lines(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

def norm_combo(line):
    line = line.strip()
    if not line or '@' not in line:
        return None
    for sep in (':', '|', ';', ',', '\t', ' '):
        if sep in line:
            a, b = line.split(sep, 1)
            a, b = a.strip(), b.strip()
            if '@' in a and b:
                return f"{a}:{b}"
    return None

def dedupe(items):
    seen = set()
    out = []
    dupes = 0
    for i in items:
        if i in seen:
            dupes += 1
            continue
        seen.add(i)
        out.append(i)
    return out, dupes

def ask_threads(default=100, lo=1, hi=3000):
    try:
        v = input(f"{Fore.CYAN}[?] threads [{default}]: {Style.RESET_ALL}").strip()
        if not v:
            return default
        return max(lo, min(hi, int(v)))
    except Exception:
        return default

# ──────────────────────────────────────────────
#  pool runner
# ──────────────────────────────────────────────

def run_pool(items, fn, threads, stats=None, name="beast", refresh=0.5):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            if stats:
                set_title(stats.title(name))
                sys.stdout.write("\r" + stats.line() + " " * 8)
                sys.stdout.flush()
            stop.wait(refresh)

    t = threading.Thread(target=ticker, daemon=True)
    t.start()

    try:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            futs = [ex.submit(fn, it) for it in items]
            for _ in as_completed(futs):
                pass
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] interrupted{Style.RESET_ALL}")
    finally:
        stop.set()
        time.sleep(refresh * 1.2)
        if stats:
            sys.stdout.write("\r" + stats.line() + " " * 8 + "\n")
            sys.stdout.flush()
