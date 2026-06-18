#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FWD Image Search - 现代化GUI图片浏览与管理工具
功能：
  1. 上传本地图片，可选择目标文件夹（支持持久化记住选择）
  2. 选择多个文件夹，集中浏览和管理图片
  3. 缩略图网格 + 大图预览 + 名称编辑
  4. 文件夹列表、窗口状态自动持久化到配置文件

依赖：pip install Pillow
"""

import os
import sys
import tkinter as tk

from config import _DND_AVAILABLE, _get_windows_dpi_scale
from app import ImageViewerApp


def main():
    # 必须在创建任何窗口之前设置 DPI 感知，否则 Tk 会按虚拟 DPI (96) 初始化
    if os.name == "nt":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            pass

    # 获取实际 DPI 缩放因子
    dpi_scale = _get_windows_dpi_scale() if os.name == "nt" else 1.0

    if _DND_AVAILABLE:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    # 设置 tkinter 全局缩放，使高 DPI 屏幕下文字和控件自适应
    root.tk.call("tk", "scaling", dpi_scale)

    root.title(" FWD Image Search")

    app = ImageViewerApp(root, dpi_scale)
    root.mainloop()


if __name__ == "__main__":
    main()
