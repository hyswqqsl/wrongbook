# -*- coding: utf-8 -*-
"""tenant_ctx —— 云端形态（WRONGBOOK_CLOUD=on）的请求级/线程级租户上下文

为什么用 contextvars.ContextVar 而不是 flask.g（Plan B 决策 D-B2）：
  异步转录 / v1.2s 异步入库都在后台 threading.Thread 里跑业务（store/topics/ai_config），
  子线程中 flask.has_request_context() 为 False，g 不可用——若只靠 g 存租户，
  后台任务会回落桌面单机全局态而串户。ContextVar 在「线程入口显式绑定」后
  与主线程行为完全一致。

绑定入口：
  1. HTTP 请求：app._cloud_gate 校验通过后 set_tenant()，teardown_request reset；
  2. 后台线程：启动前捕获 (code, uid)，线程函数最外层 with bind_tenant(code, uid)。

桌面形态（WRONGBOOK_CLOUD=off，默认）：get_tenant() 恒为 None →
store/topics/ai_config 走原有全局态分支，行为与单机版完全一致（零回归）。
"""
import os
from contextvars import ContextVar
from contextlib import contextmanager

# 形态开关：只在进程启动时读一次环境变量；测试直接 patch 本模块的 CLOUD 属性
CLOUD = os.environ.get("WRONGBOOK_CLOUD", "off").strip().lower() in ("1", "on", "true", "yes")

_current_tenant: ContextVar = ContextVar("wb_tenant", default=None)


@contextmanager
def bind_tenant(code, uid):
    """显式绑定租户（后台线程/测试用）；退出自动还原"""
    token = _current_tenant.set((str(code), str(uid)))
    try:
        yield
    finally:
        _current_tenant.reset(token)


def set_tenant(code, uid):
    """绑定并返回 token（请求入口用；配对 reset_tenant）"""
    return _current_tenant.set((str(code), str(uid)))


def reset_tenant(token):
    _current_tenant.reset(token)


def get_tenant():
    """当前租户 (code, uid)；桌面形态/未绑定返回 None"""
    return _current_tenant.get()
