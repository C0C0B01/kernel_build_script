#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil
import argparse
import datetime
import re
from pathlib import Path
from textwrap import dedent
from typing import Optional

# Root directory of this script
ROOT_DIR = Path(__file__).resolve().parent

# Target architecture and SoC
ARCH = "arm64"
TARGET_SOC = "s5e8845"
CROSS_COMPILE_PREFIX = "aarch64-linux-gnu-"

# Base directory for toolchain and other prebuilts
PREBUILTS_BASE_DIR = ROOT_DIR.parent / "prebuilts"

# Toolchain and assembler paths
TOOLCHAIN_PATH = PREBUILTS_BASE_DIR / "clang/host/linux-x86/llvm-20.1.8-x86_64/bin"
GAS_PATH = PREBUILTS_BASE_DIR / "gas"

# Path to store the build log
BUILD_LOG_FILE = ROOT_DIR / "kernel_build.log"

# Defconfig used for kernel build
KERNEL_DEFCONFIG = "essi_defconfig"

# Path to the kernel source tree
KERNEL_SOURCE_DIR = ROOT_DIR.parent / "exynos-kernel"

# Global Paths
OUT_DIR = None

# Global Environment Variables
os.environ["ARCH"] = ARCH
os.environ["CROSS_COMPILE"] = CROSS_COMPILE_PREFIX
os.environ["TARGET_SOC"] = TARGET_SOC

def log_message(message: str):
    """
    Logs a message to console and appends it to the build log file

    Args:
        message (str): Message to log
    """
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{timestamp} - {message}"
    print(line)

    try:
        BUILD_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BUILD_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except Exception as e:
        print(f"Logging failed: {e}")

def run_cmd(command: str,
            cwd: Optional[Path] = None,
            fatal_on_error: bool = True
            ) -> Optional[str]:
    """
    Runs a shell command with custom PATH

    Args:
        command: Shell command to run
        cwd: Working directory (optional)
        fatal_on_error: Exit on failure if True

    Returns:
        Command stdout, or None if failed and not fatal
    """
    log_message(
        f"Running: '{command}' in '{cwd.resolve()}'" 
        if cwd else f"Running: '{command}'"
    )

    env = os.environ.copy()

    extra_paths = filter(None, [
        TOOLCHAIN_PATH,
        GAS_PATH,
    ])

    env["PATH"] = ":".join(map(str, extra_paths)) + ":" + env["PATH"]

    try:
        result = subprocess.run(
            command, shell=True, check=True, cwd=cwd,
            capture_output=True, text=True, encoding="utf-8", env=env
        )
        log_message("Command succeeded")
        return result.stdout
    except subprocess.CalledProcessError as e:
        log_message(f"[ERROR] Command failed (exit {e.returncode}): '{command}'")
        if e.stdout:
            log_message(f"stdout:\n{e.stdout.strip()}")
        if e.stderr:
            log_message(f"stderr:\n{e.stderr.strip()}")
        if fatal_on_error:
            sys.exit(1)
        return None
    except Exception as e:
        log_message(f"[CRITICAL] Unexpected exception: {e}")
        sys.exit(1)

def validate_prebuilts():
    """
    Verifies that all required prebuilt paths and kernel source exist
    Exits if any are missing or invalid
    """
    log_message("Checking required prebuilts...")

    global OUT_DIR

    required = {
        "Toolchain": TOOLCHAIN_PATH,
        "GAS": GAS_PATH,
    }

    for name, path in required.items():
        if not path or not path.is_dir():
            log_message(f"[ERROR] Missing or invalid: {name} -> '{path}'")
            sys.exit(1)

    # Output directory for the kernel build artifacts
    OUT_DIR = KERNEL_SOURCE_DIR / "out"

    log_message("All prebuilts verified")

def clean_build_artifacts():
    """
    Cleans the kernel build environment:
    - Runs 'make clean' and 'make mrproper'
    - Removes the output directory (OUT_DIR)
    """
    log_message("Cleaning kernel build artifacts...")
    
    run_cmd("make clean", cwd=KERNEL_SOURCE_DIR, fatal_on_error=False)
    run_cmd("make mrproper", cwd=KERNEL_SOURCE_DIR, fatal_on_error=False)
    
    if OUT_DIR.exists():
        log_message(f"Removing main output directory: '{OUT_DIR}'")
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    
    log_message("Clean operation completed...")

def build_kernel(jobs: int):
    """
    Builds the Android kernel using the given defconfig

    Args:
        jobs (int): Number of parallel make jobs (-j)
    """
    log_message(f"Starting kernel build with {jobs} parallel jobs...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    make_args = (
        f"LLVM=1 LLVM_IAS=1 ARCH={ARCH} O={OUT_DIR} "
        f"CROSS_COMPILE={CROSS_COMPILE_PREFIX}"
    )

    log_message(f"Using defconfig: '{KERNEL_DEFCONFIG}'")
    run_cmd(
        f"make {make_args} {KERNEL_DEFCONFIG}",
        cwd=KERNEL_SOURCE_DIR,
        fatal_on_error=True
    )

    log_message("Compiling kernel Image...")
    run_cmd(
        f"make -j{jobs} {make_args}", 
        cwd=KERNEL_SOURCE_DIR,
        fatal_on_error=True
    )

    log_message("Kernel build completed")

def main():
    """
    Main entry point: parses arguments and runs the build process
    """
    parser = argparse.ArgumentParser(
        description="Android kernel build script",
        epilog=dedent("""
            Examples:
                ./build_kernel.py
                    Build using all CPU cores

                ./build_kernel.py --clean
                    Clean before building

                ./build_kernel.py -j8
                    Build using 8 jobs

                ./build_kernel.py --clean -j$(nproc)
                    Clean and build with all cores
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Optional clean flag
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean previous build artifacts before starting"
    )

    # Parallel jobs option
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=os.cpu_count(),
        help=f"Number of parallel build jobs (default: {os.cpu_count()})"
    )

    args = parser.parse_args()

    log_message("Starting Android kernel build process...")

    validate_prebuilts()

    if args.clean:
        clean_build_artifacts()

    build_kernel(args.jobs)

    log_message("Android kernel build completed successfully.")

if __name__ == "__main__":
    main()
