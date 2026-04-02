# QZone Photo Downloader

一个自动下载 QQ 空间相册照片的 Python 脚本。支持多线程下载、EXIF 元信息写回，并可根据白名单/黑名单过滤相册。

## 功能

- 自动获取目标 QQ 号所有相册
- 下载相册中所有照片（含原图/RAW/原始URL策略）
- 支持多线程提高下载速度
- 支持将 EXIF 信息写回已下载照片（使用 `piexif`）
- 支持白名单/黑名单相册过滤
- 提供异常和日志输出（使用 `logging` + `coloredlogs`）

## 准备

1. 复制本仓库中的 `config_demo.py` 为 `config.py`
2. 打开 `config.py`
3. 将 `target_qq` 修改为你的目标 QQ 号
4. 登录 QQ 空间，复制浏览器里当前会话的 `cookies` 字符串
5. 粘贴到 `cookies_str` 中（或者直接使用字典形式 `cookies`）

示例：

```python
cookies_str = (
    "p_skey=xxx; uin=o0000000; skey=xxx; pgv_pvi=xxx; pgv_si=sxxx; "
)

cookies = None
```

> 注意：要确保 `cookies` 来自已登录的 QQ 空间，且在有效期内。

## 安装依赖

```bash
pip install -r requirements.txt
```

或

```bash
pip install requests Pillow piexif coloredlogs
```

## 运行

```bash
python qzone_photo_get.py
```

当 `download_and_save` 执行时，如果 `photo` 含有 EXIF 数据则自动写入本地图片文件。

## 文件说明

- `qzone_photo_get.py` : 主脚本，包含相册列表获取、照片列表获取、下载、EXIF 写回等逻辑
- `config_demo.py` : 配置示例，用户须复制并修改为 `config.py`
- `requirements.txt` : 依赖文件

## 免责声明

此脚本仅供个人下载自己的QQ相册照片使用，请确保你有权访问和下载对应空间内容，遵守平台规则和隐私规范。
