# -*- coding: utf-8 -*-
"""题库 Schema —— generate.py 与 generate_any.py 共用，practice.py 直接读 JSON"""
from pydantic import BaseModel
from typing import Literal

class Choice(BaseModel):
    key: Literal["A", "B", "C", "D"]
    text: str

class Seg(BaseModel):
    a: str                          # 起点名称
    b: str                          # 终点名称
    dashed: bool = False            # 虚线（通常表示辅助线）

class Point(BaseModel):
    name: str                       # 点名，如 "A"
    x: float                        # 0~10 坐标系
    y: float

class FigureSpec(BaseModel):
    """原创题绘图指令：模型输出命名点+连线，导出时用 matplotlib 渲染成图"""
    points: list[Point]
    segments: list[Seg] = []

class Question(BaseModel):
    type: Literal["single", "multiple", "fill", "short"]  # 单选/多选/填空/简答
    question: str
    choices: list[Choice] = []      # 选择题才有
    answer: str
    explanation: str                # 解析（引用教材原句依据）
    knowledge_point: str            # 考点归类
    difficulty: Literal["easy", "medium", "hard"]
    source_page: int = 0            # 题目主要依据的页码（按提供页序从1计，0=未知）
    figure_box: list[float] | None = None  # 原文配图在页内位置 [l,t,r,b] 0~1比例，None=无图
    figure_spec: FigureSpec | None = None  # 原创题绘图指令（与 figure_box 互斥，优先用）

class QuizBank(BaseModel):
    chapter: str
    questions: list[Question]
