import subprocess
import os
import shutil
import json
from pathlib import Path

def run_manager():
    # 1. Path Definitions
    root = Path("AuraAssistant")
    shell_src = root / "core/RogShell"
    dist_bin = root / "bin"
    
    print(f"--- Aura Industrial Manager: Deployment Phase ---")

    # 2. Build RogShell
    # We use -O3 for optimization (makes it faster/smaller for the assistant)
    os.makedirs(dist_bin, exist_ok=True)
    
    compile_cmd = [
        "g++",
        str(shell_src / "src/main.cpp"),
        str(shell_src / "src/process.c"),
        f"-I{shell_src / 'include'}",
        "-O3", 
        "-o", str(dist_bin / "RogShell.exe"),
        "-ladvapi32"
    ]

    print("[*] Compiling RogShell.exe...")
    result = subprocess.run(compile_cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("[+] Compilation successful.")
    else:
        print("[!] Compilation Error:")
        print(result.stderr)
        return

    # 3. JSON Configuration Sync
    # We copy the config to bin/ so the Assistant.exe can find it locally
    json_files = ["config.json", "data/assistant_data.json"]
    
    print("[*] Syncing JSON configurations...")
    for j_file in json_files:
        src_path = root / j_file
        dest_path = dist_bin / os.path.basename(j_file)
        
        if src_path.exists():
            shutil.copy2(src_path, dest_path)
            print(f"[+] Synced: {j_file} -> bin/")
        else:
            print(f"[!] Warning: {j_file} not found in root.")

    # 4. Final Package Check
    print(f"\n[COMPLETE] Deployment package ready in: {dist_bin.absolute()}")
    print(f"Items in bin: {os.listdir(dist_bin)}")

if __name__ == "__main__":
    run_manager()