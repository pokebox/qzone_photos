#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import requests
from requests.adapters import HTTPAdapter
from PIL import Image, ImageFile
from PIL.ExifTags import TAGS
from io import BytesIO
import piexif  # 引入专业的 EXIF 库
import threading
import signal
from config import target_qq, cookies, cookies_str, save_path, white_id_list, black_id_list

import traceback
import logging

import coloredlogs
coloredlogs.install(level='INFO',
        fmt='%(asctime)s %(filename)s[%(lineno)d] %(levelname)s: %(message)s',
        milliseconds=True)

# 请求头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://user.qzone.qq.com/',
}
format_type = "json"    #json/jsonp, json为纯数据，jsonp带回调函数

shutdown_event = threading.Event()

# ---------- 全局 Session 和 锁 ----------
session = requests.Session()
session.headers.update(HEADERS)
_truncated_lock = threading.Lock()

def init_session(cookies_dict):
    session.cookies.update(cookies_dict)
    # 增加连接池大小，适配多线程并发
    adapter = HTTPAdapter(
        pool_connections=30,   # 连接池中保存的连接数（不同主机）
        pool_maxsize=30,       # 每个主机的最大连接数（与线程数匹配）
        pool_block=True        # 池满时阻塞等待，而非丢弃连接
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)

def signal_handler(signum, frame):
    """收到中断信号时的处理函数：设置事件标志"""
    logging.warning("收到中断信号 (Ctrl+C)，正在优雅停止...")
    shutdown_event.set()  # 设置全局事件，通知所有线程停止

def register_signal_handler():
    """在主线程中注册信号处理器"""
    signal.signal(signal.SIGINT, signal_handler) # 监听 Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # 监听终止信号

# 解析cookie字符串为字典
def parse_cookie_string(cookie_str):
    """将 'key1=value1; key2=value2' 格式的字符串解析为字典"""
    cookies = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            key, value = item.split('=', 1)
            cookies[key.strip()] = value.strip()
    logging.debug(f"解析cookie字符串成功: {cookies}")
    return cookies

# 获取最终的cookies字典
def get_cookies_dict():
    global cookies, cookies_str
    if cookies is not None:
        # 用户直接给了字典
        return cookies
    elif 'cookies_str' in globals() and cookies_str:
        # 用户给了字符串
        return parse_cookie_string(cookies_str)
    else:
        raise ValueError("请设置 cookies 或 cookies_str 变量！")

# 根据cookies计算g_tk
def get_g_tk(cookie_dict):
    skey = cookie_dict.get('p_skey', cookie_dict.get('skey', ''))
    if not skey:
        return 0
    h = 5381
    for ch in skey:
        h = (h * 33 + ord(ch)) & 0x7fffffff
    return h


def parse_jsonp(text):
    """
    解析 JSONP 响应，支持以下格式：
    - 纯 JSON: {"code":0,...}
    - JSONP: callback({"code":0,...})
    - 多行、带注释等
    """
    # 去除首尾空白
    text = text.strip()
    # with open('debug_response.json', 'w', encoding='utf-8') as f:
    #     f.write(text)
    # 尝试直接解析为 JSON
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            rep_json = repair_json(text)
            return json.loads(rep_json, strict=False)
        except ImportError:
            pass
        pass

    # 匹配 JSONP 回调：函数名(...)
    # 使用正则匹配到最外层括号内的内容（支持嵌套括号）
    # 方法：找到第一个 '(' 和最后一个 ')' 之间的内容
    start = text.find('(')
    end = text.rfind(')')
    if start != -1 and end != -1 and start < end:
        json_str = text[start+1:end].strip()
        # 移除末尾可能的分号
        if json_str.endswith(';'):
            json_str = json_str[:-1]
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError as e:
            try:
                from json_repair import repair_json
                rep_json = repair_json(json_str)
                return json.loads(rep_json, strict=False)
            except Exception as ee:
                logging.error(f"JSON 解析失败: {e}\n{ee}")
                logging.debug(f"尝试解析的内容前200字符: {json_str[:200]}")
            raise

    # 如果都失败，抛出异常
    raise ValueError(f"无法解析 JSONP 响应: {text}")

