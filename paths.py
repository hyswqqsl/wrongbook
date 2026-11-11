# -*- coding: utf-8 -*-
"""paths —— 目录解析与 JSON 写入的唯一收口（R1 数据外置改造）

目录解析（优先级从高到低）：
1. WRONGBOOK_DATA 环境变量：显式覆盖（开发/测试/多实例用）
2. 打包态（sys.frozen，PyInstaller onedir）：平台标准数据目录 —— 升级覆盖安装不丢数据
   Windows %APPDATA%/WrongBook/data ｜ macOS ~/Library/Application Support/WrongBook/data
   ｜ Linux ~/.local/share/WrongBook/data
3. 开发态（非 frozen）：<项目>/data —— 与历史行为一致，存量数据零迁移

程序目录 app_root()：frozen 时取 exe 所在目录（static/、engine/、KB 种子随包），
开发态为项目根。out/ 产物目录随 DATA_HOME（程序目录只读原则：升级=直接覆盖替换）。

JSON 写入收口 atomic_write_json()：tmp 落盘 + flush/fsync + .bak 三代轮换
（.bak=上一版 / .bak.1 / .bak.2）+ os.replace 原子替换 + 目录 fsync。
"""
import os, sys, json, time, shutil

APP_DIR_NAME = "WrongBook"   # 数据目录名（避免中文：跨平台路径/终端/脚本兼容性）
DATA_SCHEMA_VERSION = 1   # data/meta.json 结构版本（未来迁移按 1→2→… 顺序挂钩子）
APP_VERSION = "1.4.2"     # 应用版本号（唯一源：license 心跳上报/R4 打包命名/升级检查共用）


def app_root():
    """程序（代码/静态资源/引擎/种子资产）所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_home():
    """数据根目录解析（见模块 docstring 的优先级）"""
    env = os.environ.get("WRONGBOOK_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            return os.path.join(base, APP_DIR_NAME, "data")
        if sys.platform == "darwin":
            return os.path.expanduser(
                os.path.join("~", "Library", "Application Support", APP_DIR_NAME, "data"))
        return os.path.expanduser(os.path.join("~", ".local", "share", APP_DIR_NAME, "data"))
    return os.path.join(app_root(), "data")   # 开发态：项目内 data/，行为不变


DATA_HOME = data_home()   # import 时定值；运行期测试重定向走 store.set_test_data_dir


# ---------- JSON 原子写入（fsync + .bak 三代轮换） ----------

def _rotate_bak(path):
    """轮换备份链：.bak.2←.bak.1←.bak←当前（保存前调用，.bak 恒为上一版）"""
    for old, new in ((path + ".bak.1", path + ".bak.2"),
                     (path + ".bak", path + ".bak.1")):
        if os.path.exists(old):
            try:
                os.replace(old, new)
            except OSError:
                pass
    if os.path.exists(path):
        try:
            shutil.copy(path, path + ".bak")
        except OSError:
            pass


def _fsync_dir(d):
    """目录 fsync（POSIX）：确保重命名/新文件落盘；Windows 无目录句柄，静默跳过"""
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, AttributeError, NotImplementedError):
        pass


def atomic_write_json(path, obj, indent=1):
    """原子写 JSON：tmp+fsync → .bak 轮换 → os.replace → 目录 fsync"""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    _rotate_bak(path)
    os.replace(tmp, path)
    _fsync_dir(d)


# ---------- data/meta.json 版本钩子 ----------

def ensure_meta(data_dir=None, version=DATA_SCHEMA_VERSION):
    """确保 data/meta.json 存在且 schema 达到当前版本；返回 meta 内容。

    首次（含存量老数据目录）写入 {"schema":1,...}；将来版本升级时按
    schema+1→…→version 顺序执行迁移钩子后更新版本号（当前迁移链为空）。
    """
    d = data_dir or DATA_HOME
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "meta.json")
    meta = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}   # meta 损坏不阻塞业务数据（按待迁移处理重建）
    if not isinstance(meta, dict):
        meta = {}
    v = int(meta.get("schema") or 0)
    if v > version:
        meta["schema_warn"] = "数据由更高版本程序产生，回退旧版可能丢新结构"
        return meta   # 降级保护：不写回，只提示
    if v < version:
        meta.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        meta["schema"] = version
        meta["upgraded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write_json(p, meta)
    return meta
