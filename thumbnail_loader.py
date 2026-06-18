#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FWD Image Search — 后台缩略图加载器
线程池加载 + 内存 LRU 缓存 + 磁盘持久化缓存
"""

import os
import hashlib
import threading
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageTk


class ThumbnailLoader:
    """后台线程池加载缩略图，内存 LRU 缓存 + 磁盘持久化缓存，UI 回调"""

    MAX_CACHE = 500
    DISK_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".image_manager", "thumbnails")

    def __init__(self, root: tk.Tk, thumb_size: tuple[int, int] = (150, 150)):
        self.root = root
        self.thumb_size = thumb_size
        self.cache: OrderedDict[str, tk.PhotoImage] = OrderedDict()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        # 根据 CPU 核心数动态设置线程池，提升批量加载吞吐量
        cpu_count = os.cpu_count() or 4
        workers = max(4, min(8, cpu_count))
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self.on_loaded: callable | None = None  # (img_path, photo) -> void
        # 确保磁盘缓存目录存在
        os.makedirs(self.DISK_CACHE_DIR, exist_ok=True)

    def enqueue(self, img_path: str) -> None:
        """入队加载缩略图（已缓存或正在加载的跳过）"""
        with self._lock:
            if img_path in self.cache or img_path in self._pending:
                return
            self._pending.add(img_path)
        self._executor.submit(self._load_task, img_path)

    def _cache_key(self, img_path: str) -> str:
        """根据图片路径 + 缩略图尺寸生成缓存文件名（MD5 哈希）"""
        raw = f"{img_path}:{self.thumb_size[0]}x{self.thumb_size[1]}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest() + ".png"

    def _cache_filepath(self, img_path: str) -> str:
        """获取缓存文件的完整路径"""
        return os.path.join(self.DISK_CACHE_DIR, self._cache_key(img_path))

    def _load_from_disk_cache(self, img_path: str) -> Image.Image | None:
        """从磁盘缓存加载缩略图 PIL Image，若缓存无效或不存在返回 None"""
        cache_path = self._cache_filepath(img_path)
        if not os.path.exists(cache_path):
            return None
        try:
            # 检查缓存是否比源图更新
            src_mtime = os.path.getmtime(img_path)
            cache_mtime = os.path.getmtime(cache_path)
            if cache_mtime < src_mtime:
                return None  # 源图已更新，缓存失效
            return Image.open(cache_path).convert("RGB")
        except Exception:
            return None

    def _save_to_disk_cache(self, img_path: str, pil_img: Image.Image) -> None:
        """将缩略图保存到磁盘缓存"""
        try:
            cache_path = self._cache_filepath(img_path)
            pil_img.save(cache_path, "PNG")
        except Exception:
            pass  # 缓存写入失败不影响主流程

    def _load_task(self, img_path: str) -> None:
        """后台线程：优先从磁盘缓存加载，未命中则生成并写入缓存"""
        try:
            # 先尝试磁盘缓存
            cached = self._load_from_disk_cache(img_path)
            if cached is not None:
                self.root.after(0, lambda: self._on_done(img_path, cached))
                return

            # 磁盘缓存未命中，正常生成缩略图
            img = Image.open(img_path)
            img = img.convert("RGB")
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
            img = img.resize(self.thumb_size, Image.LANCZOS)
            # 写入磁盘缓存
            self._save_to_disk_cache(img_path, img)
            # 回调主线程
            self.root.after(0, lambda: self._on_done(img_path, img))
        except Exception:
            self.root.after(0, lambda: self._on_error(img_path))

    def _on_done(self, img_path: str, pil_img: Image.Image) -> None:
        """主线程：生成 PhotoImage 并加入 LRU 缓存"""
        with self._lock:
            self._pending.discard(img_path)
        try:
            photo = ImageTk.PhotoImage(pil_img)
        except Exception:
            return

        with self._lock:
            if len(self.cache) >= self.MAX_CACHE:
                # LRU 淘汰最旧项
                oldest = next(iter(self.cache))
                del self.cache[oldest]
            self.cache[img_path] = photo

        if self.on_loaded:
            self.on_loaded(img_path, photo)

    def _on_error(self, img_path: str) -> None:
        with self._lock:
            self._pending.discard(img_path)

    def get(self, img_path: str) -> tk.PhotoImage | None:
        """同步获取已缓存的缩略图（无阻塞）"""
        with self._lock:
            return self.cache.get(img_path)

    def preload_batch(self, paths: list[str]) -> None:
        """批量入队加载"""
        for p in paths:
            self.enqueue(p)

    def clear(self, disk: bool = False) -> None:
        """清空缓存。disk=True 时同时清除磁盘缓存。"""
        with self._lock:
            self.cache.clear()
            self._pending.clear()
        if disk:
            try:
                for f in os.listdir(self.DISK_CACHE_DIR):
                    os.remove(os.path.join(self.DISK_CACHE_DIR, f))
            except Exception:
                pass

    def clear_disk_cache(self) -> None:
        """清除所有磁盘缓存文件"""
        self.clear(disk=True)

    def disk_cache_size(self) -> int:
        """返回磁盘缓存文件数量"""
        try:
            return len(os.listdir(self.DISK_CACHE_DIR))
        except Exception:
            return 0

    def set_thumb_size(self, size: tuple[int, int]) -> None:
        """更新缩略图尺寸并清空缓存（尺寸变化时调用）"""
        self.thumb_size = size
        self.clear()

    def shutdown(self) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=False)
