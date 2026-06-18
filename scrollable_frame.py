#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FWD Image Search — 自定义可滚动框架（支持虚拟滚动模式）
"""

import tkinter as tk
from tkinter import ttk

from config import Colors


class ScrollableFrame(ttk.Frame):
    """可滚动的 Frame 容器 —— 支持虚拟滚动模式"""

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.canvas = tk.Canvas(self, highlightthickness=0, bg=Colors.BG_MAIN)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=Colors.BG_MAIN)

        self.canvas.create_window((0, 0), window=self.inner, anchor="nw", tags="inner")
        self.canvas.configure(yscrollcommand=self._on_scroll_command)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self._on_scroll_callback: callable | None = None
        self._bind_mousewheel()
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def set_on_scroll(self, callback: callable) -> None:
        """注册滚动回调（用于虚拟滚动同步）"""
        self._on_scroll_callback = callback

    def _on_scroll_command(self, *args):
        """拦截滚动条命令，触发回调后再滚动"""
        self.scrollbar.set(*args)
        if self._on_scroll_callback:
            self._on_scroll_callback()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig("inner", width=event.width)
        if self._on_scroll_callback:
            self._on_scroll_callback()

    def _bind_mousewheel(self):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event):
        """鼠标滚轮滚动 — 仅当鼠标在 canvas 区域时生效"""
        widget = event.widget.winfo_containing(event.x_root, event.y_root)
        if widget and (widget is self.canvas or str(self.canvas) in str(widget)):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if self._on_scroll_callback:
                self._on_scroll_callback()
