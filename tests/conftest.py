# -*- coding: utf-8 -*-
"""tests 全局夹具：回归套件关闭激活门禁（license_client 默认 True 是 R3 实测期决定，
套件内不依赖真实 ECS；个别激活/门禁用例自行 patch ENABLED）"""
import os

os.environ.setdefault("WRONGBOOK_LICENSE", "off")