# 获取所有相册
def get_all_albums(uin, cookies):
    albums = []
    g_tk = get_g_tk(cookies)
    page = 15
    while True:
        url = (f"https://user.qzone.qq.com/proxy/domain/photo.qzone.qq.com/fcgi-bin/fcg_list_album_v3"
               f"?g_tk={g_tk}&t={int(time.time())}&hostUin={uin}&uin={uin}&appid=4"
               f"&inCharset=utf-8&outCharset=utf-8&source=qzone&plat=qzone&format={format_type}"
               f"&notice=0&filter=1&handset=4&pageNumModeSort=40&pageNumModeClass={page}"
               f"&needUserInfo=1&idcNum=4&callbackFun=shine0")
        resp = session.get(url, cookies=cookies)   # 共用 session发起请求
        if resp.status_code != 200:
            break
        data = parse_jsonp(resp.text)
        if data.get('code') != 0:
            logging.error(f"获取相册失败: {data.get('message')}")
            break

        album_data = data.get('data', {})
        class_list = album_data.get('albumListModeClass', [])
        for cat in class_list:
            album_list = cat.get('albumList', [])
            for album in album_list:
                albums.append(album)

        has_next = any(cat.get('nextPageStart', 0) > 0 for cat in class_list)
        if not has_next:
            break
        page += 15
        if len(albums) >= album_data.get('albumsInUser', 0):
            break
    return albums

# 获取指定相册的所有照片（并给每张照片添加 album_id 字段）
def get_photos_in_album(album_id, uin, cookies):
    photos = []
    start = 0
    page_size = 30
    g_tk = get_g_tk(cookies)
    while True:
        url = (
            f"https://h5.qzone.qq.com/proxy/domain/photo.qzone.qq.com/fcgi-bin/cgi_list_photo"
            f"?g_tk={g_tk}&t={int(time.time())}&mode=0&idcNum=4&hostUin={uin}"
            f"&topicId={album_id}&noTopic=0&uin={uin}&pageStart={start}&pageNum={page_size}"
            f"&skipCmtCount=0&singleurl=1&batchId=&notice=0&appid=4"
            f"&inCharset=utf-8&outCharset=utf-8&source=qzone&plat=qzone"
            f"&outstyle=json&format={format_type}&json_esc=1&question=&answer=&callbackFun=shine0"
        )
        resp = session.get(url, cookies=cookies)
        if resp.status_code != 200:
            break
        data = parse_jsonp(resp.text)
        if data.get('code') != 0:
            logging.warning(f"获取相册 {album_id} 照片失败: {data.get('message')}")
            break

        photo_list = data.get('data', {}).get('photoList', [])
        # # 为每张照片添加 album_id
        if not photo_list:
            break
        for p in photo_list:
            p['album_id'] = album_id
        photos.extend(photo_list)

        total = data.get('data', {}).get('totalInAlbum', 0)
        if len(photos) >= total:
            break
        start += page_size
        time.sleep(0.1)
    return photos

# 获取某张照片的评论
def get_photo_comments(album_id, lloc, uin, cookies):
    g_tk = get_g_tk(cookies)
    topic_id = f"{album_id}_{lloc}"
    url = (
        f"https://user.qzone.qq.com/proxy/domain/app.photo.qzone.qq.com/cgi-bin/app/cgi_pcomment_xml_v2"
        f"?uin={uin}&hostUin={uin}&start=0&num=50&order=1&topicId={topic_id}"
        f"&format={format_type}&inCharset=utf-8&outCharset=utf-8&ref=photo"
        f"&need_private_comment=1&albumId={album_id}&qzone=qzone&plat=qzone"
        f"&random={time.time()}&g_tk={g_tk}"
    )
    max_retries=3
    retry_delay=1
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                data = parse_jsonp(resp.text)
                if data.get('code') != 0:
                    return []
                comments = data.get('data', {}).get('comments', [])
                comment_lines = []
                for cmt in comments:
                    poster = cmt.get('poster', {})
                    name = poster.get('name', '未知')
                    content = cmt.get('content', '')
                    if content.strip():
                        comment_lines.append(f"[{name}]: {content}\r\n")
                return comment_lines
            elif resp.status_code == 404:
                logging.warning(f"无法获取EXIF，状态码 404")
                return {}
            else:
                logging.warning(f"获取EXIF失败 (尝试 {attempt}/{max_retries})，状态码 {resp.status_code}")
        except Exception as e:
            logging.error(f"获取EXIF异常 (尝试 {attempt}/{max_retries}): {e}")
        if attempt < max_retries:
            time.sleep(retry_delay)
            retry_delay *= 2   # 指数退避
    logging.error(f"获取照片原始 EXIF 失败: {album_id} {lloc}")
    return []

    

