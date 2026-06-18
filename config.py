#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FWD Image Search — 配置、常量与工具函数
"""

import os
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("请先安装 Pillow 库：pip install Pillow")
    sys.exit(1)

# 拖拽上传支持
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False


# ─── 配置文件路径 ──────────────────────────────────────────


def _get_config_path() -> str:
    """获取配置文件路径"""
    app_dir = os.path.join(os.path.expanduser("~"), ".image_manager")
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, "config.json")


# ─── 配色方案 ─────────────────────────────────────────────


class Colors:
    BG_MAIN = "#F0F2F5"
    BG_SIDEBAR = "#1E293B"
    BG_CARD = "#FFFFFF"
    BG_TOOLBAR = "#FFFFFF"
    BG_INPUT = "#F1F5F9"
    BG_HOVER = "#E8ECF1"
    BG_SELECTED = "#DBEAFE"

    TEXT_PRIMARY = "#1E293B"
    TEXT_SECONDARY = "#64748B"
    TEXT_SIDEBAR = "#CBD5E1"
    TEXT_SIDEBAR_ACTIVE = "#FFFFFF"
    TEXT_PLACEHOLDER = "#94A3B8"

    ACCENT = "#3B82F6"
    ACCENT_HOVER = "#2563EB"
    ACCENT_LIGHT = "#EFF6FF"
    DANGER = "#EF4444"
    DANGER_HOVER = "#DC2626"
    SUCCESS = "#10B981"
    WARNING = "#F59E0B"

    BORDER = "#E2E8F0"
    DIVIDER = "#F1F5F9"

    FONT_FAMILY = "Microsoft YaHei" if os.name == "nt" else "Helvetica"


# ─── 图片工具函数 ─────────────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico", ".svg"}


def is_image_file(filepath: str) -> bool:
    return Path(filepath).suffix.lower() in IMAGE_EXTENSIONS


def get_image_files_and_struct(folders: list[str]) -> tuple[list[str], dict]:
    """遍历所有监控文件夹，返回 (排序后的图片列表, 文件夹结构缓存)"""
    images = []
    seen = set()
    folder_struct: dict[str, dict] = {}

    for folder in folders:
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            # 初始化当前目录的缓存条目
            if root not in folder_struct:
                folder_struct[root] = {"subdirs": [], "image_count": 0}

            # 记录直接子目录（完整路径，排序）
            subdir_paths = []
            for d in sorted(dirs):
                full = os.path.join(root, d)
                subdir_paths.append(full)
                # 预填充子目录条目（即使没有图片也保留，确保树结构完整）
                if full not in folder_struct:
                    folder_struct[full] = {"subdirs": [], "image_count": 0}
            folder_struct[root]["subdirs"] = subdir_paths

            # 统计当前目录直接包含的图片
            for f in files:
                if is_image_file(f):
                    full = os.path.join(root, f)
                    if full not in seen:
                        seen.add(full)
                        images.append(full)
                        folder_struct[root]["image_count"] += 1

    return sorted(images), folder_struct


def make_thumbnail(img_path: str, size: tuple[int, int] = (160, 160)) -> Image.Image | None:
    """生成正方形缩略图（居中裁剪）"""
    try:
        img = Image.open(img_path)
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize(size, Image.LANCZOS)
        return img
    except Exception:
        return None


# ─── DPI 缩放工具 ──────────────────────────────────────────


def _get_windows_dpi_scale() -> float:
    """获取 Windows 系统 DPI 缩放因子（96 DPI = 1.0）"""
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(dpi / 96.0, 1.0)
    except Exception:
        return 1.0
