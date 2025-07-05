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
VARIANT = "user"
CROSS_COMPILE_PREFIX = "aarch64-linux-gnu-"

# Base directory for toolchain and other prebuilts
PREBUILTS_BASE_DIR = ROOT_DIR.parent / "prebuilts"

# Path to store the build log
BUILD_LOG_FILE = ROOT_DIR / "kernel_build.log"

# Defconfig used for kernel build
KERNEL_DEFCONFIG = "essi_defconfig"

# Path to the kernel source tree
KERNEL_SOURCE_DIR = ROOT_DIR.parent / "exynos-kernel"

# Global Paths
OUT_DIR = None
DIST_DIR = None
TOOLCHAIN_PATH = None
GAS_PATH = None
MKBOOT_PATH = None
RAMDISK_PATH = None

# Global Environment Variables
os.environ["ARCH"] = ARCH
os.environ["CROSS_COMPILE"] = CROSS_COMPILE_PREFIX
os.environ["TARGET_SOC"] = TARGET_SOC

# Config for downloading required prebuilts
PREBUILTS_CONFIG = {
    "Toolchain": {
        "target_dir_name": "clang/host/linux-x86/llvm-20.1.8-x86_64",
        "bin_path_suffix": "bin",
        "download_type": "download_url",
        "download_url": "https://www.kernel.org/pub/tools/llvm/files/llvm-20.1.8-x86_64.tar.gz",
        "extract_name_in_archive": "llvm-20.1.8-x86_64"
    },
    "GAS": {
        "target_dir_name": "gas/linux-x86",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://android.googlesource.com/platform/prebuilts/gas/linux-x86/",
        "branch": "main",
        "depth": 1
    },
    "Ramdisk_Repo": {
        "target_dir_name": "ramdisk_repo",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://gitlab.com/velpecula/samsung_s5e8845/a55x-kernel/kernel_samsung_prebuilt.git",
        "branch": "main",
        "depth": 1
    },
    "Mkbootimg_Tool": {
        "target_dir_name": "mkbootimg",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://android.googlesource.com/platform/system/tools/mkbootimg",
        "branch": "android14-qpr3-release",
        "depth": 1
    },
}

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
        MKBOOT_PATH,
        RAMDISK_PATH,
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

    global OUT_DIR, DIST_DIR

    required = {
        "Toolchain": TOOLCHAIN_PATH,
        "GAS": GAS_PATH,
        "Mkbootimg Tool": MKBOOT_PATH,
        "Ramdisk": RAMDISK_PATH,
    }

    for name, path in required.items():
        if not path or not path.is_dir():
            log_message(f"[ERROR] Missing or invalid: {name} -> '{path}'")
            sys.exit(1)

    # Output directory for the kernel build artifacts
    OUT_DIR = KERNEL_SOURCE_DIR / "out"
    DIST_DIR = KERNEL_SOURCE_DIR.parent / "out" / "dist"

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
    DIST_DIR.mkdir(parents=True, exist_ok=True)

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

def build_boot_image():
    """
    Builds boot.img from kernel image and prebuilt ramdisk
    """
    # Paths to input and output files
    kernel_image_path = OUT_DIR / "arch" / ARCH / "boot" / "Image"
    bootimg_output_path = DIST_DIR / "boot.img"

    prebuilt_ramdisk = (
        RAMDISK_PATH /
        "boot-artifacts" / "arm64" / "exynos" / VARIANT /"ramdisk.cpio.lz4"
    )

    # Check required files
    required_files = [
        (kernel_image_path, "Kernel image"),
        (prebuilt_ramdisk, "Ramdisk image"),
    ]

    for file_path, description in required_files:
        if not file_path.is_file():
            log_message(f"ERROR: {description} not found: {file_path}")
            sys.exit(1)

    run_cmd(
        f"{MKBOOT_PATH / 'mkbootimg.py'} --kernel {kernel_image_path} "
        f"--ramdisk {prebuilt_ramdisk} "
        f"--output {bootimg_output_path} "
        f"--pagesize 4096 "
        f"--header_version 4 ",
        fatal_on_error=True
    )

    if bootimg_output_path.exists():
        log_message(f"boot.img created at {bootimg_output_path}")
    else:
        sys.exit(1)