# --- 辅助函数 - 将 '1/22' 这类字符串解析为 piexif 需要的 (分子, 分母) 元组 ---
def parse_rational(val_str):
    if not val_str:
        return None
    parts = str(val_str).split('/')
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return None

def replace_trailing_b(url):
    """
    将 URL 中最后一个 '/b' 或 '/o' 及其之后的内容替换为 '/r'
    """
    # 模式说明：
    # (.*)      - 贪婪匹配从开头到最后一个 '/b' 之前的所有内容（作为捕获组）
    # /b        - 匹配字面量 "/b"
    # .*$       - 匹配剩余部分直到字符串结尾
    # 替换为 \1/r，即保留捕获组内容，再拼接 "/r"
    new_url = re.sub(r'/(o|b)[^/]*$', r'/r', url)
    return new_url

# --- 调用接口获取照片原始 EXIF ---
def get_photo_exif(album_id, lloc, uin, cookies):
    logging.debug(f"获取照片原始 EXIF: {album_id} {lloc}")
    g_tk = get_g_tk(cookies)
    url = (
        f"https://user.qzone.qq.com/proxy/domain/photo.qzone.qq.com/cgi-bin/common/cgi_get_exif_v2"
        f"?g_tk={g_tk}&t={time.time()}&inCharset=utf-8&outCharset=utf-8"
        f"&hostUin={uin}&plat=qzone&source=qzone&topicId={album_id}&lloc={lloc}"
        f"&refer=qzone&uin={uin}&callbackFun=shine1"
    )
    max_retries=3
    retry_delay=1
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                data = parse_jsonp(resp.text)
                if data.get('code') == 0:
                    return data.get('data', {}).get('exif', {})
            elif resp.status_code == 404:
                logging.warning(f"无法获取EXIF，状态码 404")
                return {}
            else:
                logging.warning(f"获取EXIF失败 (尝试 {attempt}/{max_retries})，状态码 {resp.status_code}")
        except Exception as e:
            logging.error(f"获取EXIF异常 (尝试 {attempt}/{max_retries}): {e}")
        if attempt < max_retries:
            time.sleep(retry_delay)
            retry_delay *= 2   # 指数退避
    logging.error(f"获取照片原始 EXIF 失败: {album_id} {lloc}")
    return {}

