# FWD Image Search

现代化 GUI 图片浏览与管理工具，基于 Python Tkinter 构建。

## 功能特性

- 📤 **上传图片**：支持本地图片上传到指定目标文件夹（持久化记住选择）
- 📁 **多文件夹管理**：选择多个文件夹，集中浏览和管理图片
- 🖼️ **缩略图网格**：虚拟滚动网格视图，高效展示大量图片
- 🔍 **搜索筛选**：按文件名或路径搜索
- ✏️ **名称编辑**：支持直接重命名图片文件
- 🔍 **原图查看器**：双击打开，支持滚轮缩放 + 拖拽平移
- 📂 **文件夹树**：侧边栏显示文件夹层级结构，支持折叠/展开、拖拽排序
- 🎨 **现代化 UI**：扁平化设计，支持高 DPI 缩放
- 💾 **状态持久化**：文件夹列表、窗口状态自动保存

## 依赖

```
pip install Pillow tkinterdnd2
```

## 运行

```bash
python image_viewer.py
```

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller ImageManager.spec
```

输出文件位于 `dist/FWD Image Search.exe`
