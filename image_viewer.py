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
import json
import shutil
import hashlib
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor, Future
from collections import OrderedDict

# 拖拽上传支持
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
from pathlib import Path

try:
    from PIL import Image, ImageTk, ImageDraw
except ImportError:
    print("请先安装 Pillow 库：pip install Pillow")
    sys.exit(1)


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


# ─── 后台缩略图加载器 ─────────────────────────────────────
class ThumbnailLoader:
    """后台线程池加载缩略图，内存 LRU 缓存 + 磁盘持久化缓存，UI 回调"""

    MAX_CACHE = 300
    DISK_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".image_manager", "thumbnails")

    def __init__(self, root: tk.Tk, thumb_size: tuple[int, int] = (150, 150)):
        self.root = root
        self.thumb_size = thumb_size
        self.cache: OrderedDict[str, tk.PhotoImage] = OrderedDict()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)
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


# ─── 自定义滚动框架 ──────────────────────────────────────
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


# ─── 主应用程序 ───────────────────────────────────────────
class ImageViewerApp:
    def __init__(self, root: tk.Tk, dpi_scale: float = 1.0):
        self.root = root
        self._dpi_scale = dpi_scale  # 用于缩放所有硬编码像素值
        self.root.title(" FWD Image Search")
        self.root.geometry(
            f"{int(1280 * dpi_scale)}x{int(800 * dpi_scale)}"
        )
        self.root.minsize(
            int(960 * dpi_scale), int(600 * dpi_scale)
        )
        self.root.configure(bg=Colors.BG_MAIN)

        # 配置文件路径
        self.config_path = _get_config_path()

        # 状态数据
        self.folders: list[str] = []
        self.all_images: list[str] = []
        self.current_image: str | None = None
        self._selected_path: str = ""  # 当前选中的图片路径
        self.sidebar_collapsed: bool = False
        self._search_after_id: str | None = None
        self._default_upload_target: str = ""  # 持久化的默认上传位置
        self._resize_after_id: str | None = None  # 窗口缩放防抖
        self._last_thumb_container_width: int = 0  # 上次缩略图容器宽度
        self._collapsed_folders: set[str] = set()  # 已折叠的文件夹路径
        self._current_folder_filter: str | None = None  # 当前筛选的文件夹路径（再次点击取消）
        self._folder_struct_cache: dict = {}  # {path: {"subdirs": [str], "image_count": int}}
        self._folder_widgets: dict[str, tk.Frame] = {}  # 文件夹项控件缓存（path -> item_frame）
        self._folder_drag_info: dict | None = None  # 拖拽排序状态

        # 虚拟网格状态
        self._card_slots: list[dict] = []       # [{frame, img_lbl, name_lbl, canvas_id, place_id}, ...]
        self._displayed_images: list[str] = []   # 当前过滤后的图片列表
        self._card_cols: int = 1                 # 当前列数

        # 先从配置文件读取用户缩放倍率，确保 UI 构建时使用正确的尺寸
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._user_scale = json.load(f).get("user_scale", 1.0)
            else:
                self._user_scale = 1.0
        except Exception:
            self._user_scale = 1.0

        # DPI 感知的像素尺寸缩放（动态读取 _user_scale）
        def _sp(v: int) -> int:
            """将基准尺寸按 DPI × 用户缩放 动态计算"""
            return int(v * self._dpi_scale * self._user_scale)

        self._sp = _sp

        # 缩略图及卡片尺寸（基准 150px，按 DPI 缩放）
        self.THUMB_SIZE = _sp(150)
        self.CARD_PAD = _sp(10)
        self.CARD_WIDTH = self.THUMB_SIZE + _sp(4)
        self.CARD_HEIGHT = self.THUMB_SIZE + _sp(46)
        self._place_size = self.THUMB_SIZE + self.CARD_PAD

        # 设置图标
        self._set_app_icon()

        # 后台缩略图加载器
        self.thumbnail_loader = ThumbnailLoader(root, thumb_size=(self.THUMB_SIZE, self.THUMB_SIZE))
        self.thumbnail_loader.on_loaded = self._on_thumbnail_loaded

        # 拖拽上传
        self._dnd_overlay: tk.Frame | None = None
        self._setup_drag_drop()

        # 构建 UI
        self._build_toolbar()
        self._build_main_layout()
        self._build_statusbar()

        # 加载持久化状态
        self._load_config()
        # 强制完成布局计算，确保 Canvas 拿到真实尺寸后再渲染缩略图
        self.root.update_idletasks()
        self._load_initial_state()

        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_app_icon(self):
        """设置窗口图标（优先使用 app_icon.ico）"""
        # 查找图标文件路径（支持 PyInstaller 打包后和开发模式）
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "app_icon.ico")
        try:
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
                return
        except Exception:
            pass
        # 回退：动态生成图标
        try:
            icon_img = Image.new("RGBA", (64, 64), (59, 130, 246, 255))
            draw = ImageDraw.Draw(icon_img)
            draw.rectangle([12, 16, 52, 48], fill="white", outline="white", width=2)
            draw.ellipse([22, 24, 32, 34], fill=(59, 130, 246))
            draw.polygon([22, 40, 42, 26, 42, 40], fill=(59, 130, 246))
            photo = ImageTk.PhotoImage(icon_img)
            self.root.iconphoto(True, photo)
            self._icon_ref = photo
        except Exception:
            pass

    # ── 配置持久化 ────────────────────────────────────────
    def _load_config(self):
        """从配置文件加载状态"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # 恢复文件夹列表（过滤掉已不存在的路径）
                saved_folders = cfg.get("folders", [])
                self.folders = [f for f in saved_folders if os.path.isdir(f)]
                if len(self.folders) != len(saved_folders):
                    removed = set(saved_folders) - set(self.folders)
                    if removed:
                        print(f"已移除不存在的文件夹：{removed}")

                self._default_upload_target = cfg.get("default_upload_target", "")
                self.sidebar_collapsed = cfg.get("sidebar_collapsed", False)
                self._user_scale = cfg.get("user_scale", 1.0)
                # 更新缩放标签显示
                self._scale_label.config(text=f"{int(self._user_scale * 100)}%")

                # 恢复窗口几何
                geo = cfg.get("window_geometry", "")
                if geo:
                    try:
                        self.root.geometry(geo)
                    except Exception:
                        pass
        except Exception as e:
            print(f"加载配置失败：{e}")

    def _save_config(self):
        """保存当前状态到配置文件"""
        try:
            cfg = {
                "folders": self.folders,
                "default_upload_target": self._default_upload_target,
                "sidebar_collapsed": self.sidebar_collapsed,
                "user_scale": self._user_scale,
                "window_geometry": self.root.geometry(),
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败：{e}")

    def _auto_save(self):
        """延迟自动保存（防抖 500ms）"""
        if hasattr(self, "_save_after_id") and self._save_after_id:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(500, self._save_config)

    def _on_close(self):
        """窗口关闭时保存配置"""
        self._save_config()
        self.thumbnail_loader.shutdown()
        self.root.destroy()

    # ── 顶部工具栏 ────────────────────────────────────────
    def _build_toolbar(self):
        toolbar = tk.Frame(self.root, bg=Colors.BG_TOOLBAR, height=self._sp(56))
        toolbar.pack(fill="x", side="top")
        toolbar.pack_propagate(False)

        inner = tk.Frame(toolbar, bg=Colors.BG_TOOLBAR)
        inner.pack(fill="both", expand=True, padx=20, pady=8)

        # ── 左侧：标题 + 分隔线 ──
        title = tk.Label(
            inner, text=" FWD Image Search",
            font=(Colors.FONT_FAMILY, 16, "bold"),
            fg=Colors.TEXT_PRIMARY, bg=Colors.BG_TOOLBAR,
        )
        title.pack(side="left", padx=(0, 16))

        sep1 = tk.Frame(inner, bg=Colors.BORDER, width=1)
        sep1.pack(side="left", fill="y", padx=10)

        # ── 搜索区（标题右侧） ──
        search_frame = tk.Frame(inner, bg=Colors.BG_TOOLBAR)
        search_frame.pack(side="left", padx=4)

        # 搜索图标
        tk.Label(
            search_frame, text="🔍", font=(Colors.FONT_FAMILY, 13),
            bg=Colors.BG_TOOLBAR, fg=Colors.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 6))

        def make_search_entry(placeholder: str):
            """创建一个搜索框 — 仅在用户输入文字后才触发搜索"""
            sv = tk.StringVar()
            entry = tk.Entry(
                search_frame, textvariable=sv,
                font=(Colors.FONT_FAMILY, 11), bg=Colors.BG_INPUT,
                fg=Colors.TEXT_PLACEHOLDER, insertbackground=Colors.TEXT_PRIMARY,
                relief="flat", bd=8, width=13,
            )
            entry.pack(side="left", padx=(0, 6))
            entry.insert(0, placeholder)
            entry.config(fg=Colors.TEXT_PLACEHOLDER)
            # 用 _placeholder 属性标记待清除 / 待恢复
            entry._placeholder = placeholder

            def on_focus_in(e):
                if entry.get() == entry._placeholder:
                    entry.delete(0, "end")
                    entry.config(fg=Colors.TEXT_PRIMARY)

            def on_focus_out(e):
                if entry.get().strip() == "":
                    entry.delete(0, "end")
                    entry.insert(0, entry._placeholder)
                    entry.config(fg=Colors.TEXT_PLACEHOLDER)

            def on_key_release(e):
                """仅在用户实际按键后触发搜索（忽略 Ctrl/Cmd/Shift 等修饰键）"""
                if e.keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                                "Alt_L", "Alt_R", "Meta_L", "Meta_R",
                                "Tab", "Escape", "Return", "Caps_Lock"):
                    return
                self._on_search()

            entry.bind("<FocusIn>", on_focus_in)
            entry.bind("<FocusOut>", on_focus_out)
            entry.bind("<KeyRelease>", on_key_release)
            return sv, entry

        self.search_var1, self.search_entry1 = make_search_entry("文件名...")
        self.search_var2, self.search_entry2 = make_search_entry("路径...")

        # ── 分隔线 ──
        sep2 = tk.Frame(inner, bg=Colors.BORDER, width=1)
        sep2.pack(side="left", fill="y", padx=10)

        # ── 右侧：操作按钮组 ──
        actions_frame = tk.Frame(inner, bg=Colors.BG_TOOLBAR)
        actions_frame.pack(side="left")

        self.btn_upload = self._make_button(
            actions_frame, text="📤 上传图片", command=self.upload_images, accent=True,
        )
        self.btn_upload.pack(side="left", padx=3)

        # 上传目标路径选择器
        upload_target_frame = tk.Frame(actions_frame, bg=Colors.BG_TOOLBAR)
        upload_target_frame.pack(side="left", padx=4)

        tk.Label(
            upload_target_frame, text="→",
            font=(Colors.FONT_FAMILY, 10), fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR,
        ).pack(side="left", padx=(0, 4))

        self.upload_target_var = tk.StringVar(value="")
        self.upload_target_btn = tk.Label(
            upload_target_frame,
            textvariable=self.upload_target_var,
            font=(Colors.FONT_FAMILY, 10, "underline"),
            fg=Colors.ACCENT, bg=Colors.BG_TOOLBAR,
            cursor="hand2",
        )
        self.upload_target_btn.pack(side="left")
        self.upload_target_btn.bind("<Button-1>", lambda e: self._choose_upload_target())

        change_btn = tk.Label(
            upload_target_frame,
            text="📂",
            font=(Colors.FONT_FAMILY, 12),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR,
            cursor="hand2",
        )
        change_btn.pack(side="left", padx=(2, 0))
        change_btn.bind("<Button-1>", lambda e: self._choose_upload_target())

        self.btn_add_folder = self._make_button(
            actions_frame, text="📁 添加文件夹", command=self.add_folder, accent=False,
        )
        self.btn_add_folder.pack(side="left", padx=3)

        self.btn_refresh = self._make_button(
            actions_frame, text="🔄 刷新", command=self._refresh_clear_filter, accent=False,
        )
        self.btn_refresh.pack(side="left", padx=3)

        # ── 缩放比例调节 ──
        sep3 = tk.Frame(inner, bg=Colors.BORDER, width=1)
        sep3.pack(side="left", fill="y", padx=10)

        scale_frame = tk.Frame(inner, bg=Colors.BG_TOOLBAR)
        scale_frame.pack(side="left")

        tk.Label(
            scale_frame, text="🔍", font=(Colors.FONT_FAMILY, 12),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR,
        ).pack(side="left", padx=(0, 4))

        scale_minus = tk.Label(
            scale_frame, text="−", font=(Colors.FONT_FAMILY, 13, "bold"),
            fg=Colors.ACCENT, bg=Colors.BG_TOOLBAR, cursor="hand2",
        )
        scale_minus.pack(side="left")
        scale_minus.bind("<Button-1>", lambda e: self._adjust_scale(-0.1))

        self._scale_label = tk.Label(
            scale_frame, text="100%",
            font=(Colors.FONT_FAMILY, 11, "bold"),
            fg=Colors.TEXT_PRIMARY, bg=Colors.BG_TOOLBAR, width=5,
            anchor="center",
        )
        self._scale_label.pack(side="left", padx=2)

        scale_plus = tk.Label(
            scale_frame, text="＋", font=(Colors.FONT_FAMILY, 13, "bold"),
            fg=Colors.ACCENT, bg=Colors.BG_TOOLBAR, cursor="hand2",
        )
        scale_plus.pack(side="left")
        scale_plus.bind("<Button-1>", lambda e: self._adjust_scale(0.1))

        # 重置按钮
        scale_reset = tk.Label(
            scale_frame, text="↺", font=(Colors.FONT_FAMILY, 11),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR, cursor="hand2",
        )
        scale_reset.pack(side="left", padx=(4, 0))
        scale_reset.bind("<Button-1>", lambda e: self._reset_scale())

        # ── 底部分隔线 ──
        border = tk.Frame(toolbar, bg=Colors.BORDER, height=1)
        border.pack(fill="x", side="bottom")

    def _make_button(self, parent, text, command, accent=False):
        btn = tk.Button(
            parent, text=text, command=command,
            font=(Colors.FONT_FAMILY, 11), relief="flat", bd=0,
            padx=16, pady=6, cursor="hand2",
        )
        if accent:
            btn.config(
                bg=Colors.ACCENT, fg="white",
                activebackground=Colors.ACCENT_HOVER, activeforeground="white",
            )
        else:
            btn.config(
                bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                activebackground=Colors.BG_HOVER, activeforeground=Colors.TEXT_PRIMARY,
            )
        return btn

    def _adjust_scale(self, delta: float):
        """调整用户缩放倍率（每次增减 10%）"""
        new_scale = round(self._user_scale + delta, 2)
        if new_scale < 0.5 or new_scale > 3.0:
            return
        self._user_scale = new_scale
        self._scale_label.config(text=f"{int(new_scale * 100)}%")
        # 更新缩略图尺寸
        self.THUMB_SIZE = self._sp(150)
        self.CARD_PAD = self._sp(10)
        self.CARD_WIDTH = self.THUMB_SIZE + self._sp(4)
        self.CARD_HEIGHT = self.THUMB_SIZE + self._sp(46)
        self._place_size = self.THUMB_SIZE + self.CARD_PAD
        # 更新缩略图加载器尺寸并清空缓存
        self.thumbnail_loader.set_thumb_size((self.THUMB_SIZE, self.THUMB_SIZE))
        self._render_thumbnails(folder_filter=self._current_folder_filter)
        self._update_preview_panel()
        self._auto_save()

    def _reset_scale(self):
        """重置缩放为 100%"""
        if self._user_scale == 1.0:
            return
        self._adjust_scale(1.0 - self._user_scale)

    # ── 主布局（三栏） ────────────────────────────────────
    def _build_main_layout(self):
        self.main = tk.Frame(self.root, bg=Colors.BG_MAIN)
        self.main.pack(fill="both", expand=True, side="top")
        self.main.grid_rowconfigure(0, weight=1)
        self.main.grid_columnconfigure(0, weight=0)
        self.main.grid_columnconfigure(1, weight=1)
        self.main.grid_columnconfigure(2, weight=0)

        # ── 左侧边栏 ──
        self.sidebar = tk.Frame(self.main, bg=Colors.BG_SIDEBAR, width=self._sp(240))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.pack_propagate(False)
        self.sidebar.grid_propagate(False)
        self._build_sidebar()

        # ── 折叠后的窄条 ──
        self.collapsed_bar = tk.Frame(self.main, bg=Colors.BG_SIDEBAR, width=self._sp(40))
        self.collapsed_bar.grid_propagate(False)

        expand_btn = tk.Label(
            self.collapsed_bar, text="▶",
            font=(Colors.FONT_FAMILY, 12, "bold"),
            fg=Colors.TEXT_SIDEBAR_ACTIVE, bg=Colors.BG_SIDEBAR, cursor="hand2",
        )
        expand_btn.pack(pady=16)
        expand_btn.bind("<Button-1>", lambda e: self._toggle_sidebar())

        dir_label = tk.Label(
            self.collapsed_bar, text="目\n录",
            font=(Colors.FONT_FAMILY, 9),
            fg=Colors.TEXT_SIDEBAR, bg=Colors.BG_SIDEBAR, justify="center",
        )
        dir_label.pack(side="top", pady=4)

        # ── 中间缩略图区 ──
        center = tk.Frame(self.main, bg=Colors.BG_MAIN)
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)

        self.thumbnail_area = ScrollableFrame(center)
        self.thumbnail_area.grid(row=0, column=0, sticky="nsew")
        self.thumbnail_area.set_on_scroll(self._on_scroll_sync)

        # 监听缩略图容器宽度变化 → 防抖重排
        self.thumbnail_area.canvas.bind("<Configure>", self._on_thumbnail_container_resize, add="+")

        self.empty_label = tk.Label(
            self.thumbnail_area.inner,
            text="📂\n\n添加文件夹或上传图片开始浏览",
            font=(Colors.FONT_FAMILY, 14),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_MAIN, justify="center",
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        # ── 右侧预览面板 ──
        self.preview_panel = tk.Frame(self.main, bg=Colors.BG_CARD, width=self._sp(300))
        self.preview_panel.grid(row=0, column=2, sticky="ns")
        self.preview_panel.grid_propagate(False)
        self._build_preview_panel()

    # ── 侧边栏 ────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar_header = tk.Frame(self.sidebar, bg=Colors.BG_SIDEBAR)
        sidebar_header.pack(fill="x", padx=8, pady=(12, 6))

        tk.Label(
            sidebar_header, text="📁 文件夹",
            font=(Colors.FONT_FAMILY, 12, "bold"),
            fg=Colors.TEXT_SIDEBAR_ACTIVE, bg=Colors.BG_SIDEBAR,
        ).pack(side="left")

        self.folder_count_label = tk.Label(
            sidebar_header, text="(0)", font=(Colors.FONT_FAMILY, 11),
            fg=Colors.TEXT_SIDEBAR, bg=Colors.BG_SIDEBAR,
        )
        self.folder_count_label.pack(side="right", padx=(0, 6))

        # 回到顶部按钮（折叠所有文件夹并滚动到顶部）
        top_btn = tk.Label(
            sidebar_header, text="⬆", font=(Colors.FONT_FAMILY, 12),
            fg=Colors.TEXT_SIDEBAR, bg=Colors.BG_SIDEBAR, cursor="hand2",
        )
        top_btn.pack(side="right", padx=(0, 2))
        top_btn.bind("<Button-1>", lambda e: self._collapse_all_to_top())

        collapse_btn = tk.Label(
            sidebar_header, text="◀", font=(Colors.FONT_FAMILY, 10, "bold"),
            fg=Colors.TEXT_SIDEBAR, bg=Colors.BG_SIDEBAR, cursor="hand2",
        )
        collapse_btn.pack(side="right")
        collapse_btn.bind("<Button-1>", lambda e: self._toggle_sidebar())

        # 文件夹列表（可滚动）
        folder_list_frame = tk.Frame(self.sidebar, bg=Colors.BG_SIDEBAR)
        folder_list_frame.pack(fill="both", expand=True, padx=4, pady=0)

        self.folder_canvas = tk.Canvas(
            folder_list_frame, bg=Colors.BG_SIDEBAR, highlightthickness=0, bd=0,
        )
        folder_scrollbar = ttk.Scrollbar(
            folder_list_frame, orient="vertical", command=self.folder_canvas.yview,
        )
        self.folder_inner = tk.Frame(self.folder_canvas, bg=Colors.BG_SIDEBAR)

        self.folder_inner.bind(
            "<Configure>",
            lambda e: self.folder_canvas.configure(scrollregion=self.folder_canvas.bbox("all")),
        )
        self.folder_canvas.create_window((0, 0), window=self.folder_inner, anchor="nw", tags="folder_inner")
        self.folder_canvas.configure(yscrollcommand=folder_scrollbar.set)

        self.folder_canvas.pack(side="left", fill="both", expand=True)
        folder_scrollbar.pack(side="right", fill="y")

        self.folder_canvas.bind(
            "<Configure>",
            lambda e: self.folder_canvas.itemconfig("folder_inner", width=e.width),
        )

        # 鼠标滚轮滚动（仅当鼠标在侧边栏文件夹区域时生效）
        def _on_folder_mousewheel(event):
            widget = event.widget.winfo_containing(event.x_root, event.y_root)
            if widget and (widget is self.folder_canvas or str(self.folder_canvas) in str(widget)):
                self.folder_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.folder_canvas.bind_all("<MouseWheel>", _on_folder_mousewheel, add="+")
        self.folder_canvas.bind("<Button-4>", lambda e: self.folder_canvas.yview_scroll(-1, "units"))
        self.folder_canvas.bind("<Button-5>", lambda e: self.folder_canvas.yview_scroll(1, "units"))


        # 底部"添加"按钮
        add_btn_frame = tk.Frame(self.sidebar, bg=Colors.BG_SIDEBAR)
        add_btn_frame.pack(fill="x", padx=6, pady=8)

        add_folder_btn = tk.Button(
            add_btn_frame, text="＋ 添加文件夹", command=self.add_folder,
            font=(Colors.FONT_FAMILY, 11), bg="#334155",
            fg=Colors.TEXT_SIDEBAR_ACTIVE, activebackground="#475569",
            activeforeground="white", relief="flat", bd=0, padx=12, pady=8, cursor="hand2",
        )
        add_folder_btn.pack(fill="x")

    def _folder_has_subdirs(self, folder: str) -> bool:
        """检查文件夹是否包含子目录（优先使用缓存）"""
        entry = self._folder_struct_cache.get(folder)
        if entry is not None:
            return len(entry["subdirs"]) > 0
        # 缓存未命中时回退到文件系统扫描
        try:
            for d in os.scandir(folder):
                if d.is_dir():
                    return True
        except (OSError, PermissionError):
            pass
        return False

    def _collapse_all_to_top(self):
        """折叠所有文件夹并滚动到顶部（快速回到顶层）"""
        self._collapsed_folders.clear()
        for folder in self.folders:
            self._collect_collapsible(folder)
        self._render_folder_list()
        # 滚动到顶部
        self.folder_canvas.yview_moveto(0)

    def _auto_collapse_all(self):
        """启动时折叠所有有子目录的文件夹（递归）"""
        self._collapse_all_to_top()

    # ── 文件夹拖拽排序 ──────────────────────────────────────
    def _on_folder_drag_start(self, event):
        """记录拖拽起始位置，检测点击的顶层文件夹"""
        y_inner = event.y_root - self.folder_inner.winfo_rooty()
        folder, folder_idx = self._find_top_folder_at_y(y_inner)
        if folder is None:
            return
        self._folder_drag_info = {
            "folder": folder,
            "folder_idx": folder_idx,
            "start_x": event.x_root,
            "start_y": event.y_root,
            "drag_active": False,
            "indicator": None,
        }

    def _on_folder_drag_move(self, event):
        """拖拽移动：超过阈值后显示插入指示线"""
        info = self._folder_drag_info
        if info is None:
            return
        dx = event.x_root - info["start_x"]
        dy = event.y_root - info["start_y"]
        if not info["drag_active"] and abs(dx) < 5 and abs(dy) < 5:
            return
        info["drag_active"] = True
        self._update_drag_indicator(event)

    def _on_folder_drag_end(self, event):
        """释放鼠标：如果是拖拽则重排"""
        info = self._folder_drag_info
        if info is None:
            return
        if info["indicator"]:
            try:
                info["indicator"].destroy()
            except Exception:
                pass
        if info["drag_active"]:
            y_inner = event.y_root - self.folder_inner.winfo_rooty()
            _, target_idx = self._find_top_folder_at_y(y_inner)
            if target_idx is not None and target_idx != info["folder_idx"]:
                old_idx = info["folder_idx"]
                if target_idx > old_idx:
                    target_idx -= 1
                else:
                    target_idx = max(0, target_idx)
                folder = self.folders.pop(old_idx)
                self.folders.insert(target_idx, folder)
                self._render_folder_list()
                self._auto_collapse_all()
                self._auto_save()
        self._folder_drag_info = None

    def _update_drag_indicator(self, event):
        """在目标位置绘制插入指示线"""
        info = self._folder_drag_info
        if info is None:
            return
        y_inner = event.y_root - self.folder_inner.winfo_rooty()
        target_folder, target_idx = self._find_top_folder_at_y(y_inner)
        if info["indicator"]:
            try:
                info["indicator"].destroy()
            except Exception:
                pass
            info["indicator"] = None
        if target_folder is None:
            return
        target_frame = self._folder_widgets.get(target_folder)
        if target_frame is None:
            return
        try:
            item_y = target_frame.winfo_y()
            item_h = target_frame.winfo_height()
            if y_inner < item_y + item_h // 2:
                line_y = item_y
            else:
                line_y = item_y + item_h
            line = tk.Frame(self.folder_inner, bg=Colors.ACCENT, height=2)
            line.place(x=self._sp(10), y=line_y, width=self.folder_canvas.winfo_width() - self._sp(20), height=2)
            info["indicator"] = line
        except Exception:
            pass

    def _find_top_folder_at_y(self, y_inner: int) -> tuple[str | None, int | None]:
        """根据 folder_inner 内 Y 坐标找到最近的顶层文件夹及索引"""
        best_folder = None
        best_idx = None
        best_dist = float("inf")
        for idx, folder in enumerate(self.folders):
            frame = self._folder_widgets.get(folder)
            if frame is None:
                continue
            try:
                item_y = frame.winfo_y()
                item_h = frame.winfo_height()
                center_y = item_y + item_h // 2
                dist = abs(y_inner - center_y)
                if dist < best_dist:
                    best_dist = dist
                    best_folder = folder
                    best_idx = idx
            except Exception:
                continue
        return best_folder, best_idx

    def _collect_collapsible(self, folder: str):
        """递归收集所有有子目录的文件夹（默认折叠，优先使用缓存）"""
        entry = self._folder_struct_cache.get(folder)
        if entry is not None and entry["subdirs"]:
            self._collapsed_folders.add(folder)
            for subdir in entry["subdirs"]:
                self._collect_collapsible(subdir)
        elif entry is None:
            # 缓存未命中时的回退
            if self._folder_has_subdirs(folder):
                self._collapsed_folders.add(folder)
                try:
                    for d in os.scandir(folder):
                        if d.is_dir():
                            self._collect_collapsible(d.path)
                except (OSError, PermissionError):
                    pass

    def _toggle_folder_collapse(self, folder: str):
        """切换文件夹折叠/展开状态（增量更新，不重建整棵树）"""
        if folder in self._collapsed_folders:
            # 展开：创建或恢复直接子文件夹
            self._collapsed_folders.discard(folder)
            self._expand_folder(folder)
        else:
            # 折叠：隐藏所有子孙文件夹
            self._collapsed_folders.add(folder)
            self._collapse_descendants(folder)
        # 更新图标
        self._update_folder_item_icon(folder)
        # 更新计数
        self._update_folder_count()

    def _expand_folder(self, folder: str, indent: int = 0):
        """展开文件夹：创建或恢复直接子文件夹项"""
        entry = self._folder_struct_cache.get(folder)
        if not entry or not entry["subdirs"]:
            return
        parent_frame = self._folder_widgets.get(folder)
        if not parent_frame:
            return
        child_indent = indent + self._sp(20)
        # 找到父项在 pack 顺序中的位置，在其后插入子项
        for sub in entry["subdirs"]:
            if sub in self._folder_widgets:
                # 已缓存：恢复显示，更新缩进
                frame = self._folder_widgets[sub]
                self._update_item_indent(frame, child_indent)
                try:
                    frame.pack(after=parent_frame, fill="x", pady=1)
                except Exception:
                    frame.pack(fill="x", pady=1)
                parent_frame = frame
            else:
                # 首次创建
                frame = self._create_folder_item(sub, 0, child_indent)
                self._folder_widgets[sub] = frame
                try:
                    frame.pack(after=parent_frame, fill="x", pady=1)
                except Exception:
                    frame.pack(fill="x", pady=1)
                parent_frame = frame
            # 如果子文件夹已展开，递归恢复其子项
            if sub not in self._collapsed_folders:
                self._expand_folder(sub, child_indent)

    def _update_item_indent(self, frame: tk.Frame, indent: int):
        """更新文件夹项的缩进"""
        if not frame.winfo_children():
            return
        content = frame.winfo_children()[0]
        # 更新 content frame 的 padx
        try:
            content.pack_configure(padx=(2 + indent, 2))
        except Exception:
            pass

    def _collapse_descendants(self, folder: str):
        """隐藏文件夹的所有子孙项（保留控件缓存）"""
        entry = self._folder_struct_cache.get(folder)
        if not entry or not entry["subdirs"]:
            return
        for sub in entry["subdirs"]:
            if sub in self._folder_widgets:
                try:
                    self._folder_widgets[sub].pack_forget()
                except Exception:
                    pass
            # 递归隐藏更深层级
            self._collapse_descendants(sub)

    def _update_folder_item_icon(self, folder: str):
        """更新单个文件夹项的折叠图标"""
        frame = self._folder_widgets.get(folder)
        if not frame:
            return
        content = frame.winfo_children()[0] if frame.winfo_children() else None
        if content is None:
            return
        # 查找 icon label（第一个 pack 的 Label）
        for child in content.winfo_children():
            if isinstance(child, tk.Label) and child.cget("text") in ("▶", "▼", "📁"):
                if self._folder_has_subdirs(folder):
                    child.config(text="▶" if folder in self._collapsed_folders else "▼")
                break

    def _update_folder_count(self):
        """更新文件夹计数标签（遍历树计算可见项数）"""
        def _count_visible(folder: str) -> int:
            c = 1
            if folder in self._collapsed_folders:
                return c
            entry = self._folder_struct_cache.get(folder)
            if entry:
                for sub in entry["subdirs"]:
                    c += _count_visible(sub)
            return c

        total = sum(_count_visible(f) for f in self.folders)
        self.folder_count_label.config(text=f"({total})")

    def _render_folder_list(self):
        """完全重建侧边栏文件夹列表（添加/移除文件夹时使用）"""
        for w in self.folder_inner.winfo_children():
            w.destroy()
        self._folder_widgets.clear()

        total_items = 0
        if not self.folders:
            empty = tk.Label(
                self.folder_inner, text="暂无文件夹\n点击下方按钮添加",
                font=(Colors.FONT_FAMILY, 10), fg=Colors.TEXT_SIDEBAR,
                bg=Colors.BG_SIDEBAR, justify="center",
            )
            empty.pack(pady=30)
        else:
            for i, folder in enumerate(self.folders):
                total_items += self._render_folder_tree(folder, i, indent=0)

        self.folder_count_label.config(text=f"({total_items})")

    def _render_folder_tree(self, folder: str, index: int, indent: int) -> int:
        """递归渲染文件夹树（仅创建可见项），返回创建的项数"""
        is_collapsed = folder in self._collapsed_folders
        frame = self._create_folder_item(folder, index, indent)
        frame.pack(fill="x", pady=1)
        self._folder_widgets[folder] = frame
        count = 1
        if is_collapsed:
            return count
        entry = self._folder_struct_cache.get(folder)
        subdirs = entry["subdirs"] if entry is not None else []
        for sub in subdirs:
            count += self._render_folder_tree(sub, index + count, indent + self._sp(20))
        return count

    def _create_folder_item(self, folder: str, index: int, indent: int = 0):
        """创建单个文件夹项（含右键菜单）。indent 为缩进像素，用于子文件夹层级显示。"""
        name = os.path.basename(folder) or folder
        entry = self._folder_struct_cache.get(folder)
        count = entry["image_count"] if entry else 0

        item_frame = tk.Frame(self.folder_inner, bg=Colors.BG_SIDEBAR, cursor="hand2")
        # pack 由调用方负责（_render_folder_tree 或 _expand_folder）

        bg_color = "#334155" if index % 2 == 0 else Colors.BG_SIDEBAR
        content = tk.Frame(item_frame, bg=bg_color)
        content.pack(fill="both", expand=True, padx=(2 + indent, 2), pady=3)

        # 图标：有子目录的文件夹显示折叠箭头，否则显示 📁
        has_subdirs = self._folder_has_subdirs(folder)
        if has_subdirs:
            icon_text = "▶" if folder in self._collapsed_folders else "▼"
        else:
            icon_text = "📁"
        icon = tk.Label(content, text=icon_text, font=(Colors.FONT_FAMILY, 14),
                        bg=content["bg"], fg=Colors.TEXT_SIDEBAR)
        icon.pack(side="left", padx=(6, 2), pady=8)
        # 有子目录的文件夹图标点击切换折叠状态
        if has_subdirs:
            icon.config(cursor="hand2")
            icon.bind("<Button-1>", lambda e, f=folder: self._toggle_folder_collapse(f))

        name_label = tk.Label(content, text=name, font=(Colors.FONT_FAMILY, 11, "bold"),
                              fg=Colors.TEXT_SIDEBAR_ACTIVE, bg=content["bg"], anchor="w")
        name_label.pack(side="left", padx=(0, 2), pady=8, fill="x", expand=True)

        count_label = tk.Label(content, text=str(count), font=(Colors.FONT_FAMILY, 9),
                               fg=Colors.TEXT_SECONDARY, bg=content["bg"])
        count_label.pack(side="right", padx=(0, 2), pady=8)

        # 删除按钮（所有文件夹均可移除）
        del_btn = tk.Label(content, text="✕", font=(Colors.FONT_FAMILY, 10, "bold"),
                           fg=Colors.TEXT_SIDEBAR, bg=content["bg"], cursor="hand2")
        del_btn.pack(side="right", padx=(0, 1), pady=8)
        del_btn.bind("<Button-1>", lambda e, f=folder: self.remove_folder(f))

        # ── 事件绑定（必须在所有子控件创建之后） ──
        # content 点击 → 筛选
        content.bind("<Button-1>", lambda e, f=folder: self._filter_by_folder(f))
        # 子控件：name_label、count_label 点击 → 筛选
        name_label.bind("<Button-1>", lambda e, f=folder: self._filter_by_folder(f))
        count_label.bind("<Button-1>", lambda e, f=folder: self._filter_by_folder(f))
        # 无子目录的文件夹图标也可点击筛选（有子目录的已有折叠绑定）
        if not has_subdirs:
            icon.bind("<Button-1>", lambda e, f=folder: self._filter_by_folder(f))

        # 拖拽排序绑定（顶层文件夹可拖拽重排）
        if indent == 0:
            for w in (item_frame, content, icon, name_label, count_label):
                w.bind("<ButtonPress-1>", self._on_folder_drag_start, add="+")
                w.bind("<B1-Motion>", self._on_folder_drag_move, add="+")
                w.bind("<ButtonRelease-1>", self._on_folder_drag_end, add="+")

        # ── 右键菜单 ──
        self._bind_context_menu(content, folder)
        self._bind_context_menu(icon, folder)
        self._bind_context_menu(name_label, folder)
        self._bind_context_menu(count_label, folder)

        return item_frame

    def _bind_context_menu(self, widget: tk.Widget, folder: str):
        """绑定右键菜单到控件"""
        widget.bind(
            "<Button-3>" if os.name == "nt" else "<Button-2>",
            lambda e, f=folder: self._show_folder_context_menu(e, f),
        )

    def _show_folder_context_menu(self, event, folder: str):
        """显示文件夹右键菜单"""
        menu = tk.Menu(self.root, tearoff=0, font=(Colors.FONT_FAMILY, 10),
                       bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                       activebackground=Colors.ACCENT, activeforeground="white")

        menu.add_command(
            label="📁 新建文件夹",
            command=lambda f=folder: self._create_new_folder(f),
        )
        menu.add_command(
            label="📤 上传图片到此文件夹",
            command=lambda f=folder: self._upload_to_folder(f),
        )
        menu.add_command(
            label="📂 在资源管理器中打开",
            command=lambda f=folder: self._open_folder_in_explorer(f),
        )
        menu.add_separator()
        menu.add_command(
            label="✕ 移除文件夹",
            command=lambda f=folder: self.remove_folder(f),
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_folder_in_explorer(self, folder: str):
        """在资源管理器中打开文件夹"""
        if os.name == "nt":
            os.startfile(folder)
        else:
            import subprocess
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def _create_new_folder(self, parent_folder: str):
        """在指定文件夹下新建子文件夹"""
        name = simpledialog.askstring("新建文件夹", "请输入文件夹名称：", parent=self.root)
        if not name or not name.strip():
            return
        try:
            new_path = os.path.join(parent_folder, name.strip())
            os.makedirs(new_path, exist_ok=True)
            self._render_folder_list()
            self.status_label.config(text=f"已创建文件夹：{name.strip()}")
        except Exception as e:
            messagebox.showerror("创建失败", f"无法创建文件夹：\n{e}")

    def _toggle_sidebar(self):
        """折叠 / 展开左侧边栏"""
        if self.sidebar_collapsed:
            self.collapsed_bar.grid_remove()
            self.sidebar.grid(row=0, column=0, sticky="ns")
            self.sidebar.config(width=self._sp(240))
            self.sidebar_collapsed = False
        else:
            self.sidebar.grid_remove()
            self.collapsed_bar.grid(row=0, column=0, sticky="ns")
            self.sidebar_collapsed = True
            self.root.after(200, self._render_thumbnails)
        self._auto_save()

    # ── 预览面板 ──────────────────────────────────────────
    def _build_preview_panel(self):
        preview_header = tk.Frame(self.preview_panel, bg=Colors.BG_CARD)
        preview_header.pack(fill="x", padx=16, pady=(16, 0))

        tk.Label(
            preview_header, text="🖼️ 预览",
            font=(Colors.FONT_FAMILY, 12, "bold"),
            fg=Colors.TEXT_PRIMARY, bg=Colors.BG_CARD,
        ).pack(side="left")

        preview_img_frame = tk.Frame(self.preview_panel, bg=Colors.BG_INPUT)
        preview_img_frame.pack(fill="x", padx=16, pady=12)
        preview_img_frame.config(height=self._sp(240))
        preview_img_frame.pack_propagate(False)

        self.preview_label = tk.Label(
            preview_img_frame, text="选择一张图片\n进行预览",
            font=(Colors.FONT_FAMILY, 11), fg=Colors.TEXT_SECONDARY,
            bg=Colors.BG_INPUT, justify="center",
        )
        self.preview_label.pack(expand=True, fill="both")

        # 文件名编辑区
        info_frame = tk.Frame(self.preview_panel, bg=Colors.BG_CARD)
        info_frame.pack(fill="x", padx=16, pady=8)

        tk.Label(
            info_frame, text="文件名", font=(Colors.FONT_FAMILY, 10, "bold"),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_CARD,
        ).pack(anchor="w")

        self.name_var = tk.StringVar()
        # 文件名行：输入框（仅文件名） + 后缀名标签（不可编辑）
        name_row = tk.Frame(info_frame, bg=Colors.BG_CARD)
        name_row.pack(fill="x", pady=(4, 0))

        name_entry = tk.Entry(
            name_row, textvariable=self.name_var,
            font=(Colors.FONT_FAMILY, 12), bg=Colors.BG_INPUT,
            fg=Colors.TEXT_PRIMARY, insertbackground=Colors.TEXT_PRIMARY,
            relief="flat", bd=8,
        )
        name_entry.pack(side="left", fill="x", expand=True)
        self.name_entry = name_entry

        self.ext_label = tk.Label(
            name_row, text="",
            font=(Colors.FONT_FAMILY, 12, "bold"),
            fg=Colors.ACCENT, bg=Colors.BG_CARD,
        )
        self.ext_label.pack(side="left", padx=(6, 0))

        self.path_var = tk.StringVar(value="")
        path_label = tk.Label(
            info_frame, textvariable=self.path_var,
            font=(Colors.FONT_FAMILY, 9), fg=Colors.TEXT_SECONDARY,
            bg=Colors.BG_CARD, anchor="w", wraplength=260,
        )
        path_label.pack(fill="x", pady=(4, 0))

        # 操作按钮
        btn_frame = tk.Frame(self.preview_panel, bg=Colors.BG_CARD)
        btn_frame.pack(fill="x", padx=16, pady=12)

        save_btn = tk.Button(
            btn_frame, text="💾 保存名称", command=self.rename_image,
            font=(Colors.FONT_FAMILY, 11), bg=Colors.SUCCESS, fg="white",
            activebackground="#059669", activeforeground="white",
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
        )
        save_btn.pack(fill="x", pady=(0, 6))

        open_folder_btn = tk.Button(
            btn_frame, text="📂 打开所在文件夹", command=self.open_file_location,
            font=(Colors.FONT_FAMILY, 11), bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER, activeforeground=Colors.TEXT_PRIMARY,
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
        )
        open_folder_btn.pack(fill="x", pady=(0, 6))

        copy_btn = tk.Button(
            btn_frame, text="📋 复制路径", command=self.copy_path,
            font=(Colors.FONT_FAMILY, 11), bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER, activeforeground=Colors.TEXT_PRIMARY,
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
        )
        copy_btn.pack(fill="x")

        self.info_text = tk.Label(
            self.preview_panel, text="", font=(Colors.FONT_FAMILY, 9),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_CARD, justify="left",
        )
        self.info_text.pack(fill="x", padx=16, pady=8)

    # ── 状态栏 ────────────────────────────────────────────
    def _build_statusbar(self):
        status = tk.Frame(self.root, bg=Colors.BG_TOOLBAR, height=self._sp(32))
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)

        border = tk.Frame(status, bg=Colors.BORDER, height=1)
        border.pack(fill="x", side="top")

        inner = tk.Frame(status, bg=Colors.BG_TOOLBAR)
        inner.pack(fill="both", expand=True, padx=16)

        self.status_label = tk.Label(
            inner, text="就绪", font=(Colors.FONT_FAMILY, 10),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR,
        )
        self.status_label.pack(side="left")

        self.image_count_label = tk.Label(
            inner, text="图片: 0", font=(Colors.FONT_FAMILY, 10),
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_TOOLBAR,
        )
        self.image_count_label.pack(side="right")

    # ── 核心功能 ──────────────────────────────────────────
    def add_folder(self):
        """添加文件夹"""
        folder = filedialog.askdirectory(title="选择包含图片的文件夹")
        if not folder:
            return
        folder = os.path.abspath(folder)
        if folder in self.folders:
            messagebox.showinfo("提示", f"文件夹已存在：{folder}")
            return
        self.folders.append(folder)
        self._render_folder_list()
        self._current_folder_filter = None
        self.refresh_all()
        self.status_label.config(text=f"已添加文件夹：{folder}")
        self._auto_save()

    def remove_folder(self, folder: str):
        """移除文件夹：顶层文件夹从监控列表移除，子文件夹从磁盘删除"""
        if folder in self.folders:
            # 顶层文件夹：从监控列表移除（不删磁盘文件）
            self.folders.remove(folder)
            self._current_folder_filter = None
            self._render_folder_list()
            self.refresh_all()
            self.status_label.config(text=f"已移除文件夹：{folder}")
            self._auto_save()
        elif os.path.isdir(folder):
            # 子文件夹：从磁盘删除
            if not messagebox.askyesno("确认删除", f"确定要删除文件夹及其所有内容？\n\n{folder}"):
                return
            try:
                shutil.rmtree(folder)
                self._current_folder_filter = None
                self._render_folder_list()
                self.refresh_all()
                self.status_label.config(text=f"已删除文件夹：{os.path.basename(folder)}")
            except Exception as e:
                messagebox.showerror("删除失败", f"无法删除文件夹：\n{e}")

    def _upload_to_folder(self, target_folder: str):
        """右键菜单：直接上传到指定文件夹"""
        files = filedialog.askopenfilenames(
            title="选择要上传的图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.gif *.bmp *.webp *.tiff *.tif"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return
        self._copy_files_to_target(files, target_folder)

    def upload_images(self):
        """上传图片——选择文件后直接复制到预设的默认路径"""
        # 检查是否已设置上传路径
        if not self._default_upload_target or not os.path.isdir(self._default_upload_target):
            # 首次使用，引导用户选择路径
            messagebox.showinfo("设置上传路径", "请先设置图片上传的目标文件夹。")
            self._choose_upload_target()
            if not self._default_upload_target:
                return

        files = filedialog.askopenfilenames(
            title="选择要上传的图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.gif *.bmp *.webp *.tiff *.tif"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return

        self._copy_files_to_target(files, self._default_upload_target)

    def _set_upload_target(self, folder: str):
        """将指定文件夹设为上传目标路径"""
        folder = os.path.abspath(folder)
        self._default_upload_target = folder
        self._update_upload_target_display()
        self._auto_save()
        self.status_label.config(text=f"上传路径已设为：{folder}")

    def _choose_upload_target(self):
        """选择上传目标文件夹（直接使用系统文件夹选择对话框）"""
        folder = filedialog.askdirectory(
            title="选择图片上传的目标文件夹",
            initialdir=self._default_upload_target if self._default_upload_target else None,
        )
        if not folder:
            return
        folder = os.path.abspath(folder)
        self._default_upload_target = folder
        self._update_upload_target_display()
        self._auto_save()
        self.status_label.config(text=f"上传路径已设为：{folder}")

    def _update_upload_target_display(self):
        """更新上传路径显示"""
        path = self._default_upload_target
        if not path:
            self.upload_target_var.set("点击设置上传路径...")
            return
        self.upload_target_var.set(path)

    def _copy_files_to_target(self, files: list[str], target: str):
        """将文件复制到目标文件夹"""
        target = os.path.abspath(target)
        os.makedirs(target, exist_ok=True)

        copied = 0
        for src in files:
            dst_name = os.path.basename(src)
            dst = os.path.join(target, dst_name)

            # 重名自动加序号
            base, ext = os.path.splitext(dst_name)
            counter = 1
            while os.path.exists(dst):
                dst = os.path.join(target, f"{base}_{counter}{ext}")
                counter += 1

            try:
                shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                messagebox.showerror("上传失败", f"无法复制文件：{src}\n错误：{e}")

        if copied > 0:
            # 自动将上传目标文件夹添加到监控列表（仅当不是已有文件夹的子目录时）
            def _is_subdir(parent: str, child: str) -> bool:
                try:
                    return os.path.commonpath([parent, child]) == parent and child != parent
                except ValueError:
                    return False  # 不同驱动器
            is_subdir = any(_is_subdir(f, target) for f in self.folders)
            if target not in self.folders and not is_subdir:
                self.folders.append(target)
                self._render_folder_list()
                self._auto_save()
            self.refresh_all(folder_filter=self._current_folder_filter)
            self.status_label.config(text=f"成功上传 {copied} 张图片到：{target}")
        else:
            self.status_label.config(text="上传取消")

    # ── 拖拽上传 ────────────────────────────────────────────
    def _setup_drag_drop(self):
        """初始化拖拽上传支持"""
        if not _DND_AVAILABLE:
            return
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self._on_drop)
        self.root.dnd_bind('<<DragEnter>>', self._on_drag_enter)
        self.root.dnd_bind('<<DragLeave>>', self._on_drag_leave)

    def _on_drag_enter(self, event):
        """拖拽进入窗口——显示上传提示遮罩"""
        if self._dnd_overlay is not None:
            return
        self._dnd_overlay = tk.Frame(self.root, bg=Colors.ACCENT)
        self._dnd_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        # 降低透明度效果通过子标签实现
        inner = tk.Frame(self._dnd_overlay, bg=Colors.ACCENT)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            inner, text="📥", font=(Colors.FONT_FAMILY, 48),
            fg="white", bg=Colors.ACCENT,
        ).pack()
        tk.Label(
            inner, text="释放以上传图片",
            font=(Colors.FONT_FAMILY, 18, "bold"),
            fg="white", bg=Colors.ACCENT,
        ).pack(pady=(8, 0))
        self._dnd_overlay.lift()

    def _on_drag_leave(self, event):
        """拖拽离开窗口——隐藏遮罩"""
        if self._dnd_overlay is not None:
            self._dnd_overlay.destroy()
            self._dnd_overlay = None

    def _on_drop(self, event):
        """处理拖拽释放的文件"""
        # 先隐藏遮罩
        self._on_drag_leave(event)

        files = self._parse_drop_files(event.data)
        image_files = [f for f in files if is_image_file(f)]
        if not image_files:
            return

        # 确保上传路径已设置
        if not self._default_upload_target or not os.path.isdir(self._default_upload_target):
            messagebox.showinfo("设置上传路径", "请先设置图片上传的目标文件夹。")
            self._choose_upload_target()
            if not self._default_upload_target:
                return

        self._copy_files_to_target(image_files, self._default_upload_target)

    @staticmethod
    def _parse_drop_files(data: str) -> list[str]:
        """解析拖拽的文件路径（Windows 格式）"""
        import re
        # Windows 返回 "{path1} {path2}" 格式（路径含空格时用花括号包裹）
        files = re.findall(r'\{(.*?)\}|(\S+)', data)
        result = []
        for f in files:
            path = f[0] or f[1]
            if path:
                result.append(path)
        return result

    def _refresh_clear_filter(self):
        """刷新按钮：保持当前文件夹筛选"""
        self.refresh_all(folder_filter=self._current_folder_filter)

    def refresh_all(self, folder_filter: str | None = None):
        """刷新所有图片列表和缩略图（可选保持文件夹筛选）"""
        self.all_images, self._folder_struct_cache = get_image_files_and_struct(self.folders)
        self.thumbnail_loader.clear()
        self._render_thumbnails(folder_filter=folder_filter)
        self._update_status()
        self._update_preview_panel()
        # 同时刷新文件夹列表以更新图片计数
        self._render_folder_list()

    def _filter_by_folder(self, folder: str):
        """按文件夹筛选显示（再次点击同一文件夹则取消筛选）"""
        if self._search_after_id:
            self.root.after_cancel(self._search_after_id)
            self._search_after_id = None
        self.search_var1.set("")
        self.search_var2.set("")
        # 切换：同一文件夹再次点击 → 取消筛选，显示全部
        if self._current_folder_filter == folder:
            self._current_folder_filter = None
            self._render_thumbnails()
            self.status_label.config(text="显示全部图片")
        else:
            self._current_folder_filter = folder
            self._render_thumbnails(folder_filter=folder)
            self.status_label.config(text=f"筛选文件夹：{os.path.basename(folder)}")
            # 同步上传路径到当前筛选的文件夹
            self._default_upload_target = os.path.abspath(folder)
            self._update_upload_target_display()
            self._auto_save()

    def _on_search(self):
        """搜索过滤（带防抖）"""
        if self._search_after_id:
            self.root.after_cancel(self._search_after_id)
        self._current_folder_filter = None  # 用户手动搜索时清除文件夹筛选
        self._search_after_id = self.root.after(250, self._render_thumbnails)

    def _on_thumbnail_container_resize(self, event=None):
        """缩略图容器尺寸变化时，防抖重排缩略图网格"""
        if not self._displayed_images:
            return
        new_width = self.thumbnail_area.canvas.winfo_width()
        if new_width < self._sp(100):
            return
        new_height = self.thumbnail_area.winfo_height()
        # 宽度变化超过 30px 或高度变化超过 80px 才算有效变化（按 DPI 缩放）
        width_changed = abs(new_width - self._last_thumb_container_width) >= self._sp(30)
        height_changed = hasattr(self, '_last_thumb_container_height') and \
            abs((new_height or 0) - self._last_thumb_container_height) >= self._sp(80)
        if not width_changed and not height_changed:
            return
        self._last_thumb_container_width = new_width
        self._last_thumb_container_height = new_height or 0
        # 防抖 200ms
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(200, self._render_thumbnails)

    def _on_scroll_sync(self):
        """滚动时同步可见卡片（由 ScrollableFrame 回调触发）"""
        self._sync_cards()

    # ── 虚拟滚动网格渲染 ───────────────────────────────────

    def _get_filtered_images(self, folder_filter: str | None = None) -> list[str]:
        """根据搜索/筛选条件过滤图片（仅操作字符串，不创建控件）"""
        def _is_real_query(sv: tk.StringVar) -> bool:
            """判断搜索框是否有实际输入（排除 placeholder）"""
            val = sv.get().strip()
            return bool(val) and val not in ("文件名...", "路径...")

        query1 = self.search_var1.get().strip() if _is_real_query(self.search_var1) else ""
        query2 = self.search_var2.get().strip() if _is_real_query(self.search_var2) else ""
        images = self.all_images

        if folder_filter:
            images = [img for img in images if os.path.dirname(img) == folder_filter]

        if query1 or query2:
            def matches(img_path: str) -> bool:
                basename_lower = os.path.basename(img_path).lower()
                path_lower = img_path.lower()
                if query1 and query1.lower() not in basename_lower and query1.lower() not in path_lower:
                    return False
                if query2 and query2.lower() not in basename_lower and query2.lower() not in path_lower:
                    return False
                return True
            images = [img for img in images if matches(img)]

        return images

    def _render_thumbnails(self, folder_filter: str | None = None):
        """虚拟滚动：只创建视口内可见的卡片，其余仅在需要时渲染"""
        images = self._get_filtered_images(folder_filter)

        # 清除旧卡片控件（保留 empty_label 除外）
        for slot in self._card_slots:
            try:
                slot["frame"].destroy()
            except Exception:
                pass
        self._card_slots.clear()
        # 同时清理 inner 内其他残留控件（但不影响 empty_label）
        for w in self.thumbnail_area.inner.winfo_children():
            if w is not getattr(self, 'empty_label', None):
                try:
                    w.destroy()
                except Exception:
                    pass

        if not images:
            has_filter = bool(self.search_var1.get().strip() or self.search_var2.get().strip() or folder_filter)
            # 先销毁旧的 empty_label，避免 pack/place 管理器冲突导致标签残留
            if hasattr(self, 'empty_label') and self.empty_label:
                try:
                    self.empty_label.destroy()
                except Exception:
                    pass
            self.empty_label = tk.Label(
                self.thumbnail_area.inner,
                text="📂\n\n没有找到图片" if has_filter else "📂\n\n添加文件夹或上传图片开始浏览",
                font=(Colors.FONT_FAMILY, 14), fg=Colors.TEXT_SECONDARY,
                bg=Colors.BG_MAIN, justify="center",
            )
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self.image_count_label.config(text="图片: 0")
            self.status_label.config(text="就绪")
            self._displayed_images = []
            self._last_thumb_container_width = self.thumbnail_area.canvas.winfo_width()
            # 重置 canvas 滚动区域
            self.thumbnail_area.canvas.configure(scrollregion=(0, 0, 1, 1))
            return

        # 隐藏 empty_label
        if hasattr(self, 'empty_label') and self.empty_label:
            self.empty_label.place_forget()

        container_width = self.thumbnail_area.canvas.winfo_width()
        if container_width < self._sp(100):
            container_width = self._sp(700)
        self._last_thumb_container_width = container_width

        cols = max(1, (container_width - self._sp(20)) // (self.CARD_WIDTH + self.CARD_PAD))
        total_rows = max(1, (len(images) + cols - 1) // cols)
        canvas_height = total_rows * (self.CARD_HEIGHT + self.CARD_PAD) + self.CARD_PAD

        # 设置 inner frame 高度（place() 不会自动扩展父容器）
        self.thumbnail_area.inner.configure(height=canvas_height)
        # 设置 Canvas 滚动区域（模拟全部图片的高度）
        self.thumbnail_area.canvas.configure(scrollregion=(0, 0, container_width, canvas_height))

        # 计算需要的卡片槽位数
        viewport_h = self.thumbnail_area.canvas.winfo_height()
        if viewport_h < self._sp(100):
            # 回退：使用父容器高度或主窗口高度估算
            parent_h = self.thumbnail_area.winfo_height()
            if parent_h and parent_h > self._sp(100):
                viewport_h = parent_h
            else:
                viewport_h = max(self._sp(600), self.root.winfo_height() - self._sp(120))
        visible_rows = viewport_h // (self.CARD_HEIGHT + self.CARD_PAD) + 3  # +3 行缓冲
        total_slots = min(visible_rows * cols, len(images))

        # 保存状态
        self._displayed_images = images
        self._card_cols = cols

        # 创建卡片槽位
        self._ensure_card_slots(total_slots)

        # 首次同步
        self.thumbnail_area.canvas.yview_moveto(0)
        self._sync_cards()

        self.image_count_label.config(text=f"图片: {len(images)}")
        self._update_status()

    def _ensure_card_slots(self, count: int):
        """创建或回收卡片槽位（不够就新建，多了就销毁）"""
        inner = self.thumbnail_area.inner

        # 销毁多余的槽位
        while len(self._card_slots) > count:
            slot = self._card_slots.pop()
            try:
                slot["frame"].destroy()
            except Exception:
                pass

        # 创建不足的槽位
        while len(self._card_slots) < count:
            card = tk.Frame(
                inner, bg=Colors.BG_CARD, bd=0, highlightthickness=0,
                width=self.CARD_WIDTH, height=self.CARD_HEIGHT,
                cursor="hand2",
            )
            card.pack_propagate(False)

            # 图片标签
            img_lbl = tk.Label(card, text="⏳", font=(Colors.FONT_FAMILY, 22),
                               fg=Colors.TEXT_SECONDARY, bg=Colors.BG_CARD)

            # 文件名标签
            name_lbl = tk.Label(card, text="", font=(Colors.FONT_FAMILY, 9),
                                fg=Colors.TEXT_PRIMARY, bg=Colors.BG_CARD,
                                anchor="center", wraplength=self.THUMB_SIZE - 8, height=2)

            self._card_slots.append({
                "frame": card,
                "img_lbl": img_lbl,
                "name_lbl": name_lbl,
                "current_path": None,
            })

    def _sync_cards(self):
        """根据当前滚动位置，将卡片槽位绑定到对应图片"""
        if not self._displayed_images:
            return

        images = self._displayed_images
        cols = self._card_cols
        canvas = self.thumbnail_area.canvas

        first_visible_y = int(canvas.canvasy(0))
        first_row = max(0, first_visible_y // (self.CARD_HEIGHT + self.CARD_PAD))
        start_idx = first_row * cols

        # 收集新进入视口的图片路径（用于后台预加载）
        new_paths = []

        for i, slot in enumerate(self._card_slots):
            global_idx = start_idx + i
            if global_idx >= len(images):
                slot["frame"].place_forget()
                continue

            img_path = images[global_idx]
            row = global_idx // cols
            col = global_idx % cols

            x = col * (self.CARD_WIDTH + self.CARD_PAD) + self.CARD_PAD // 2
            y = row * (self.CARD_HEIGHT + self.CARD_PAD) + self.CARD_PAD // 2

            slot["frame"].place(x=x, y=y)

            # 如果图片变了，更新内容和绑定
            if slot["current_path"] != img_path:
                slot["current_path"] = img_path
                self._update_card_content(slot, img_path)
                new_paths.append(img_path)

        # 后台预加载新出现的图片
        if new_paths:
            self.thumbnail_loader.preload_batch(new_paths[:24])  # 限制批量数

    def _update_card_content(self, slot: dict, img_path: str):
        """更新单个卡片的内容（图片 + 文件名 + 事件绑定）"""
        card = slot["frame"]
        img_lbl = slot["img_lbl"]
        name_lbl = slot["name_lbl"]

        # 文件名（不显示后缀）
        name = os.path.splitext(os.path.basename(img_path))[0]
        display_name = name if len(name) <= 18 else name[:15] + "..."
        name_lbl.config(text=display_name)

        # 尝试从缓存获取缩略图
        photo = self.thumbnail_loader.get(img_path)
        if photo:
            img_lbl.config(image=photo, text="")
            img_lbl.image = photo
        else:
            img_lbl.config(image="", text="⏳")
            img_lbl.image = None
            self.thumbnail_loader.enqueue(img_path)

        # 布局：图片在上，文件名在下
        img_lbl.place(relx=0.5, rely=0.40, anchor="center")
        name_lbl.place(relx=0.5, rely=0.88, anchor="center", width=self.THUMB_SIZE - 4)

        # 清除旧绑定并重新绑定
        self._rebind_card_events(card, img_lbl, name_lbl, img_path)

    def _rebind_card_events(self, card: tk.Frame, img_lbl: tk.Label,
                            name_lbl: tk.Label, img_path: str):
        """为卡片控件重新绑定鼠标事件"""
        # 移除旧的绑定：使用 bind 替换的方式
        for widget in (card, img_lbl, name_lbl):
            widget.unbind("<Button-1>")
            widget.unbind("<Double-Button-1>")
            widget.unbind("<Enter>")
            widget.unbind("<Leave>")

        # 单击选中
        card.bind("<Button-1>", lambda e, p=img_path: self._on_image_select(p))
        img_lbl.bind("<Button-1>", lambda e, p=img_path: self._on_image_select(p))
        name_lbl.bind("<Button-1>", lambda e, p=img_path: self._on_image_select(p))

        # 双击打开
        card.bind("<Double-Button-1>", lambda e, p=img_path: self._open_image_viewer(p))
        img_lbl.bind("<Double-Button-1>", lambda e, p=img_path: self._open_image_viewer(p))
        name_lbl.bind("<Double-Button-1>", lambda e, p=img_path: self._open_image_viewer(p))

        # 悬停高亮
        def on_enter(event, c=card, p=img_path):
            if self._selected_path != p:
                c.config(bg=Colors.BG_SELECTED)
                for ch in c.winfo_children():
                    try:
                        ch.config(bg=Colors.BG_SELECTED)
                    except Exception:
                        pass

        def on_leave(event, c=card, p=img_path):
            if self._selected_path != p:
                c.config(bg=Colors.BG_CARD)
                for ch in c.winfo_children():
                    try:
                        ch.config(bg=Colors.BG_CARD)
                    except Exception:
                        pass

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)
        img_lbl.bind("<Enter>", on_enter)
        img_lbl.bind("<Leave>", on_leave)
        name_lbl.bind("<Enter>", on_enter)
        name_lbl.bind("<Leave>", on_leave)

    def _on_thumbnail_loaded(self, img_path: str, photo: tk.PhotoImage):
        """后台缩略图加载完成的回调 — 更新对应卡片（如果可见）"""
        for slot in self._card_slots:
            if slot["current_path"] == img_path and slot["frame"].winfo_exists():
                slot["img_lbl"].config(image=photo, text="")
                slot["img_lbl"].image = photo
                # 更新选中状态
                if self._selected_path == img_path:
                    slot["frame"].config(bg=Colors.BG_SELECTED)
                    for ch in slot["frame"].winfo_children():
                        try:
                            ch.config(bg=Colors.BG_SELECTED)
                        except Exception:
                            pass
                break

    def _open_image_viewer(self, img_path: str):
        """双击使用系统默认程序打开图片"""
        if os.name == "nt":
            os.startfile(img_path)
        else:
            import subprocess
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", img_path])

    def _on_image_select(self, img_path: str):
        """选中某张图片"""
        self.current_image = img_path
        self._selected_path = img_path
        self._update_preview_panel()

        # 更新所有可见卡片的选中/非选中样式
        for slot in self._card_slots:
            if not slot["frame"].winfo_exists():
                continue
            if slot["current_path"] == img_path:
                slot["frame"].config(bg=Colors.BG_SELECTED)
                for ch in slot["frame"].winfo_children():
                    try:
                        ch.config(bg=Colors.BG_SELECTED)
                    except Exception:
                        pass
            else:
                slot["frame"].config(bg=Colors.BG_CARD)
                for ch in slot["frame"].winfo_children():
                    try:
                        ch.config(bg=Colors.BG_CARD)
                    except Exception:
                        pass

    def _update_preview_panel(self):
        """更新右侧预览面板"""
        if not self.current_image or not os.path.exists(self.current_image):
            self.preview_label.config(image="", text="选择一张图片\n进行预览")
            self.name_var.set("")
            self.ext_label.config(text="")
            self.path_var.set("")
            self.info_text.config(text="")
            return

        img_path = self.current_image

        try:
            preview_img = Image.open(img_path)
            preview_img = preview_img.convert("RGB")
            max_w, max_h = self._sp(280), self._sp(240)
            w, h = preview_img.size
            ratio = min(max_w / w, max_h / h)
            new_w, new_h = int(w * ratio), int(h * ratio)
            preview_img = preview_img.resize((new_w, new_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(preview_img)
            self.preview_label.config(image=photo, text="", bg=Colors.BG_CARD)
            self.preview_label.image = photo
        except Exception as e:
            self.preview_label.config(image="", text=f"无法加载预览\n{str(e)[:50]}")

        stem, ext = os.path.splitext(os.path.basename(img_path))
        self.name_var.set(stem)
        self.ext_label.config(text=ext)
        self.path_var.set(img_path)

        try:
            img = Image.open(img_path)
            size_mb = os.path.getsize(img_path) / (1024 * 1024)
            info = f"尺寸: {img.size[0]}×{img.size[1]}\n大小: {size_mb:.2f} MB\n格式: {img.format or '未知'}"
            self.info_text.config(text=info)
        except Exception:
            self.info_text.config(text="")

    def rename_image(self):
        """重命名当前选中的图片（后缀名不可修改）"""
        if not self.current_image:
            messagebox.showwarning("提示", "请先选择一张图片")
            return

        new_stem = self.name_var.get().strip()
        if not new_stem:
            messagebox.showwarning("提示", "文件名不能为空")
            return

        old_path = self.current_image
        old_dir = os.path.dirname(old_path)
        old_ext = os.path.splitext(old_path)[1]
        new_name = new_stem + old_ext

        new_path = os.path.join(old_dir, new_name)

        if old_path == new_path:
            return

        if os.path.exists(new_path):
            messagebox.showwarning("提示", f"文件名已存在：{new_name}")
            return

        try:
            os.rename(old_path, new_path)
            # 更新缩略图缓存键名
            photo = self.thumbnail_loader.get(old_path)
            if photo is not None:
                with self.thumbnail_loader._lock:
                    self.thumbnail_loader.cache.pop(old_path, None)
                    self.thumbnail_loader.cache[new_path] = photo
            self.current_image = new_path
            self.all_images = [new_path if p == old_path else p for p in self.all_images]
            self._render_thumbnails(folder_filter=self._current_folder_filter)
            self._update_preview_panel()
            self.status_label.config(text=f"已重命名为：{new_name}")
        except Exception as e:
            messagebox.showerror("重命名失败", f"无法重命名文件：\n{e}")

    def open_file_location(self):
        """在资源管理器中打开文件所在位置"""
        if not self.current_image:
            messagebox.showwarning("提示", "请先选择一张图片")
            return
        folder = os.path.dirname(self.current_image)
        if os.name == "nt":
            os.startfile(folder)
        else:
            import subprocess
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def copy_path(self):
        """复制文件路径到剪贴板"""
        if not self.current_image:
            messagebox.showwarning("提示", "请先选择一张图片")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.current_image)
        self.root.update()
        self.status_label.config(text="已复制路径到剪贴板")

    def _update_status(self):
        """更新状态栏"""
        total = len(self.all_images)
        folder_count = len(self.folders)
        self.status_label.config(text=f"共 {total} 张图片，{folder_count} 个文件夹")
        self.image_count_label.config(text=f"图片: {total}")

    def _load_initial_state(self):
        """加载初始状态——优先使用持久化配置，否则扫描默认目录"""
        # 确保上传路径有默认值
        if not self._default_upload_target:
            pictures = os.path.join(os.path.expanduser("~"), "Pictures")
            if os.path.isdir(pictures):
                self._default_upload_target = pictures
            else:
                self._default_upload_target = os.path.expanduser("~")
        self._update_upload_target_display()

        if self.folders:
            # 已有持久化文件夹，直接加载
            self._render_folder_list()
            self.refresh_all()
            # 应用侧边栏折叠状态
            if self.sidebar_collapsed:
                self.sidebar_collapsed = False  # 先重置
                self._toggle_sidebar()
            # 启动时默认折叠所有文件夹
            self._auto_collapse_all()
            return

        # 没有持久化配置时，扫描默认目录
        default_dirs = [
            os.path.join(os.path.expanduser("~"), "Pictures"),
            os.path.join(os.path.expanduser("~"), "Desktop"),
        ]
        for d in default_dirs:
            if os.path.isdir(d) and d not in self.folders:
                imgs = [f for f in os.listdir(d) if is_image_file(os.path.join(d, f))]
                if imgs:
                    self.folders.append(d)
                    break

        self._render_folder_list()
        self.refresh_all()
        self._auto_collapse_all()


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


# ─── 入口 ──────────────────────────────────────────────────
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
