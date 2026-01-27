#!/usr/bin/env python3
"""
Claude Code Remote - 跨平台打包脚本
生成独立安装包，用户无需安装 Python 或任何依赖

用法:
  python build.py           # 打包当前平台
  python build.py --onefile # 打包成单文件
"""

import os
import sys
import platform
import subprocess
import shutil

APP_NAME = "Claude Code Remote"
APP_NAME_SHORT = "ClaudeCodeRemote"
APP_VERSION = "2.0.0"
APP_ID = "com.claudecode.remote"
MAIN_SCRIPT = "agent/gui.py"


def get_platform():
    system = platform.system().lower()
    if system == "darwin":
        return "mac"
    elif system == "windows":
        return "win"
    elif system == "linux":
        return "linux"
    return system


def clean_build():
    for path in ["build", "dist", f"{APP_NAME_SHORT}.spec"]:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    print("清理完成")


def build_mac(onefile=False):
    print("\n" + "=" * 60)
    print("构建 macOS 应用")
    print("=" * 60)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME_SHORT,
        "--windowed",
        "--clean",
        "--noconfirm",
        "--osx-bundle-identifier", APP_ID,
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # 数据文件
    cmd.extend([
        "--add-data", "agent/terminal.html:agent",
    ])

    # agent 模块（GUI 直接导入）
    cmd.extend([
        "--hidden-import", "agent.server",
        "--hidden-import", "agent.frp",
    ])

    # 隐藏导入
    cmd.extend([
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.scrolledtext",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "json",
        "--hidden-import", "secrets",
        "--hidden-import", "urllib.request",
        "--hidden-import", "urllib.parse",
        "--hidden-import", "threading",
        "--hidden-import", "pathlib",
        "--hidden-import", "subprocess",
        "--hidden-import", "signal",
        "--hidden-import", "pty",
        "--hidden-import", "select",
        "--hidden-import", "fcntl",
        "--hidden-import", "termios",
        "--hidden-import", "struct",
        "--hidden-import", "shutil",
    ])

    # FastAPI + uvicorn
    cmd.extend([
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fastapi",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n运行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        if os.path.exists(f"dist/{APP_NAME_SHORT}.app"):
            target = f"dist/{APP_NAME}.app"
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(f"dist/{APP_NAME_SHORT}.app", target)
            print(f"\n✓ 应用已生成: dist/{APP_NAME}.app")
            print(f"  可以直接拖拽到 Applications 文件夹使用")
    else:
        print("\n✗ 构建失败")
        return False

    return True


def build_windows(onefile=True):
    print("\n" + "=" * 60)
    print("构建 Windows 应用")
    print("=" * 60)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME_SHORT,
        "--windowed",
        "--clean",
        "--noconfirm",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.extend([
        "--add-data", "agent/terminal.html;agent",
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fastapi",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n运行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        if onefile:
            src = f"dist/{APP_NAME_SHORT}.exe"
            dst = f"dist/{APP_NAME}.exe"
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
            print(f"\n✓ 应用已生成: dist/{APP_NAME}.exe")
        else:
            print(f"\n✓ 应用已生成: dist/{APP_NAME_SHORT}/")
    else:
        print("\n✗ 构建失败")
        return False

    return True


def build_linux(onefile=True):
    print("\n" + "=" * 60)
    print("构建 Linux 应用")
    print("=" * 60)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "claude-code-remote",
        "--clean",
        "--noconfirm",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.extend([
        "--add-data", "agent/terminal.html:agent",
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fastapi",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n运行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print("\n✓ 应用已生成: dist/claude-code-remote")
    else:
        print("\n✗ 构建失败")
        return False

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="构建跨平台独立安装包")
    parser.add_argument("--platform", "-p", choices=["mac", "win", "linux", "auto"],
                        default="auto", help="目标平台 (默认: auto)")
    parser.add_argument("--onefile", "-f", action="store_true",
                        help="打包成单个文件 (Windows/Linux 默认启用)")
    parser.add_argument("--clean", "-c", action="store_true",
                        help="仅清理构建目录")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║      Claude Code Remote - 独立安装包构建工具                 ║
╚══════════════════════════════════════════════════════════════╝

应用名称: {APP_NAME}
版本: {APP_VERSION}
当前平台: {get_platform()}
Python: {sys.version.split()[0]}
""")

    if args.clean:
        clean_build()
        return

    target = args.platform if args.platform != "auto" else get_platform()
    clean_build()

    success = False
    if target == "mac":
        success = build_mac(onefile=args.onefile)
    elif target == "win":
        success = build_windows(onefile=True)
    elif target == "linux":
        success = build_linux(onefile=True)
    else:
        print(f"不支持的平台: {target}")
        sys.exit(1)

    if success:
        print("\n" + "=" * 60)
        print("构建完成!")
        print("=" * 60)
        print(f"\n输出目录: {os.path.abspath('dist')}")
        print("\n用户无需安装任何依赖，直接运行即可使用。")


if __name__ == "__main__":
    main()
