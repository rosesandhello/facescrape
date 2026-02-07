#!/usr/bin/env python3
"""
Setup cron jobs for ScrapedFace - FB Arbitrage Scanner

Installs:
- Twice-daily opportunity recheck (9am and 9pm)
"""
import os
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"

CRON_MARKER = "# SCRAPEDFACE-SCANNER"

LOG_FILE = "/tmp/scrapedface-recheck.log"

# Jobs are built dynamically to use resolved paths
def get_cron_jobs():
    return [
        # Recheck opportunities twice daily (9am and 9pm)
        {
            "schedule": "0 9 * * *",
            "name": "recheck-morning",
            "command": f'cd {PROJECT_DIR} && {VENV_PYTHON} -c "import asyncio; from services.recheck import run_recheck; asyncio.run(run_recheck())"',
            "log": LOG_FILE
        },
        {
            "schedule": "0 21 * * *",
            "name": "recheck-evening",
            "command": f'cd {PROJECT_DIR} && {VENV_PYTHON} -c "import asyncio; from services.recheck import run_recheck; asyncio.run(run_recheck())"',
            "log": LOG_FILE
        },
    ]

CRON_JOBS = get_cron_jobs()


def get_current_crontab() -> str:
    """Get current user's crontab"""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except Exception:
        return ""


def set_crontab(content: str) -> bool:
    """Set user's crontab"""
    try:
        process = subprocess.Popen(
            ["crontab", "-"],
            stdin=subprocess.PIPE,
            text=True
        )
        process.communicate(input=content)
        return process.returncode == 0
    except Exception as e:
        print(f"❌ Failed to set crontab: {e}")
        return False


def remove_existing_jobs(crontab: str) -> str:
    """Remove existing FB Arbitrage cron jobs"""
    lines = crontab.split('\n')
    filtered = []
    skip_next = False
    
    for line in lines:
        if CRON_MARKER in line:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        filtered.append(line)
    
    return '\n'.join(filtered)


def install_cron_jobs() -> bool:
    """Install cron jobs for FB Arbitrage Scanner"""
    print("\n" + "=" * 50)
    print("🕐 CRON JOB SETUP")
    print("=" * 50)
    
    # Check if venv exists
    if not VENV_PYTHON.exists():
        print(f"❌ Virtual environment not found at {VENV_PYTHON}")
        print("   Run: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")
        return False
    
    # Get current crontab
    current = get_current_crontab()
    
    # Remove any existing FB Arbitrage jobs
    cleaned = remove_existing_jobs(current)
    
    # Build new cron entries
    new_entries = []
    for job in CRON_JOBS:
        comment = f"{CRON_MARKER} {job['name']}"
        entry = f"{job['schedule']} {job['command']} >> {job['log']} 2>&1"
        new_entries.append(comment)
        new_entries.append(entry)
    
    # Combine
    if cleaned.strip():
        new_crontab = cleaned.rstrip() + '\n\n' + '\n'.join(new_entries) + '\n'
    else:
        new_crontab = '\n'.join(new_entries) + '\n'
    
    # Show what we're installing
    print("\nWill install these cron jobs:\n")
    for job in CRON_JOBS:
        print(f"  📅 {job['schedule']} - {job['name']}")
        print(f"     Log: {job['log']}")
    
    # Confirm
    print()
    confirm = input("Install cron jobs? (y/n): ").strip().lower()
    if confirm != 'y':
        print("❌ Cancelled")
        return False
    
    # Install
    if set_crontab(new_crontab):
        print("\n✅ Cron jobs installed successfully!")
        print("\nSchedule:")
        print("  • 9:00 AM  - Morning recheck")
        print("  • 9:00 PM  - Evening recheck")
        print(f"\nLogs: /tmp/fb-arbitrage-recheck.log")
        return True
    else:
        print("\n❌ Failed to install cron jobs")
        return False


def uninstall_cron_jobs() -> bool:
    """Remove FB Arbitrage cron jobs"""
    print("\n🗑️  Removing FB Arbitrage cron jobs...")
    
    current = get_current_crontab()
    cleaned = remove_existing_jobs(current)
    
    if set_crontab(cleaned):
        print("✅ Cron jobs removed")
        return True
    return False


def show_cron_status():
    """Show current cron job status"""
    print("\n📋 Current FB Arbitrage cron jobs:\n")
    
    current = get_current_crontab()
    
    found = False
    lines = current.split('\n')
    for i, line in enumerate(lines):
        if CRON_MARKER in line:
            found = True
            job_name = line.replace(CRON_MARKER, '').strip()
            if i + 1 < len(lines):
                schedule = lines[i + 1].split()[0:5]
                print(f"  ✅ {job_name}: {' '.join(schedule)}")
    
    if not found:
        print("  ❌ No cron jobs installed")
        print("\n  Run: python setup_cron.py install")


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_cron.py [install|uninstall|status]")
        print()
        show_cron_status()
        return
    
    action = sys.argv[1].lower()
    
    if action == "install":
        install_cron_jobs()
    elif action == "uninstall":
        uninstall_cron_jobs()
    elif action == "status":
        show_cron_status()
    else:
        print(f"Unknown action: {action}")
        print("Usage: python setup_cron.py [install|uninstall|status]")


if __name__ == "__main__":
    main()
