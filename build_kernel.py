#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil
import argparse
import datetime
import re
import stat
import tempfile
import math
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

# Path to a kernel modules list file
VENDOR_RAMDISK_DLKM_EARLY_MODULES_FILE = ROOT_DIR / "modules.early.load"
VENDOR_RAMDISK_DLKM_MODULES_FILE = ROOT_DIR / "modules.load"
VENDOR_DLKM_MODULES_FILE = ROOT_DIR / "modules.load.vendor_dlkm"

# Global Paths
OUT_DIR = None
DIST_DIR = None
TOOLCHAIN_PATH = None
KERNELBUILD_TOOLS_PATH = None
GAS_PATH = None
MKBOOT_PATH = None
RAMDISK_PATH = None
MODULES_STAGING_DIR = None
KERNEL_SOURCE_DIR = None

# Config for downloading required prebuilts
PREBUILTS_CONFIG = {
    "Toolchain": {
        "target_dir_name": "clang/host/linux-x86/llvm-20.1.8-x86_64",
        "bin_path_suffix": "bin",
        "download_type": "download_url",
        "download_url": "https://www.kernel.org/pub/tools/llvm/files/llvm-20.1.8-x86_64.tar.gz",
        "extract_name_in_archive": "llvm-20.1.8-x86_64"
    },
    "Kernel_Build_Tools": {
        "target_dir_name": "kernel-build-tools",
        "bin_path_suffix": Path("linux-x86") / "bin",
        "download_type": "git",
        "repo_url": "https://android.googlesource.com/kernel/prebuilts/build-tools",
        "branch": "main-kernel-build-2023",
        "depth": 1
    },
    "GAS": {
        "target_dir_name": "gas/linux-x86",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://android.googlesource.com/platform/prebuilts/gas/linux-x86",
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
    "Ramdisk_Repo": {
        "target_dir_name": "ramdisk_repo",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://gitlab.com/velpecula/samsung_s5e8845/a55x-kernel/kernel_samsung_prebuilt.git",
        "branch": "main",
        "depth": 1
    },
    "Kernel_Source": {
        "target_dir_name": "exynos-kernel",
        "bin_path_suffix": "",
        "download_type": "git",
        "repo_url": "https://github.com/mst8981/android_kernel_samsung_a55x.git",
        "branch": "exynos-6.1-lts-dev",
        "depth": 1
    }
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
    Runs a shell command. The global PATH environment variable is expected
    to be set correctly by setup_environment()

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

    try:
        result = subprocess.run(
            command, shell=True, check=True, cwd=cwd,
            capture_output=True, text=True, encoding="utf-8"
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

    global OUT_DIR, DIST_DIR, MODULES_STAGING_DIR, KERNEL_SOURCE_DIR

    required = {
        "Toolchain": TOOLCHAIN_PATH,
        "Kernel Build Tools": KERNELBUILD_TOOLS_PATH,
        "GAS": GAS_PATH,
        "Mkbootimg Tool": MKBOOT_PATH,
        "Ramdisk": RAMDISK_PATH,
        "Kernel Source": KERNEL_SOURCE_DIR,
    }

    for name, path in required.items():
        if not path or not path.is_dir():
            log_message(f"[ERROR] Missing or invalid: {name} -> '{path}'")
            sys.exit(1)

    # Output directory for the kernel build artifacts
    OUT_DIR = KERNEL_SOURCE_DIR / "out"
    DIST_DIR = KERNEL_SOURCE_DIR.parent / "out" / "dist"
    MODULES_STAGING_DIR = OUT_DIR / "modules_install"

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

def build_kernel(jobs: int, install_modules: bool):
    """
    Builds the Android kernel using the given defconfig

    Args:
        jobs (int): Number of parallel make jobs (-j)
    """
    log_message(f"Starting kernel build with {jobs} parallel jobs...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    MODULES_STAGING_DIR.mkdir(parents=True, exist_ok=True)

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

    # Install modules to the staging directory
    if install_modules:
        log_message(f"Installing all modules to: {MODULES_STAGING_DIR}...")
        run_cmd(
            f"make -j{jobs} {make_args} "
            f"INSTALL_MOD_STRIP='--strip-debug --keep-section=.ARM.attributes' "
            f"INSTALL_MOD_PATH={MODULES_STAGING_DIR} modules_install",
            cwd=KERNEL_SOURCE_DIR
        )

    # Source and destination paths for the final kernel Image
    image_path = OUT_DIR / "arch" / ARCH / "boot" / "Image"
    dist_path = DIST_DIR / "Image"

    try:
        shutil.copyfile(image_path, dist_path)
    except Exception as e:
        log_message(f"ERROR: Failed to copy kernel Image to DIST_DIR: {e}")
        sys.exit(1)

    log_message("Kernel build completed")

def build_dtbo_images():
    """
    Generate dtbo.img and dtb.img from compiled *.dtbo and *.dtb files

    - Uses mkdtimg for dtbo.img with custom flags
    - Concatenates *.dtb files into dtb.img
    """
    arch_dts = OUT_DIR / "arch" / ARCH / "boot" / "dts"
    dtbo_dir = arch_dts / "samsung" / "a55x"
    dtb_dir = arch_dts / "exynos"

    dtbo_files = sorted(dtbo_dir.glob("*.dtbo"))
    dtb_files = sorted(dtb_dir.glob("*.dtb"))

    if not dtbo_files:
        log_message(f"ERROR: No *.dtbo files found in {dtbo_dir}")
        sys.exit(1)
    if not dtb_files:
        log_message(f"ERROR: No *.dtb files found in {dtb_dir}")
        sys.exit(1)

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    dtbo_img_path = DIST_DIR / "dtbo.img"
    dtb_img_path = DIST_DIR / "dtb.img"

    # Build dtbo.img
    custom_flags = (
        "--custom0=/:dtbo-hw_rev "
        "--custom1=/:dtbo-hw_rev_end "
        "--custom2=/:edtbo-rev"
    )

    run_cmd(
        f"{KERNELBUILD_TOOLS_PATH / 'mkdtimg'} create {dtbo_img_path} {custom_flags} "
        + " ".join(str(f) for f in dtbo_files),
        fatal_on_error=True
    )

    # Build dtb.img
    with open(dtb_img_path, "wb") as out_f:
        for dtb in dtb_files:
            out_f.write(dtb.read_bytes())

    log_message("Successfully built dtbo.img and dtb.img")

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

def read_modules_file(file_path: Path) -> list[str]:
    """
    Reads a list of modules from a text file, one module per line
    Ignores empty lines and lines starting with '#'
    """
    if not file_path.is_file():
        log_message(f"WARNING: Module list file not found: {file_path}")
        sys.exit(1)

    modules = []
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                modules.append(line)
    return modules

def mk_vendor_rd_dlkm(mount_prefix: str,
                    module_early_list_file: Path,
                    module_list_file: Path):
    """
    Creates vendor_ramdisk_dlkm.cpio.lz4 from a module list and mount prefix

    Args:
        mount_prefix (str): Ramdisk mount point (e.g., "vendor_ramdisk_dlkm")
        module_early_list_file (Path): Early-loaded kernel module list file
        module_list_file (Path): Kernel module list file
    """
    # Ensure module list files exist
    missing_files = [
        module_early_list_file,
        module_list_file
    ]

    if not all(file.is_file() for file in missing_files):
        log_message(f"ERROR: One or more module list files are missing: {missing_files}")
        sys.exit(1)

    # Read early and normal module lists
    early_modules = read_modules_file(module_early_list_file)
    normal_modules = read_modules_file(module_list_file)

    dist_dir = Path(DIST_DIR)
    base_modules_dir = Path(MODULES_STAGING_DIR) / "lib" / "modules"
    tools_path = Path(KERNELBUILD_TOOLS_PATH)

    final_output_path = dist_dir / "vendor_ramdisk_dlkm.cpio.lz4"
    final_output_path.parent.mkdir(parents=True, exist_ok=True)

    kernel_dirs = list(base_modules_dir.glob("*-*"))
    if not kernel_dirs:
        log_message("ERROR: No kernel version found")
        sys.exit(1)
    kernel_version = kernel_dirs[0].name
    log_message(f"Kernel version: {kernel_version}")

    staging_dir = Path(tempfile.mkdtemp(prefix="vendor_ramdisk_dlkm_staging_"))
    flat_dir = staging_dir / "lib" / "modules" / kernel_version
    flat_dir.mkdir(parents=True, exist_ok=True)

    output_cpio_path = staging_dir.parent / "vendor_ramdisk_dlkm.cpio"

    vendor_modules = early_modules + normal_modules
    modules_copied = 0
    if not vendor_modules:
        log_message("ERROR: Module list is empty")
        sys.exit(1)

    for name in vendor_modules:
        found = list(base_modules_dir.rglob(name))
        if found:
            shutil.copy(found[0], flat_dir / name)
            modules_copied += 1
        else:
            log_message(f"ERROR: Module not found: {name}")
            sys.exit(1)

    if modules_copied == 0:
        log_message("No modules copied, check module list or install path")
        sys.exit(1)

    # Ensure required tools exist
    mkbootfs = lz4 = depmod = None
    for name in ["mkbootfs", "lz4", "depmod"]:
        tool = tools_path / name
        if not tool.is_file():
            log_message(f"ERROR: {name} not found: {tool}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            output_cpio_path.unlink(missing_ok=True)
            sys.exit(1)
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if name == "mkbootfs":
            mkbootfs = tool
        elif name == "depmod":
            depmod = tool
        elif name == "lz4":
            lz4 = tool

    if not all([mkbootfs, lz4, depmod]):
        log_message("ERROR: One or more required tools are missing after path assignment")
        sys.exit(1)

    run_cmd(f"{depmod} -b {staging_dir} {kernel_version}", fatal_on_error=True)

    # Flatten: move contents from lib/modules/<version>/ to lib/modules/
    flat_mod_root = staging_dir / "lib" / "modules"
    for item in flat_dir.iterdir():
        shutil.move(str(item), flat_mod_root / item.name)
    shutil.rmtree(flat_dir)

    dep_file = flat_mod_root / "modules.dep"
    if dep_file.exists():
        new_lines = []
        with open(dep_file, "r") as f:
            for line in f:
                parts = line.strip().split(":", 1)
                main = f"{mount_prefix}/lib/modules/{parts[0].strip()}"
                deps = ""
                if len(parts) == 2:
                    deps = " ".join(f"{mount_prefix}/lib/modules/{d.strip()}"
                            for d in parts[1].split())
                new_lines.append(f"{main}: {deps}".rstrip())
        with open(dep_file, "w") as f:
            f.write("\n".join(new_lines))
    else:
        log_message("ERROR: modules.dep not found")
        sys.exit(1)

    # Copy modules.builtin files
    for name in ["modules.builtin",  "modules.builtin.modinfo",
                "modules.builtin.alias.bin", "modules.builtin.bin"]:
        src = base_modules_dir / kernel_version / name
        dst = flat_mod_root / name
        if src.exists():
            shutil.copy(src, dst)
        else:
            log_message(f"WARNING: {name} not found in {src.parent}")

    # Create modules.load and modules.order files
    for filename in ["modules.load", "modules.order"]:
        output_file_path = flat_mod_root / filename
        with open(output_file_path, "w") as f:
            for name in vendor_modules:
                if name.endswith(".ko"):
                    f.write(name + "\n")

    try:
        with open(output_cpio_path, 'wb') as out:
            subprocess.run([str(mkbootfs), str(staging_dir)], stdout=out, check=True)

        run_cmd(
            f"{lz4} -9 -f -l {output_cpio_path} {final_output_path}",
            fatal_on_error=True
        )

    except Exception as e:
        log_message(f"ERROR during CPIO creation or compression: {e}")
        sys.exit(1)

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        output_cpio_path.unlink(missing_ok=True)

def get_system_dlkm_list() -> list[str]:
    """
    Extracts .ko filenames from modules.bzl under
    _COMMON_GKI_MODULES_LIST and _ARM64_GKI_MODULES_LIST

    Returns:
        list[str]: Sorted unique list of .ko filenames
    """
    bzl_path = KERNEL_SOURCE_DIR / "modules.bzl"
    markers = ["_COMMON_GKI_MODULES_LIST", "_ARM64_GKI_MODULES_LIST"]
    system_dlkm_mod_list = set()

    try:
        with open(bzl_path, "r") as f:
            lines = f.readlines()

        capture = False
        for line in lines:
            if any(marker in line for marker in markers):
                capture = True
                continue
            if capture:
                match = re.search(r'^\s*"([^"]+\.ko)"', line)
                if match:
                    system_dlkm_mod_list.add(Path(match.group(1)).name)
                elif "]" in line:
                    capture = False
    except FileNotFoundError:
        log_message(f"ERROR: modules.bzl not found: {bzl_path}")
        sys.exit(1)

    return sorted(system_dlkm_mod_list)

def build_dlkm_image(image_name: str,
                    modules_list_file: Optional[Path],
                    mount_prefix: str,
                    sign_modules: bool = False):
    """
    Build a DLKM image in EROFS format using mkfs.erofs

    Args:
        image_name (str): Output image name (e.g., "system_dlkm")
        modules_list_file (Path): List of kernel module filenames to include
        mount_prefix (str): Mount point inside the image (e.g., "/system_dlkm")
    """
    if image_name == "system_dlkm":
        log_message("Reading system_dlkm modules from modules.bzl...")
        modules = get_system_dlkm_list()
    else:
        modules = read_modules_file(modules_list_file)

    if not modules:
        log_message(f"ERROR: No modules found for {image_name}")
        sys.exit(1)

    dist_dir = Path(DIST_DIR)
    base_modules_dir = Path(MODULES_STAGING_DIR) / "lib" / "modules"
    tools_path = Path(KERNELBUILD_TOOLS_PATH)

    # keys
    sign_tool = OUT_DIR / "scripts" / "sign-file"
    key_pem = OUT_DIR / "certs" / "signing_key.pem"
    key_x509 = OUT_DIR / "certs" / "signing_key.x509"

    final_img = dist_dir / f"{image_name}.img"
    final_img.parent.mkdir(parents=True, exist_ok=True)

    kernel_dirs = list(base_modules_dir.glob("*-*"))
    if not kernel_dirs:
        log_message("ERROR: No kernel version found")
        sys.exit(1)
    kernel_version = kernel_dirs[0].name
    log_message(f"Kernel version: {kernel_version}")

    staging_dir = Path(tempfile.mkdtemp(prefix=f"{image_name}_staging_"))
    try:
        flat_dir = staging_dir / "lib" / "modules" / kernel_version
        flat_dir.mkdir(parents=True, exist_ok=True)

        modules_copied = 0
        if not modules:
            log_message("ERROR: Module list is empty")
            sys.exit(1)

        for name in modules:
            found = list(base_modules_dir.rglob(name))
            if found:
                dst = flat_dir / name
                shutil.copy(found[0], dst)

                # Sign modules
                if sign_modules:
                    required_signing_files = [
                        sign_tool,
                        key_pem,
                        key_x509,
                    ]
                    if not all(x.is_file() for x in required_signing_files):
                        log_message("ERROR: Missing signing tool or keys")
                        sys.exit(1)

                    result = subprocess.run([
                        str(sign_tool), "sha1",
                        str(key_pem),
                        str(key_x509),
                        str(dst)
                    ])
                    if result.returncode != 0:
                        log_message(f"ERROR: Failed to sign {name}")
                        sys.exit(1)

                modules_copied += 1
            else:
                log_message(f"WARNING: Module not found: {name}")

        if modules_copied == 0:
            log_message("No modules copied, check module list or install path")
            sys.exit(1)

        # Ensure required tools exist
        mkfs = depmod = None
        for name in ["mkfs.erofs", "depmod"]:
            tool = tools_path / name
            if not tool.is_file():
                log_message(f"ERROR: {name} not found: {tool}")
                sys.exit(1)
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if name == "mkfs.erofs":
                mkfs = tool
            elif name == "depmod":
                depmod = tool

        if not all([mkfs, depmod]):
            log_message("ERROR: One or more required tools are missing after path assignment")
            sys.exit(1)

        run_cmd(f"{depmod} -b {staging_dir} {kernel_version}", fatal_on_error=True)

        # Flatten: move contents from lib/modules/<version>/ to lib/modules/
        flat_mod_root = staging_dir / "lib" / "modules"
        for item in flat_dir.iterdir():
            shutil.move(str(item), flat_mod_root / item.name)
        shutil.rmtree(flat_dir)

        dep_file = flat_mod_root / "modules.dep"
        if dep_file.exists():
            new_lines = []
            with open(dep_file, "r") as f:
                for line in f:
                    parts = line.strip().split(":", 1)
                    main = f"{mount_prefix}/lib/modules/{parts[0].strip()}"
                    deps = ""
                    if len(parts) == 2:
                        deps = " ".join(f"{mount_prefix}/lib/modules/{d.strip()}"
                                    for d in parts[1].split())
                    new_lines.append(f"{main}: {deps}".rstrip())
            with open(dep_file, "w") as f:
                f.write("\n".join(new_lines))
        else:
            log_message("ERROR: modules.dep not found")
            sys.exit(1)

        # Copy modules.builtin files
        for name in ["modules.builtin", "modules.builtin.modinfo",
                     "modules.builtin.alias.bin", "modules.builtin.bin"]:
            src = base_modules_dir / kernel_version / name
            dst = flat_mod_root / name
            if src.exists():
                shutil.copy(src, dst)
            else:
                log_message(f"WARNING: {name} not found in {src.parent}")

        # Create modules.load and modules.order
        for filename in ["modules.load", "modules.order"]:
            output_file_path = flat_mod_root / filename
            with open(output_file_path, "w") as f:
                for name in modules:
                    if name.endswith(".ko"):
                        f.write(name + "\n")

        # Use the appropriate file_contexts based on image_name
        fc_dir = ROOT_DIR / "sepolicy"
        if image_name == "system_dlkm":
            fc_file = fc_dir / "system_dlkm_file_contexts"
        elif image_name == "vendor_dlkm":
            fc_file = fc_dir / "vendor_dlkm_file_contexts"

        # Create the EROFS image
        run_cmd(
            f"{mkfs} "
            f"-z lz4hc,9 "
            f"-T 0 "
            f"--mount-point {mount_prefix.strip('/')} "
            f"--file-contexts {str(fc_file)} "
            f"{str(final_img)} "
            f"{str(staging_dir)}",
            fatal_on_error=True
        )

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

def build_vendorboot_image():
    """
    Assemble vendor_boot.img using mkbootimg (header version 4).

    Combines multiple vendor ramdisk fragments (platform, dlkm, recovery),
    a DTB image, and embeds a basic vendor bootconfig file

    Requires:
        - DTB image generated with --create-dtbo-images
        - Vendor ramdisk fragments generated with --build-vendor-ramdisk-dlkm
    """
    image_name = "vendor_boot"
    final_img = DIST_DIR / f"{image_name}.img"
    parts_dir = ROOT_DIR / "vb_fragments"

    vendor_ramdisk_dlkm = DIST_DIR / "vendor_ramdisk_dlkm.cpio.lz4"
    dtb_path = DIST_DIR / "dtb.img"
    vendor_ramdisk_platform = parts_dir / "vendor_ramdisk_platform.lz4"
    vendor_ramdisk_recovery = parts_dir / "vendor_ramdisk_recovery.lz4"

    # Create staging directory
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{image_name}_staging_"))
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Create bootconfig
        bootconfig_file = staging_dir / "vendor_bootconfig.txt"
        bootconfig_file.write_text(
            "buildtime_bootconfig=enable\n"
            "androidboot.serialconsole=0\n"
        )

        # Check required files
        for x in [
            vendor_ramdisk_platform,
            vendor_ramdisk_recovery,
            vendor_ramdisk_dlkm,
            dtb_path
        ]:
            if not x.exists():
                log_message(f"Missing required file: {x}")
                sys.exit(1)

        # Run mkbootimg
        run_cmd(
            f"{MKBOOT_PATH / 'mkbootimg.py'} "
            f"--vendor_bootconfig {bootconfig_file} "
            f"--vendor_cmdline \"bootconfig loop.max_part=7\" "
            f"--header_version 4 "
            f"--dtb {dtb_path} "
            f"--pagesize 2048 "
            f"--ramdisk_name platform "
            f"--ramdisk_type platform "
            f"--vendor_ramdisk_fragment {vendor_ramdisk_platform} "
            f"--ramdisk_name dlkm "
            f"--ramdisk_type dlkm "
            f"--vendor_ramdisk_fragment {vendor_ramdisk_dlkm} "
            f"--ramdisk_name recovery "
            f"--ramdisk_type recovery "
            f"--vendor_ramdisk_fragment {vendor_ramdisk_recovery} "
            f"--vendor_boot {final_img} ",
            fatal_on_error=True
        )

        if not final_img.exists():
            log_message(f"Failed to generate {final_img}")
            sys.exit(1)

    finally:
        if staging_dir.exists():
            log_message(f"Cleaning up temporary directory: {staging_dir}")
            shutil.rmtree(staging_dir, ignore_errors=True)

def sign_partition_image(image_path: Path, partition_name: str):
    """
    Signs a partition image using AVBTool
    Uses add_hash_footer for boot.img, and add_hashtree_footer for mountable images
    """
    avbtool = KERNELBUILD_TOOLS_PATH / 'avbtool'
    key_path = MKBOOT_PATH / "gki/testdata/testkey_rsa4096.pem"

    missing = []
    if not avbtool:
        missing.append("avbtool")
    if not image_path.exists():
        missing.append("image")
    if not key_path.exists():
        missing.append("key")

    if missing:
        log_message(f"ERROR: Required component(s) missing: {', '.join(missing)}")
        sys.exit(1)

    log_message(f"Signing {partition_name}.img with AVBTool...")
    if partition_name in {"boot", "vendor_boot", "dtbo"}:
        # Partitions
        partition_sizes = {
            "boot": 67_108_864,
            "vendor_boot": 67_108_864,
            "dtbo": 8_388_608,
            "init_boot": 16_777_216,
        }
        padded_size = partition_sizes.get(partition_name)
        if not padded_size:
            raw_size = image_path.stat().st_size + (128 * 1024)
            padded_size = math.ceil(raw_size / 4096) * 4096
        if partition_name in {"boot", "vendor_boot"}:
            run_cmd(
                f"{avbtool} add_hash_footer "
                f"--image {image_path} "
                f"--partition_name {partition_name} "
                f"--partition_size {padded_size} "
                f"--key {key_path} "
                f"--algorithm SHA256_RSA4096",
                fatal_on_error=True
            )
        else:
            run_cmd(
                f"{avbtool} add_hashtree_footer "
                f"--image {image_path} "
                f"--partition_name {partition_name} "
                f"--partition_size {padded_size} "
                f"--do_not_generate_fec "
                f"--hash_algorithm sha256 "
                f"--key {key_path} "
                f"--algorithm SHA256_RSA4096",
                fatal_on_error=True
            )
    else:
        run_cmd(
            f"{avbtool} add_hashtree_footer "
            f"--image {image_path} "
            f"--partition_name {partition_name} "
            f"--do_not_generate_fec "
            f"--hash_algorithm sha256 "
            f"--key {key_path} "
            f"--algorithm SHA256_RSA4096",
            fatal_on_error=True
        )

    log_message(f"{partition_name}.img signed successfully")

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
        log_message(f"Cloning git repo: {repo} (branch: {branch})")
        depth = config.get("depth")
        depth_arg = f"--depth {depth}" if depth else ""
        run_cmd(
            f"git clone {depth_arg} --branch {branch} {repo} {target_dir}",
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

    global TOOLCHAIN_PATH, GAS_PATH, KERNELBUILD_TOOLS_PATH
    global MKBOOT_PATH, RAMDISK_PATH, KERNEL_SOURCE_DIR

    # Global Environment Variables
    os.environ["ARCH"] = ARCH
    os.environ["CROSS_COMPILE"] = CROSS_COMPILE_PREFIX
    os.environ["TARGET_SOC"] = TARGET_SOC
    log_message(f"Set environment variables: ARCH={os.environ['ARCH']}, "
        f"CROSS_COMPILE={os.environ['CROSS_COMPILE']}, "
        f"TARGET_SOC={os.environ['TARGET_SOC']}")

    for name, config in PREBUILTS_CONFIG.items():
        if name == "Kernel_Source":
            target = ROOT_DIR.parent / config["target_dir_name"]
        else:
            target = PREBUILTS_BASE_DIR / config["target_dir_name"]

        get_prebuilt(name, config, target)
        if name == "Kernel_Source":
            expected_kernel_path = ROOT_DIR.parent / "exynos-kernel"
            if target.resolve() != expected_kernel_path.resolve():
                log_message(f"WARNING: Kernel_Source path mismatch: "
                    f"'{target.resolve()}' != '{expected_kernel_path.resolve()}'")
            KERNEL_SOURCE_DIR = target

    # Set paths to prebuilts
    TOOLCHAIN_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["Toolchain"]["target_dir_name"] /
        PREBUILTS_CONFIG["Toolchain"]["bin_path_suffix"]
    )
    KERNELBUILD_TOOLS_PATH = (
        PREBUILTS_BASE_DIR /
        PREBUILTS_CONFIG["Kernel_Build_Tools"]["target_dir_name"] /
        PREBUILTS_CONFIG["Kernel_Build_Tools"]["bin_path_suffix"]
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
    KERNEL_SOURCE_DIR = (
        ROOT_DIR.parent /
        PREBUILTS_CONFIG["Kernel_Source"]["target_dir_name"]
    )

    log_message("Updating global PATH environment variable...")
    extra_paths = filter(None, [
        TOOLCHAIN_PATH,
        KERNELBUILD_TOOLS_PATH,
        GAS_PATH,
        MKBOOT_PATH,
        RAMDISK_PATH,
        KERNEL_SOURCE_DIR,
    ])

    # Add unique paths to the beginning of the PATH
    current_path_dirs = os.environ["PATH"].split(os.pathsep)
    new_path_dirs = []
    for x in extra_paths:
        x_str = str(x)
        if x_str not in current_path_dirs:
            new_path_dirs.append(x_str)

    os.environ["PATH"] = os.pathsep.join(new_path_dirs + current_path_dirs)
    log_message(f"New PATH: {os.environ['PATH']}")

    log_message("Environment setup complete")

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

    parser.add_argument(
        "--create-dtbo-images",
        action="store_true",
        help="Create dtbo.img and dtb.img from compiled DTBO/DTB files"
    )

    parser.add_argument(
        "--create-boot-image",
        action="store_true",
        help="Create boot.img using compiled kernel and prebuilt ramdisk"
    )

    parser.add_argument(
        "--build-vendor-ramdisk-dlkm",
        action="store_true",
        help="Build vendor_ramdisk_dlkm.cpio.lz4"
    )

    parser.add_argument(
        "--build-vendor-boot-image",
        action="store_true",
        help="Build vendor_boot.img"
    )

    parser.add_argument(
        "--build-dlkm-image",
        action="store_true",
        help="Build system_dlkm.img and vendor_dlkm.img using mkfs.erofs"
    )

    parser.add_argument(
        "--sign-images",
        action="store_true",
        help="Enable AVB signing for all detected images"
    )

    args = parser.parse_args()

    log_message("Starting Android kernel build process...")

    try:
        setup_environment()
        validate_prebuilts()

        if args.clean:
            clean_build_artifacts()

        # Determine whether to install kernel modules
        install_modules = (
            args.build_vendor_ramdisk_dlkm or
            args.build_vendor_boot_image or
            args.build_dlkm_image
        )

        # Build kernel Image
        build_kernel(args.jobs, install_modules=install_modules)

        # If user explicitly asked for dtbo images only
        if args.create_dtbo_images or args.build_vendor_boot_image:
            build_dtbo_images()

        if args.create_boot_image:
            build_boot_image()

        # If user explicitly asked for vendor_ramdisk_dlkm only (not via vendor_boot)
        if args.build_vendor_ramdisk_dlkm and not args.build_vendor_boot_image:
            mk_vendor_rd_dlkm(
                mount_prefix="",
                module_early_list_file=VENDOR_RAMDISK_DLKM_EARLY_MODULES_FILE,
                module_list_file=VENDOR_RAMDISK_DLKM_MODULES_FILE
            )

        # Build vendor_boot.img
        if args.build_vendor_boot_image:
            if not args.create_dtbo_images:
                log_message("Auto-enabling --create-dtbo-images (required for vendor_boot.img)")
                args.create_dtbo_images = True
            if not args.build_vendor_ramdisk_dlkm:
                log_message("Auto-enabling --build-vendor-ramdisk-dlkm (required for vendor_boot.img)")
                args.build_vendor_ramdisk_dlkm = True

            # Build dtbo image
            build_dtbo_images()

            # Build vendor_ramdisk_dlkm
            mk_vendor_rd_dlkm(
                mount_prefix="",
                module_early_list_file=VENDOR_RAMDISK_DLKM_EARLY_MODULES_FILE,
                module_list_file=VENDOR_RAMDISK_DLKM_MODULES_FILE
            )
            build_vendorboot_image()

        # Build system_dlkm and vendor_dlkm images if needed
        if args.build_dlkm_image:
            build_dlkm_image(
                image_name="system_dlkm",
                modules_list_file=None,
                mount_prefix="/system_dlkm",
                sign_modules=True,
            )
            build_dlkm_image(
                image_name="vendor_dlkm",
                modules_list_file=VENDOR_DLKM_MODULES_FILE,
                mount_prefix="/vendor_dlkm",
                sign_modules=False,
            )

        # Sign images if requested
        if args.sign_images:
            images = [
                ("dtbo", DIST_DIR / "dtbo.img", args.create_dtbo_images),
                ("boot", DIST_DIR / "boot.img", args.create_boot_image),
                ("system_dlkm", DIST_DIR / "system_dlkm.img", args.build_dlkm_image),
                ("vendor_dlkm", DIST_DIR / "vendor_dlkm.img", args.build_dlkm_image),
                ("vendor_boot", DIST_DIR / "vendor_boot.img", args.build_vendor_boot_image),
            ]

            signed_any = False
            for name, path, requested in images:
                if path.exists():
                    if requested:
                        sign_partition_image(path, name)
                        signed_any = True
                    else:
                        log_message(f"SKIP: {name}.img exists but not requested")
                elif requested:
                    log_message(f"MISS: {name}.img requested but not built")

            if not signed_any:
                log_message("ERROR: --sign-images given but no image found to sign")
                sys.exit(1)

    except SystemExit:
        log_message("Build process terminated due to fatal error")
        sys.exit(1)

    except Exception as e:
        log_message(f"CRITICAL: Unhandled exception occurred: {e}")
        sys.exit(1)

    log_message("Android kernel build completed successfully.")

if __name__ == "__main__":
    main()
