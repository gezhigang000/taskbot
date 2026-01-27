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

# 应用信息
APP_NAME = "Claude Code Remote"
APP_NAME_SHORT = "ClaudeCodeRemote"
APP_VERSION = "1.0.0"
APP_ID = "com.claudecode.remote"
MAIN_SCRIPT = "agent/gui.py"


def get_platform():
    """获取当前平台"""
    system = platform.system().lower()
    if system == "darwin":
        return "mac"
    elif system == "windows":
        return "win"
    elif system == "linux":
        return "linux"
    return system


def clean_build():
    """清理构建目录"""
    for path in ["build", "dist", f"{APP_NAME_SHORT}.spec"]:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    print("清理完成")


def build_mac(onefile=False):
    """构建 macOS 应用"""
    print("\n" + "="*60)
    print("构建 macOS 应用")
    print("="*60)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME_SHORT,
        "--windowed",  # 创建 .app bundle
        "--clean",
        "--noconfirm",
        # macOS 特定
        "--osx-bundle-identifier", APP_ID,
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # 隐藏导入 - 确保所有依赖都被打包
    cmd.extend([
        "--hidden-import", "websockets",
        "--hidden-import", "websockets.legacy",
        "--hidden-import", "websockets.legacy.client",
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.scrolledtext",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "json",
        "--hidden-import", "urllib.request",
        "--hidden-import", "urllib.parse",
        "--hidden-import", "threading",
        "--hidden-import", "dataclasses",
        "--hidden-import", "pathlib",
    ])

    # 收集所有 websockets 子模块
    cmd.extend([
        "--collect-submodules", "websockets",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n运行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        # 重命名为带空格的名称
        if os.path.exists(f"dist/{APP_NAME_SHORT}.app"):
            target = f"dist/{APP_NAME}.app"
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(f"dist/{APP_NAME_SHORT}.app", target)
            print(f"\n✓ 应用已生成: dist/{APP_NAME}.app")
            print(f"  可以直接拖拽到 Applications 文件夹使用")

            # 创建 DMG (如果有 create-dmg)
            if shutil.which("create-dmg"):
                print("\n正在创建 DMG 安装包...")
                dmg_cmd = [
                    "create-dmg",
                    "--volname", APP_NAME,
                    "--window-size", "500", "300",
                    "--icon-size", "100",
                    "--app-drop-link", "350", "150",
                    "--icon", f"{APP_NAME}.app", "150", "150",
                    f"dist/{APP_NAME}-{APP_VERSION}.dmg",
                    f"dist/{APP_NAME}.app"
                ]
                subprocess.run(dmg_cmd)
    else:
        print("\n✗ 构建失败")
        return False

    return True


def build_windows(onefile=True):
    """构建 Windows 应用"""
    print("\n" + "="*60)
    print("构建 Windows 应用")
    print("="*60)

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

    # 隐藏导入
    cmd.extend([
        "--hidden-import", "websockets",
        "--hidden-import", "websockets.legacy",
        "--hidden-import", "websockets.legacy.client",
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--collect-submodules", "websockets",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n运行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        if onefile:
            # 重命名
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
    """构建 Linux 应用"""
    print("\n" + "="*60)
    print("构建 Linux 应用")
    print("="*60)

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

    # 隐藏导入
    cmd.extend([
        "--hidden-import", "websockets",
        "--hidden-import", "websockets.legacy",
        "--hidden-import", "websockets.legacy.client",
        "--hidden-import", "asyncio",
        "--hidden-import", "tkinter",
        "--collect-submodules", "websockets",
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

    # 切换到项目根目录
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

    # 确定目标平台
    target = args.platform if args.platform != "auto" else get_platform()

    # 清理旧构建
    clean_build()

    # 构建
    success = False
    if target == "mac":
        success = build_mac(onefile=args.onefile)
    elif target == "win":
        success = build_windows(onefile=True)  # Windows 默认单文件
    elif target == "linux":
        success = build_linux(onefile=True)  # Linux 默认单文件
    else:
        print(f"不支持的平台: {target}")
        sys.exit(1)

    if success:
        print("\n" + "="*60)
        print("构建完成!")
        print("="*60)
        print(f"\n输出目录: {os.path.abspath('dist')}")
        print("\n用户无需安装任何依赖，直接运行即可使用。")


if __name__ == "__main__":
    main()
