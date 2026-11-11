# -*- coding: utf-8 -*-
"""build_win —— Windows 打包流程的实际执行体（由 packaging/build_windows.bat 调用）

为什么把流程放到 Python 而不是 .bat 里：
  cmd.exe 解释 .bat 时只认「当前控制台代码页」，且完全不支持 BOM。
  UTF-8 编码的 .bat 里出现非 ASCII 行（哪怕只是 echo 文案），
  叠加 `chcp 65001` 会让 cmd 的字节偏移与字符偏移错位，
  于是整行被从中间截断当成命令执行 → "'xx' is not recognized as an internal or external command"，
  最终表现为 xcopy 之类的步骤莫名失败。
  因此约定：
    - build_windows.bat 保持纯 ASCII（只做 venv 引导 + 调本脚本）
    - 所有中文文案与中文路径（"AI错题本"）都在本脚本里用 Python 处理
      （Python 走宽字符 API，任何代码页下都不会乱码/错位）

用法：
    .venv\\Scripts\\python packaging\\build_win.py [run]
  run = 构建完成后启动 dist\\AI错题本\\AI错题本.exe 冒烟
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 工程根
APP_NAME = "AI错题本"                                   # 发布目录名 / 可执行名（与 mac 产物同名）
EXE_NAME = "AI错题本.exe"
DIST = ROOT / "dist"
APP_DIR = DIST / APP_NAME

# 控制台代码页可能是 GBK/UTF-8/其它：一律用 replace 兜底，避免一个符号直接崩掉整条打包链
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:      # 老版本 / 非标准流，忽略
        pass

# 包内禁入名单（本地数据 / 密钥 / 已移除功能残留），与 mac 构建脚本一致
LEAK_EXACT = ("ai_config.json", "key.env", "wrongbook.json",
              "drafts.json", "users.json", "topics.json")
LEAK_GLOBS = ("license*.pem", "*geogebra*")


def log(msg=""):
    print(msg, flush=True)


def step(n, msg):
    log(f"[{n}/6] {msg}")


def fail(msg, code=1):
    log(f"[X] {msg}")
    sys.exit(code)


def run(cmd, **kw):
    """跑子进程并把输出直接透传到当前控制台"""
    log("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=str(ROOT), **kw)


# ---------- 2/6 门禁：全量回归必须全绿 ----------

def step_tests():
    step(2, "门禁：全量回归必须全绿")
    r = run([sys.executable, "-m", "pytest", "tests", "-q"])
    if r.returncode != 0:
        fail("回归未全绿，停止打包")


# ---------- 3/6 PyInstaller 构建 ----------

def app_version():
    sys.path.insert(0, str(ROOT))
    import paths  # noqa: E402  （paths.py 只读常量，安全）
    return paths.APP_VERSION


def step_pyinstaller():
    step(3, "PyInstaller 构建（清理旧产物）")
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR, ignore_errors=True)
    r = run([sys.executable, "-m", "PyInstaller",
             "packaging/wrongbook.spec", "--noconfirm", "--clean"])
    if r.returncode != 0:
        fail("PyInstaller 构建失败")
    exe = APP_DIR / EXE_NAME
    if not exe.exists():
        fail(f"构建产物缺失：{exe}")
    return exe


# ---------- 4/6 复制静态资源（程序目录布局：static/ engine/ data\\kb 种子） ----------

def step_copy_assets():
    step(4, "复制静态资源（程序目录布局：static/ engine/ data\\kb 种子）")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    pairs = [(ROOT / "static", APP_DIR / "static"),
             (ROOT / "engine", APP_DIR / "engine"),
             (ROOT / "data" / "kb", APP_DIR / "data" / "kb")]
    for src, dst in pairs:
        if not src.exists():
            fail(f"复制 {src.name} 失败：源目录不存在 {src}")
        shutil.copytree(src, dst, dirs_exist_ok=True)
        log(f"    OK  {dst.relative_to(ROOT)}")


# ---------- 5/6 白名单校验（本地数据/密钥绝不入包） ----------

def _is_leak(name):
    low = name.lower()
    if low in LEAK_EXACT:
        return True
    return any(fnmatch(low, pat) for pat in LEAK_GLOBS)


def step_leak_check():
    step(5, "白名单校验（本地数据/密钥绝不入包）")
    leaks = []
    for base, _dirs, files in os.walk(APP_DIR):
        for f in files:
            if _is_leak(f):
                leaks.append(os.path.join(base, f))
    if leaks:
        for p in leaks[:10]:
            log(f"    ! {p}")
        fail("包内发现敏感文件")
    log("    干净 OK")


# ---------- 6/6 压 zip ----------

def step_zip(version):
    step(6, "压 zip（Windows 10+ 自带 tar / 本脚本用 zipfile）")
    zip_path = DIST / f"{APP_NAME}-v{version}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(APP_DIR):
            rel_base = os.path.relpath(base, DIST).replace(os.sep, "/")
            if not dirs and not files:
                zf.writestr(rel_base + "/", "")          # 保留空目录
            for f in files:
                full = os.path.join(base, f)
                arc = os.path.relpath(full, DIST).replace(os.sep, "/")
                zf.write(full, arc)
    size_mb = zip_path.stat().st_size / 1024 / 1024
    log(f"    {zip_path.name}  ({size_mb:.1f} MB)")
    return zip_path


def main():
    ap = argparse.ArgumentParser(
        description="AI错题本 Windows 打包流程（供 packaging/build_windows.bat 调用）")
    ap.add_argument("--run", action="store_true",
                    help="构建完成后启动 dist\\AI错题本\\AI错题本.exe 冒烟")
    args, extra = ap.parse_known_args()
    do_run = args.run or ("run" in extra)      # 兼容旧的 "build_windows.bat run" 位置参数

    step_tests()
    version = app_version()
    log(f"版本：v{version}")

    exe = step_pyinstaller()
    step_copy_assets()
    step_leak_check()
    zip_path = step_zip(version)

    log()
    log(f"完成：{zip_path.relative_to(ROOT)}")
    log("免解压实测：dist\\AI错题本\\ 就是解压后的最终目录，直接运行：")
    log("    dist\\AI错题本\\AI错题本.exe")
    log("预期：自动开浏览器进入激活页（数据目录 %APPDATA%\\WrongBook\\，升级覆盖不丢数据）")
    log('首次运行：SmartScreen 点"更多信息-仍要运行"；防火墙弹窗点"允许"（服务仅绑 127.0.0.1）')

    if do_run:
        log("启动 exe 冒烟...")
        subprocess.Popen([str(exe)], cwd=str(APP_DIR))


if __name__ == "__main__":
    main()