def unpack_tarball(archive_path: Path, dest_dir: Path):
    """
    Extracts a .tar.gz archive to the given directory
    If the archive contains a single top-level folder,
    its contents are moved instead
    """
    log_message(f"Extracting '{archive_path}' to '{dest_dir}'...")

    temp_dir = archive_path.parent / f"temp_extract_{os.getpid()}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    # Extract archive to temporary path
    run_cmd(f"tar -xzf {archive_path} -C {temp_dir}", fatal_on_error=True)

    contents = list(temp_dir.iterdir())
    dest_dir.mkdir(parents=True, exist_ok=True)

    if len(contents) == 1 and contents[0].is_dir():
        log_message(f"Flattening archive by moving contents of '{contents[0]}'...")
        for item in contents[0].iterdir():
            shutil.move(str(item), str(dest_dir / item.name))
    else:
        log_message(f"Moving extracted files to '{dest_dir}'...")
        for item in contents:
            shutil.move(str(item), str(dest_dir / item.name))

    shutil.rmtree(temp_dir, ignore_errors=True)
    log_message(f"Extraction complete: '{archive_path.name}'")

def get_prebuilt(name: str, config: dict, target_dir: Path):
    """
    Fetches a prebuilt from a URL or Git repo if not already present
    Updates Git repositories if needed
    """
    log_message(f"Checking prebuilt '{name}' at '{target_dir}'...")

    if target_dir.exists():
        log_message(f"Found '{name}' at '{target_dir}'.")
        if config["download_type"] == "git":
            log_message(f"Updating git repository for '{name}'...")
            git_dir = target_dir / ".git"
            if git_dir.is_dir():
                run_cmd("git pull", cwd=target_dir, fatal_on_error=False)
            else:
                log_message(f"'{target_dir}' is not a Git repo, Skipping pull")
        return

    log_message(f"'{name}' not found, Fetching...")

    # Ensure parent directory exists
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    download_type = config["download_type"]

    if download_type == "download_url":
        archive = ROOT_DIR / f"temp_{name.lower().replace(' ', '_')}.tar.gz"
        url = config["download_url"]
        log_message(f"Downloading '{name}' from: {url}")

        # Choose available downloader
        if shutil.which("wget"):
            cmd = f"wget -q -O {archive} '{url}'"
        elif shutil.which("curl"):
            cmd = f"curl -s -L -o {archive} '{url}'"
        else:
            log_message("ERROR: wget or curl not found")
            sys.exit(1)

        run_cmd(cmd, fatal_on_error=True)
        log_message("Download complete. Extracting...")
        unpack_tarball(archive, target_dir)
        os.remove(archive)
        log_message(f"Extraction complete: {target_dir}")

    elif download_type == "git":
        repo = config["repo_url"]
        branch = config["branch"]
        depth = config["depth"]
        log_message(f"Cloning git repo: {repo} (branch: {branch})")
        run_cmd(
            f"git clone --depth {depth} --single-branch "
            f"--branch {branch} {repo} {target_dir}",
            fatal_on_error=True
        )
        log_message(f"Cloned to: {target_dir}")

    else:
        log_message(f"ERROR: Unknown download_type '{download_type}'")
        sys.exit(1)

def setup_environment():
    """
    Prepares the build environment by ensuring all prebuilts are present
    Downloads missing prebuilts and sets global paths
    """
    log_message("Initializing environment...")

    global TOOLCHAIN_PATH, GAS_PATH, MKBOOT_PATH, RAMDISK_PATH

    for name, config in PREBUILTS_CONFIG.items():
        target = PREBUILTS_BASE_DIR / config["target_dir_name"]
        get_prebuilt(name, config, target)

    # Set paths to prebuilts
    TOOLCHAIN_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["Toolchain"]["target_dir_name"] /
        PREBUILTS_CONFIG["Toolchain"]["bin_path_suffix"]
    )
    GAS_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["GAS"]["target_dir_name"]
    )
    MKBOOT_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["Mkbootimg_Tool"]["target_dir_name"]
    )
    RAMDISK_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["Ramdisk_Repo"]["target_dir_name"]
    )

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

    try:
        setup_environment()
        validate_prebuilts()

        if args.clean:
            clean_build_artifacts()

        # Build kernel Image
        build_kernel(args.jobs)

        build_boot_image()

    except SystemExit:
        log_message("Build process terminated due to fatal error")
        sys.exit(1)

    except Exception as e:
        log_message(f"CRITICAL: Unhandled exception occurred: {e}")
        sys.exit(1)

    log_message("Android kernel build completed successfully.")

if __name__ == "__main__":
    main()
