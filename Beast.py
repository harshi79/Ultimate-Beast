# beast.py
import sys
import time
import subprocess
from pathlib import Path
from core.common import banner, clear, Fore, Style

ROOT = Path(__file__).resolve().parent

MODULES = [
    ("modules/hotmail_inboxer.py", "hotmail / outlook inboxer"),
    ("modules/imap_checker.py",    "imap keyword checker"),
    ("modules/smtp_checker.py",    "smtp cracker"),
    ("modules/owa_scanner.py",     "owa scanner (office365 / godaddy)"),
]

def launch(script):
    path = ROOT / script
    if not path.exists():
        print(f"{Fore.RED}[!] {script} not found{Style.RESET_ALL}")
        input("enter...")
        return
    try:
        subprocess.run([sys.executable, str(path)], cwd=str(ROOT))
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] interrupted{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}[!] {e}{Style.RESET_ALL}")
        input("enter...")

def menu():
    clear()
    banner("unified toolkit")
    for i, (script, label) in enumerate(MODULES, 1):
        mark = " " if (ROOT / script).exists() else "x"
        print(f"  [{i}] {label:<40} {Fore.LIGHTBLACK_EX}({mark} {script}){Style.RESET_ALL}")
    print(f"  [0] exit\n")

def main():
    while True:
        menu()
        try:
            c = input(f"{Fore.CYAN}select > {Style.RESET_ALL}").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if c in ("0", "q", ""):
            return
        if not c.isdigit():
            continue
        idx = int(c) - 1
        if 0 <= idx < len(MODULES):
            clear()
            launch(MODULES[idx][0])
            time.sleep(0.3)

if __name__ == "__main__":
    main()