def download_image_with_retry(img_url, max_retries=3, retry_delay=1):
    """
    使用全局 session 下载图片，支持重试
    返回图片二进制数据，失败返回 None
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(img_url, timeout=30)
            if resp.status_code == 200:
                return resp.content
            elif resp.status_code == 404:
                logging.warning(f"图片未找到，状态码 404，URL: {img_url}")
                return None
            else:
                logging.warning(f"下载失败 (尝试 {attempt}/{max_retries})，状态码 {resp.status_code}，URL: {img_url}")
        except Exception as e:
            logging.error(f"下载异常 (尝试 {attempt}/{max_retries}): {e}")
        if attempt < max_retries:
            time.sleep(retry_delay)
            retry_delay *= 2   # 指数退避
    logging.error(f"下载图片最终失败: {img_url}")
    return None

# --- 更新后的下载与保存逻辑 ---
def download_and_save(photo, album_name, folder_path, uin, cookies, count=(0,0)):
    origin_url = photo.get('origin_url', '')
    raw_url = photo.get('raw', '')
    _tmp_url = ''
    if photo.get('raw_upload') == 1 and raw_url:
        img_url = raw_url
        _tmp_url = raw_url
        logging.debug("使用RAW")
    elif photo.get('origin_upload') == 1 and origin_url:
        _tmp_url = origin_url
        img_url = replace_trailing_b(origin_url)
        logging.debug(f"使用origin: {img_url}")
    else:
        _tmp_url = photo.get('url', '')
        img_url = replace_trailing_b(photo.get('url', ''))
        logging.debug(f"使用url: {img_url}")
        if not img_url:
            logging.warning(f"  - 无法获取图片URL，跳过")
            return

    desc:str = photo.get('desc', '').strip()    # 照片描述
    upload_time = photo.get('uploadtime', '')   # 上传时间，格式 "2015-02-15 03:20:45"
    album_id = photo.get('album_id')            # 相册ID
    lloc = photo.get('lloc', '')                # 照片在相册中的位置，如 "1/22"
    photo_name = photo.get('name', '未命名')    # 照片名称
    topic_name = photo.get('topicName', '')    # 相册名称
    geo_info = photo.get('shootGeo', {})        # 地理位置信息
    owner_name = photo.get('ownerName', photo.get('ownername', ''))    # 照片所有者名称
    modifytime = photo.get('modifytime', '')

    if shutdown_event.is_set():
        logging.warning(f"检测到停止信号，跳过图片: {topic_name}/{photo_name}")
        return
    # 1. 获取评论
    comments = []
    if album_id and lloc:
        comments = get_photo_comments(album_id, lloc, uin, cookies)
        if comments:
            logging.info(f"{photo_name} ({modifytime}) 有 {len(comments)} 条评论: {comments}")
    comment_text = "".join(comments) if comments else ""

    # 2. 获取原始 EXIF
    origin_exif_data = {}
    if album_id and lloc:
        origin_exif_data = get_photo_exif(album_id, lloc, uin, cookies)
        logging.debug(f"原始 EXIF: {origin_exif_data}")

    # 3. 确定最终的时间 (优先使用 originalTime)
    original_time_str = origin_exif_data.get('originalTime', '').strip()
    
    # 判断 originalTime 是否有效
    if original_time_str and original_time_str != "0000:00:00 00:00:00":
        # 接口返回的通常已经是标准格式: "YYYY:MM:DD HH:MM:SS"
        final_time_str = original_time_str
    else:
        # 退回使用上传时间，并将 '-' 转换为 ':'
        final_time_str = upload_time.replace('-', ':') if upload_time else ""

    # 下载图片数据（使用新的重试函数）
    img_data = download_image_with_retry(img_url)
    if img_data is None:
        logging.warning(f"  - {img_url}下载图片失败，跳过{_tmp_url}")
        return

    os.makedirs(folder_path, exist_ok=True)

    # 组合不重复的文件名，使用高优先级的时间
    #logging.warning(photo)
    safe_time_str = final_time_str.replace(':', '').replace(' ', '_') if final_time_str else '未知时间'
    safe_name_str = re.sub(r'[\\/*?:"<>|]', '_', photo_name)
    # safe_lloc_str = re.sub(r'[\\/*?:"<>|]', '_', lloc[:6]) if lloc else str(int(time.time() * 1000))[-6:]
    file_name = f"{safe_time_str}_{safe_name_str}_{modifytime}.jpg"
    file_path = os.path.join(folder_path, file_name)

    # ---------- 处理图片截断错误 ----------
    def try_save_image(data, use_truncated=False):
        """尝试加载并保存图片，use_truncated 表示是否允许截断加载"""
        old_flag = ImageFile.LOAD_TRUNCATED_IMAGES
        try:
            if use_truncated:
                ImageFile.LOAD_TRUNCATED_IMAGES = True
            img = Image.open(BytesIO(data))
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # 加载或创建 EXIF 字典
            try:
                if "exif" in img.info:
                    exif_dict = piexif.load(img.info["exif"])
                else:
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            except Exception:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

            # --- 写入获取到的原始设备和拍摄参数 EXIF ---
            # 相机制造商和型号
            make = origin_exif_data.get('make')
            if make:
                exif_dict["0th"][piexif.ImageIFD.Make] = str(make).encode('ascii', errors='ignore')
            model = origin_exif_data.get('model')
            if model:
                exif_dict["0th"][piexif.ImageIFD.Model] = str(model).encode('ascii', errors='ignore')
            # 曝光、光圈、焦距、曝光补偿 (Rational类型)
            exp_time = parse_rational(origin_exif_data.get('exposureTime'))
            if exp_time:
                exif_dict["Exif"][piexif.ExifIFD.ExposureTime] = exp_time
            fnum = parse_rational(origin_exif_data.get('fnumber'))
            if fnum:
                exif_dict["Exif"][piexif.ExifIFD.FNumber] = fnum
            focal = parse_rational(origin_exif_data.get('focalLength'))
            if focal:
                exif_dict["Exif"][piexif.ExifIFD.FocalLength] = focal
            exp_comp = parse_rational(origin_exif_data.get('exposureCompensation'))
            if exp_comp:
                exif_dict["Exif"][piexif.ExifIFD.ExposureBiasValue] = exp_comp
            # ISO、闪光灯、测光、曝光程序 (Integer类型)
            iso = origin_exif_data.get('iso')
            if iso and str(iso).isdigit():
                exif_dict["Exif"][piexif.ExifIFD.ISOSpeedRatings] = int(iso)
            flash = origin_exif_data.get('flash')
            if flash and str(flash).isdigit():
                exif_dict["Exif"][piexif.ExifIFD.Flash] = int(flash)
            metering = origin_exif_data.get('meteringMode')
            if metering and str(metering).isdigit():
                exif_dict["Exif"][piexif.ExifIFD.MeteringMode] = int(metering)
            exp_prog = origin_exif_data.get('exposureProgram')
            if exp_prog and str(exp_prog).isdigit():
                exif_dict["Exif"][piexif.ExifIFD.ExposureProgram] = int(exp_prog)
            # 拍摄者
            if owner_name:
                exif_dict["0th"][piexif.ImageIFD.Artist] = str(owner_name).encode('utf-8', errors='ignore')

            # 写入确定的拍摄/上传时间
            if final_time_str:
                dt_str = final_time_str.encode('ascii')
                if piexif.ImageIFD.DateTime not in exif_dict["0th"]:
                    exif_dict["0th"][piexif.ImageIFD.DateTime] = dt_str
                if piexif.ExifIFD.DateTimeOriginal not in exif_dict["Exif"]:
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt_str
                if piexif.ExifIFD.DateTimeDigitized not in exif_dict["Exif"]:
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = dt_str

            # 写入标题、主题、备注
            if photo_name:
                exif_dict["0th"][piexif.ImageIFD.XPTitle] = photo_name.encode('utf-16le')
            if album_name:
                exif_dict["0th"][piexif.ImageIFD.XPSubject] = album_name.encode('utf-16le')

            full_comment = ""
            if desc:
                full_comment += f"{desc}\r\n"
            if comment_text:
                full_comment += f"【评论】\r\n{comment_text}"
            if full_comment.strip():
                exif_dict["0th"][piexif.ImageIFD.XPComment] = full_comment.strip().encode('utf-16le')
                exif_dict["Exif"][piexif.ExifIFD.UserComment] = desc.strip().encode('utf-8')
            # 保存图片
            exif_bytes = piexif.dump(exif_dict)
            img.save(file_path, 'JPEG', exif=exif_bytes)
            return True
        except Exception as e:
            if "truncated" in str(e).lower():
                logging.warning(f"图片截断错误: {e}")
            else:
                logging.error(f"处理图片失败: {e}")
            return False
        finally:
            if use_truncated:
                ImageFile.LOAD_TRUNCATED_IMAGES = old_flag

    # 第一次尝试正常保存
    if try_save_image(img_data, use_truncated=False):
        if count[1] != 0:
            logging.info(f" {count[0]}/{count[1]} 张 已保存: {file_path}")
        else:
            logging.info(f" 已保存: {file_path}")
    else:
        # 重试下载（最多2次）
        saved = False
        for retry in range(2):
            if shutdown_event.is_set():
                logging.warning(f"检测到停止信号，跳过图片: {topic_name}/{photo_name}")
                return
            logging.warning(f" {photo_name} ({modifytime}) 图片加载失败，尝试重新下载 (第{retry+1}次)")
            new_data = download_image_with_retry(img_url, max_retries=1)
            if new_data and try_save_image(new_data, use_truncated=False):
                img_data = new_data
                logging.info(f"  重试成功，已保存: {file_path}")
                saved = True
                break
        if not saved:
            # 启用截断模式
            logging.warning(f"  仍失败，启用截断模式保存")
            if try_save_image(img_data, use_truncated=True):
                logging.info(f"  截断模式保存成功: {file_path}")
            else:
                # 最后手段：保存原始二进制
                logging.error(f"   {photo_name} ({modifytime}) 所有方式均失败，保存原始数据")
                with open(file_path, 'wb') as f:
                    f.write(img_data)

    # 修改文件系统时间
    if final_time_str:
        try:
            # final_time_str 的格式是 "YYYY:MM:DD HH:MM:SS"
            time_array = time.strptime(final_time_str, "%Y:%m:%d %H:%M:%S")
            timestamp = time.mktime(time_array)
            os.utime(file_path, (timestamp, timestamp))
        except Exception as e:
            logging.error(f" {photo_name} ({modifytime}) 修改文件系统时间失败: {e}")

def get_photos_id():
    '''获取相册ID和相册名称'''
    try:
        cookies = get_cookies_dict()
        init_session(cookies)
    except ValueError as e:
        logging.error(e)
        return

    logging.info("正在获取所有相册...")
    albums = get_all_albums(target_qq, cookies)
    logging.info(f"共找到 {len(albums)} 个相册")
    photos_id = {}
    for album in albums:
        album_id = album.get('id')
        album_name = album.get('name', '未命名')
        #logging.info(f"处理相册: {album_name} (ID: {album_id})")
        photos_id[album_id] = album_name
    if photos_id == {}:
        logging.warning("没有获取到相册ID和相册名称，可能是登录状态无效或接口变更")
    else:
        json_str = json.dumps(photos_id, indent=4, ensure_ascii=False)
        with open('photos_list.json', 'w', encoding='utf-8') as f:
            f.write(json_str)
        logging.info(json_str)
    return photos_id

def main():
    try:
        cookies = get_cookies_dict()
        init_session(cookies)
    except ValueError as e:
        logging.error(e)
        return

    logging.info("正在获取所有相册...")
    albums = get_all_albums(target_qq, cookies)
    logging.info(f"共找到 {len(albums)} 个相册")

    for album in albums:
        album_id = album.get('id')
        if (album_id in black_id_list) or (white_id_list and album_id not in white_id_list):
            continue
        album_name = album.get('name', '未命名')
        folder_name = re.sub(r'[\\/*?:"<>|]', '_', album_name)
        folder_path = os.path.join(save_path, folder_name)
        logging.info(f"处理相册: {album_name} (ID: {album_id})")

        photos = get_photos_in_album(album_id, target_qq, cookies)
        logging.info(f"  相册内有 {len(photos)} 张照片")

        for idx, photo in enumerate(photos, 1):
            logging.info(f"    下载第 {idx}/{len(photos)} 张...")
            download_and_save(photo, album_name, folder_path, target_qq, cookies)
            time.sleep(0.1)


from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

def get_img_thread():
    '''
    照片多线程
    '''
    try:
        cookies = get_cookies_dict()
        init_session(cookies)
    except ValueError as e:
        logging.error(e)
        return

    # 判断是否有photos_list.json文件，如果有则直接读取相册ID和名称，避免重复请求接口获取相册列表
    albums = []
    if os.path.exists('photos_list.json'):
        photos_id = {}
        with open('photos_list.json', 'r', encoding='utf-8') as f:
            photos_id = json.load(f)
        logging.info(f"从 photos_list.json 读取到 {len(photos_id)} 个相册ID和名称")
        albums = [{'id': aid, 'name': aname} for aid, aname in photos_id.items()]
    else:
        logging.info("正在获取所有相册...")
        albums = get_all_albums(target_qq, cookies)
        logging.info(f"共找到 {len(albums)} 个相册")

    for album in albums:
        album_id = album.get('id')
        if (album_id in black_id_list) or (white_id_list and album_id not in white_id_list):
            continue
        album_name = album.get('name', '未命名')
        folder_name = re.sub(r'[\\/*?:"<>|]', '_', album_name)
        folder_path = os.path.join(save_path, folder_name)
        logging.info(f"处理相册: {album_name} (ID: {album_id})")

        photos = get_photos_in_album(album_id, target_qq, cookies)
        total = len(photos)
        logging.info(f"  相册内有 {total} 张照片")
        if total == 0:
            continue

        # 使用线程池并发下载（最多20个线程）
        with ThreadPoolExecutor(max_workers=20) as executor:
            # 提交所有下载任务
            futures = []
            for idx, photo in enumerate(photos, 1):
                logging.debug(f"    提交下载第 {idx}/{total} 张...")
                future = executor.submit(
                    download_and_save,
                    photo, album_name, folder_path, target_qq, cookies, (idx, total)
                )
                futures.append(future)

            # 循环等待任务完成，同时响应停止信号
            while futures:
                # 等待任意一个任务完成，或者每隔1秒检查一次停止标志
                done, futures = wait(futures, timeout=1.0, return_when=FIRST_COMPLETED)
                
                # 检查是否需要停止
                if shutdown_event.is_set():
                    logging.warning("收到停止信号，正在取消剩余下载任务...")
                    for future in futures:
                        future.cancel()  # 取消尚未开始的任务
                    # 等待所有正在运行的任务完成，以确保资源被正确清理
                    wait(futures, timeout=5.0)
                    logging.warning("所有下载任务已停止，程序退出。")
                    return  # 直接退出函数，停止后续处理

                # 处理已完成的任务，捕获其中的异常
                for future in done:
                    try:
                        future.result()
                    except Exception as e:
                        logging.error(f"下载照片时出错: {traceback.format_exc()}")

        # 从列表里删除这个相册的ID和名称
        albums.remove(album)
        # 写回 photos_list.json 文件，记录剩余未处理的相册
        remaining_photos_id = {a.get('id'): a.get('name', '未命名') for a in albums}
        with open('photos_list.json', 'w', encoding='utf-8') as f:
            json.dump(remaining_photos_id, f, indent=4, ensure_ascii=False)
        logging.info(f"  相册 {album_name} 所有照片下载完成")
        time.sleep(1)   # 相册之间稍作延迟，避免请求过快


def get_photos_thread():
    '''
    相册多线程，最多同时处理20个相册
    '''
    try:
        cookies = get_cookies_dict()
        init_session(cookies)
    except ValueError as e:
        logging.error(e)
        return

    logging.info("正在获取所有相册...")
    albums = get_all_albums(target_qq, cookies)
    logging.info(f"共找到 {len(albums)} 个相册")

    def process_album(aid, aname, fpath):
        logging.info(f"处理相册: {aname} (ID: {aid})")
        photos = get_photos_in_album(aid, target_qq, cookies)
        logging.info(f"  相册内有 {len(photos)} 张照片")
        for idx, photo in enumerate(photos, 1):
            logging.info(f"    下载第 {idx}/{len(photos)} 张...")
            download_and_save(photo, aname, fpath, target_qq, cookies)
            time.sleep(0.1)  # 同一相册内照片间隔，避免请求过快

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for album in albums:
            album_id = album.get('id')
            if (album_id in black_id_list) or (white_id_list and album_id not in white_id_list):
                continue
            album_name = album.get('name', '未命名')
            folder_name = re.sub(r'[\\/*?:"<>|]', '_', album_name)
            folder_path = os.path.join(save_path, folder_name)
            future = executor.submit(process_album, album_id, album_name, folder_path)
            futures.append(future)

        # 等待所有相册处理完成，并捕获异常
        while futures:
            # 等待任意一个任务完成，或者每隔1秒检查一次停止标志
            done, futures = wait(futures, timeout=1.0, return_when=FIRST_COMPLETED)
            
            # 检查是否需要停止
            if shutdown_event.is_set():
                logging.warning("收到停止信号，正在取消剩余下载任务...")
                for future in futures:
                    future.cancel()  # 取消尚未开始的任务
                # 等待所有正在运行的任务完成，以确保资源被正确清理
                wait(futures, timeout=5.0)
                logging.warning("所有下载任务已停止，程序退出。")
                return  # 直接退出函数，停止后续处理

            # 处理已完成的任务，捕获其中的异常
            for future in done:
                try:
                    future.result()
                except Exception as e:
                    logging.error(f"处理相册时出错: {traceback.format_exc()}")

    logging.info("所有相册处理完成")


if __name__ == '__main__':
    get_img_thread()
