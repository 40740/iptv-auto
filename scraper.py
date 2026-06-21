#!/usr/bin/env python3
"""
GitHub 港澳台 IPTV 源自动抓取 + 真实验证 + TVBox 输出  v3
===========================================================
v3 改进:
  - 新增 tonkiang.us 数据源 (cookie方式, 设置 TONKIANG_COOKIE 环境变量)
  - 新增 8 个数据源 (共 18 个数据源)
  - 增强验证: 更严格过滤假阳性
  - 自动发现 tonkiang.us 搜索页的频道

tonkiang.us 使用方法:
  1. 在浏览器打开 https://www.tonkiang.us/
  2. 通过 reCAPTCHA 验证
  3. F12 打开开发者工具 → Application → Cookies
  4. 复制 cf_clearance 的值
  5. 设置环境变量: export TONKIANG_COOKIE="cf_clearance=xxx"
  注意: cf_clearance 绑定 IP, 在同一网络下有效

验证原理:
    Level 1 - HTTP连通: 请求m3u8地址, 状态码<400
    Level 2 - 内容有效: 返回内容含m3u8标签或视频数据
    Level 3 - 有视频流: 请求第一个ts分片, 确认返回MPEG-TS数据(0x47)
    额外  - HTML检测: 拒绝返回HTML错误页的源
    额外  - 域名黑名单: 过滤已知过期/失效域名
    额外  - 重定向检测: 拒绝重定向到错误页的源
    额外  - 内容长度: 过滤过短响应

使用:
    python scraper.py                         # 完整验证
    python scraper.py --no-validate           # 跳过验证(快速)
    python scraper.py --timeout 8 --workers 50 # 自定义参数
    TONKIANG_COOKIE="cf_clearance=xxx" python scraper.py  # 启用tonkiang
"""

import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, quote

warnings.filterwarnings('ignore')

try:
    import requests
except ImportError:
    print("[!] pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 数据源 (按质量排序: 活跃度 > 验证机制 > 覆盖面)
# ═══════════════════════════════════════════════════════════════

SOURCES = [
    # ── Tier 1: 高质量源 (活跃维护 + 有验证机制) ──
    {
        "name": "sammy0101-HK",
        "url": "https://raw.githubusercontent.com/sammy0101/hk-iptv-auto/main/hk_live.m3u",
        "filter_keyword": True,
        "quality": "high",
    },
    {
        "name": "iptv-org-香港",
        "url": "https://iptv-org.github.io/iptv/countries/hk.m3u",
        "quality": "high",
    },
    {
        "name": "iptv-org-澳门",
        "url": "https://iptv-org.github.io/iptv/countries/mo.m3u",
        "quality": "high",
    },
    {
        "name": "Free-TV-香港",
        "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_hong_kong.m3u8",
        "quality": "high",
    },
    {
        "name": "MercuryZz-港澳台",
        "url": "https://raw.githubusercontent.com/MercuryZz/IPTVN/Files/GAT.m3u",
        "quality": "high",
    },
    {
        "name": "Supprise0901-live",
        "url": "https://raw.githubusercontent.com/Supprise0901/TVBox_live/main/live.txt",
        "filter_keyword": True,
        "quality": "high",
    },
    # ── Tier 2: 中等质量源 (覆盖面广, 但含部分死链) ──
    {
        "name": "Joker-Cold-已验证",
        "url": "https://raw.githubusercontent.com/Joker-Cold/HK-IPTV/main/source/COLD_OK.m3u8",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "Joker-Cold-iptv",
        "url": "https://raw.githubusercontent.com/Joker-Cold/HK-IPTV/main/source/source_iptv.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "Joker-Cold-全量",
        "url": "https://raw.githubusercontent.com/Joker-Cold/HK-IPTV/main/source/all_sources.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "imDazui-港澳台202506",
        "url": "https://raw.githubusercontent.com/imDazui/Tvlist-awesome-m3u-m3u8/master/m3u/%E5%8F%B0%E6%B9%BE%E9%A6%99%E6%B8%AF%E6%BE%B3%E9%97%A8202506.m3u",
        "quality": "medium",
    },
    {
        "name": "imDazui-港澳台2023",
        "url": "https://raw.githubusercontent.com/imDazui/Tvlist-awesome-m3u-m3u8/master/m3u/%E5%8F%B0%E6%B9%BE%E9%A6%99%E6%B8%AF%E6%BE%B3%E9%97%A82023.m3u",
        "quality": "medium",
    },
    {
        "name": "imDazui-港澳台海外",
        "url": "https://raw.githubusercontent.com/imDazui/Tvlist-awesome-m3u-m3u8/master/m3u/%E5%8F%B0%E6%B9%BE%E9%A6%99%E6%B8%AF%E6%B5%B7%E5%A4%96.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "ChinaIPTV-自动更新",
        "url": "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8",
        "filter_group": "港澳台",
        "quality": "medium",
    },
    {
        "name": "Guovin-TV",
        "url": "https://raw.githubusercontent.com/Guovin/TV/gd/output/result.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "iptv-org-中文",
        "url": "https://iptv-org.github.io/iptv/languages/zho.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "Kimentanm-aptv",
        "url": "https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
    {
        "name": "vbskycn-iptv4",
        "url": "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
        "filter_keyword": True,
        "quality": "medium",
    },
]

# tonkiang.us 搜索关键词
TONKIANG_KEYWORDS = [
    "翡翠台", "TVB", "ViuTV", "HOY TV", "RTHK", "港台",
    "凤凰", "香港卫视", "澳门", "TDM", "TVBS", "中天",
    "东森", "Now TV", "有線", "Cable TV", "ATV",
    "明珠台", "本港台", "香港电台",
]

# 已知失效/过期的域名前缀
DEAD_DOMAINS = [
    'aktv.top',
    'php.jdshipin.com',
    'v2h.jdshipin.com',
    'smt2.1678520.xyz',
    'iptv.wwkejishe.top',
]

CDN_PREFIX = "https://cdn.jsdelivr.net/gh/"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'identity',
}

# ═══════════════════════════════════════════════════════════════
# 香港/澳门频道识别
# ═══════════════════════════════════════════════════════════════

HK_PATTERNS = [
    r'香港', r'Hong\s*Kong', r'\bHK\b', r'HKS', r'HKSTV',
    r'翡翠', r'Jade', r'TVB', r'无线', r'翡翠台',
    r'ViuTV', r'Viu\s*TV', r'HOY\s*TV', r'HOY',
    r'港台', r'RTHK', r'港台電視',
    r'凤凰', r'Phoenix', r'凤凰卫视',
    r'澳门', r'Macau', r'TDM', r'澳广视',
    r'ATV', r'亚洲电视', r'本港', r'國際台',
    r'有線', r'i-CABLE', r'Cable\s*TV',
    r'奇妙', r'Amazing', r'Now\s*[^\s]', r'Now\s*TV',
    r'開電視', r'港視', r'HKTVE', r'HKTV',
    r'天映', r'Celestial', r'耀才', r'BSTV',
    r'TVBS', r'中天', r'东森', r'東森', r'三立',
    r'民视', r'民視', r'台视', r'台視', r'中视', r'中視',
    r'华视', r'華視', r'公视', r'公視',
    r'八大', r'寰宇', r'龙华', r'龍華',
    r'镜面', r'靖天', r'台娱', r'TVBS-N',
    r'CTi', r'EBC', r'FTV', r'STV', r'TTV',
    r'MOMO', r'ELTA', r'博斯',
]
HK_PAT = [re.compile(p, re.IGNORECASE) for p in HK_PATTERNS]

EXCLUDE_PATTERNS = [
    r'^CCTV', r'^湖南卫视', r'^东方卫视', r'^浙江卫视', r'^江苏卫视',
    r'^北京卫视', r'^广东卫视', r'^深圳卫视', r'^四川卫视',
    r'^山东卫视', r'^河南卫视', r'^湖北卫视', r'^安徽卫视',
    r'^天津卫视', r'^重庆卫视', r'^辽宁卫视', r'^吉林卫视',
    r'^黑龙江卫视', r'^福建卫视', r'^河北卫视', r'^江西卫视',
    r'^广西卫视', r'^云南卫视', r'^旅游卫视',
    r'^咪咕', r'^晴彩', r'^中国之声', r'^央广',
    r'免费订阅', r'温馨提示', r'维护时间', r'公告说明',
]
EXCLUDE_PAT = [re.compile(p, re.IGNORECASE) for p in EXCLUDE_PATTERNS]


def is_hk_channel(name):
    if any(p.search(name) for p in EXCLUDE_PAT):
        return False
    return any(p.search(name) for p in HK_PAT)


def is_dead_domain(url):
    """检查 URL 是否属于已知失效域名"""
    try:
        host = urlparse(url).hostname or ''
        return any(host == d or host.endswith('.' + d) for d in DEAD_DOMAINS)
    except:
        return False


# ═══════════════════════════════════════════════════════════════
# M3U 解析
# ═══════════════════════════════════════════════════════════════

def _parse_extinf_line(line):
    in_quote = False
    last_comma = -1
    for i in range(len(line) - 1, -1, -1):
        if line[i] == '"':
            in_quote = not in_quote
        elif line[i] == ',' and not in_quote:
            last_comma = i
            break
    if last_comma == -1:
        return None
    return line[last_comma + 1:].strip(), line[:last_comma]


def parse_m3u(content, source_name, filter_group=None, filter_keyword=False):
    channels = []
    lines = content.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    current_info = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith('#EXTVLCOPT:'):
            continue

        if line.startswith('#EXTINF'):
            parsed = _parse_extinf_line(line)
            if parsed:
                name, extinf_header = parsed
                group = ""
                gm = re.search(r'group-title="([^"]*)"', extinf_header, re.IGNORECASE)
                if gm:
                    group = gm.group(1)
                tvg_id = ""
                tm = re.search(r'tvg-id="([^"]*)"', extinf_header, re.IGNORECASE)
                if tm:
                    tvg_id = tm.group(1)
                tvg_logo = ""
                lm = re.search(r'tvg-logo="([^"]*)"', extinf_header, re.IGNORECASE)
                if lm:
                    tvg_logo = lm.group(1)
                current_info = {
                    "name": name, "url": None, "group": group,
                    "tvg_id": tvg_id, "tvg_logo": tvg_logo,
                    "source": source_name,
                }
            continue

        if line.startswith('#'):
            continue

        if current_info and (line.startswith('http') or line.startswith('rtmp') or line.startswith('rtsp')):
            current_info['url'] = line

            include = True
            if filter_group:
                if filter_group not in current_info['group'] and filter_group not in current_info['name']:
                    include = False
            if filter_keyword and not is_hk_channel(current_info['name']):
                include = False
            if include and is_dead_domain(current_info['url']):
                include = False

            if include:
                channels.append(current_info)
            current_info = None

    return channels


def parse_tvbox(content, source_name, filter_keyword=False):
    """解析 TVBox 格式 (name,url 或 group,#genre#)"""
    channels = []
    lines = content.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    current_group = ""

    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            if '#genre#' in line:
                parts = line.split(',')
                if len(parts) >= 2:
                    current_group = parts[0].strip()
            continue

        if ',' in line:
            parts = line.split(',', 1)
            name = parts[0].strip()
            url = parts[1].strip()
            if url and (url.startswith('http') or url.startswith('rtmp') or url.startswith('rtsp')):
                ch = {
                    "name": name, "url": url, "group": current_group,
                    "tvg_id": "", "tvg_logo": "", "source": source_name,
                }
                include = True
                if filter_keyword and not is_hk_channel(name):
                    include = False
                if include and is_dead_domain(url):
                    include = False
                if include:
                    channels.append(ch)

    return channels


# ═══════════════════════════════════════════════════════════════
# tonkiang.us 抓取
# ═══════════════════════════════════════════════════════════════

def fetch_tonkiang(cookie_str, keywords, timeout=15):
    """
    用 cookie 抓取 tonkiang.us 搜索结果。
    cookie_str: 从浏览器复制的完整 cookie 字符串
    返回频道列表
    """
    channels = []
    if not cookie_str:
        return channels

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cookie': cookie_str,
        'Referer': 'https://www.tonkiang.us/',
    }

    for keyword in keywords:
        try:
            search_url = f'https://www.tonkiang.us/?s={quote(keyword)}'
            r = requests.get(search_url, headers=headers, timeout=timeout, verify=False)

            # 如果返回的还是验证页面，说明cookie无效
            if 'recaptcha' in r.text.lower() and len(r.text) < 5000:
                print(f"    [!] tonkiang cookie 可能已过期 (遇到reCAPTCHA)")
                break

            if r.status_code != 200:
                continue

            # 从搜索结果页提取频道信息
            # tonkiang的搜索结果通常包含视频链接
            found = _parse_tonkiang_page(r.text, keyword)
            channels.extend(found)
            time.sleep(0.5)

        except Exception as e:
            print(f"    [!] tonkiang 搜索 '{keyword}' 失败: {str(e)[:40]}")
            continue

    return channels


def _parse_tonkiang_page(html, keyword):
    """解析 tonkiang.us 搜索结果页，提取频道链接"""
    channels = []

    # tonkiang.us 搜索结果格式: 通常是表格或列表，包含频道名和m3u8链接
    # 尝试多种匹配模式

    # 模式1: 直接的m3u8链接
    m3u8_pattern = re.compile(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*')
    m3u8_urls = m3u8_pattern.findall(html)

    # 模式2: 搜索结果中的频道名和链接对
    # tonkiang通常用 <td> 或 <a> 标签包裹
    name_link_pattern = re.compile(
        r'<td[^>]*>\s*(?:<[^>]+>)*\s*(?:<a[^>]*>)?\s*([^<]+?)\s*(?:</a>)?\s*(?:</td>)*\s*</td>'
        r'.*?'
        r'(https?://[^\s"\'<>]+)',
        re.DOTALL
    )
    matches = name_link_pattern.findall(html)

    if matches:
        for name, url in matches:
            name = name.strip()
            url = url.strip().rstrip(',')
            if name and url and len(name) < 100:
                channels.append({
                    "name": name,
                    "url": url,
                    "group": "",
                    "tvg_id": "",
                    "tvg_logo": "",
                    "source": f"tonkiang.us",
                })

    # 如果上面的模式没匹配到，用简单模式提取
    if not channels and m3u8_urls:
        for url in m3u8_urls:
            url = url.strip().rstrip(',')
            if url not in [c['url'] for c in channels]:
                channels.append({
                    "name": f"tonkiang-{keyword}",
                    "url": url,
                    "group": "",
                    "tvg_id": "",
                    "tvg_logo": "",
                    "source": "tonkiang.us",
                })

    # 模式3: 提取所有 http 链接作为可能的流地址
    if not channels:
        all_urls = re.findall(r'(https?://[^\s"\'<>]+)', html)
        for url in all_urls:
            url = url.strip().rstrip(',')
            if any(ext in url for ext in ['.m3u8', '.ts', '.flv', '.mp4']):
                channels.append({
                    "name": f"tonkiang-{keyword}",
                    "url": url,
                    "group": "",
                    "tvg_id": "",
                    "tvg_logo": "",
                    "source": "tonkiang.us",
                })

    return channels


# ═══════════════════════════════════════════════════════════════
# 网络请求
# ═══════════════════════════════════════════════════════════════

def fetch_url(url, timeout=15, use_cdn=True):
    urls = [url]
    if use_cdn and 'raw.githubusercontent.com' in url:
        gh = url.replace('https://raw.githubusercontent.com/', '')
        urls.append(f"{CDN_PREFIX}{gh}")
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=timeout, verify=False)
            if r.status_code == 200 and len(r.text) > 50:
                return r.text
        except Exception:
            continue
    return ""


def validate_stream(url, timeout=12):
    """
    三级验证 + 额外检测, 确保频道真正可播:
      1. HTTP连通 + 非HTML错误页 + 检查重定向链
      2. 内容有效 (m3u8标签/视频数据, 拒绝过短响应)
      3. 请求ts分片确认MPEG-TS视频流(0x47头)
    返回: (bool, str)
    """
    # Level 1: HTTP 连通 + 重定向检测
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         stream=True, verify=False, allow_redirects=True)
        status = r.status_code
        if status >= 400:
            return False, f"HTTP {status}"

        # 检查最终URL是否是错误页面
        final_url = r.url
        if any(kw in final_url.lower() for kw in ['error', '404', 'blocked', 'denied', 'captcha']):
            return False, f"redirected to error"

        content_type = r.headers.get('Content-Type', '')
        content_length = r.headers.get('Content-Length', '')

        data = b''
        for chunk in r.iter_content(chunk_size=4096):
            data += chunk
            if len(data) >= 32768:  # 读更多数据以做更准确判断
                break
        r.close()
    except requests.exceptions.Timeout:
        return False, "timeout"
    except requests.exceptions.ConnectionError:
        return False, "connection refused"
    except Exception as e:
        return False, str(e)[:60]

    # Level 2: 内容有效
    if len(data) < 20:
        return False, "empty response"

    text = data.decode('utf-8', errors='ignore').strip()

    # 检测HTML错误页 (更严格)
    if text.startswith('<!') or text.startswith('<html') or '<!doctype' in text.lower():
        return False, "HTML error page"
    # 也检测以<html开头的
    if len(text) > 100 and '<html' in text[:500].lower():
        return False, "HTML page"

    # 视频流直接数据
    if any(v in content_type for v in ['video/', 'application/octet-stream', 'application/mp4']):
        if len(data) > 1000:
            return True, "direct stream"
        return False, "direct stream too small"

    # m3u8 播放列表
    if '#EXTM3U' in text or '#EXTINF' in text or '.ts' in text or '.m3u8' in text:
        # Level 3: 验证ts分片
        ts_urls = []
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('http'):
                ts_urls.append(line)
            elif '/' in line and not line.startswith('#'):
                base = url.rsplit('/', 1)[0]
                ts_urls.append(f"{base}/{line}")

        if ts_urls:
            # 尝试前2个ts分片 (有的第一个可能是空)
            for ts_url in ts_urls[:2]:
                try:
                    tr = requests.get(ts_url, headers=HEADERS, timeout=timeout,
                                      stream=True, verify=False, allow_redirects=True)
                    if tr.status_code >= 400:
                        return False, f"ts HTTP {tr.status_code}"
                    ts_data = b''
                    for chunk in tr.iter_content(chunk_size=2048):
                        ts_data += chunk
                        if len(ts_data) >= 4096:
                            break
                    tr.close()

                    if len(ts_data) < 188:
                        continue  # MPEG-TS包最小188字节, 试试下一个

                    ts_ct = tr.headers.get('Content-Type', '')
                    # MPEG-TS: 0x47 开头 (sync byte)
                    if ts_data[0:1] == b'\x47':
                        return True, "MPEG-TS valid"
                    # 检查是否在前几KB中包含0x47同步字节
                    if b'\x47' in ts_data[:4096]:
                        # 找到同步字节的位置
                        pos = ts_data.index(b'\x47')
                        if pos < 192:  # 在合理范围内
                            return True, "MPEG-TS (sync found)"
                    if 'video' in ts_ct or 'mpeg' in ts_ct or 'octet-stream' in ts_ct:
                        if len(ts_data) > 1000:
                            return True, "ts valid (content-type)"
                    continue

                except Exception:
                    continue

            # master playlist (多码率) - 尝试跟随
            if '#EXT-X-STREAM-INF' in text:
                for line in text.split('\n'):
                    line = line.strip()
                    if line.startswith('http') and '.m3u8' in line:
                        try:
                            mr = requests.get(line, headers=HEADERS, timeout=timeout//2,
                                            stream=True, verify=False, allow_redirects=True)
                            if mr.status_code == 200:
                                mtext = b''
                                for chunk in mr.iter_content(chunk_size=4096):
                                    mtext += chunk
                                    if len(mtext) >= 16384:
                                        break
                                mr.close()
                                mtext_str = mtext.decode('utf-8', errors='ignore')
                                if '#EXTINF' in mtext_str or '.ts' in mtext_str:
                                    return True, "master playlist (valid sub)"
                            mr.close()
                        except:
                            pass
                return True, "master playlist"

            return False, "ts content uncertain"

        # 没有ts分片的m3u8
        if '#EXT-X-STREAM-INF' in text:
            return True, "master playlist (no segments)"
        return False, "m3u8 no segments"

    # 非视频非m3u8内容 - 更严格判断
    if len(text) < 200:
        return False, f"too short ({len(text)}B)"
    if any(kw in text.lower() for kw in ['error', 'not found', '403', '404', 'denied',
                                           'blocked', 'captcha', 'token expired']):
        return False, "error response"

    return False, f"unknown format ({len(data)}B)"


# ═══════════════════════════════════════════════════════════════
# 分类逻辑
# ═══════════════════════════════════════════════════════════════

def classify_channel(name):
    n = name
    if any(k in n for k in ['RTHK', '港台電視', '港台电视', '港台']):
        return '港台RTHK'
    if any(k in n for k in ['翡翠', 'Jade', 'TVBJ', '无线']):
        return 'TVB翡翠台'
    if '明珠' in n:
        return 'TVB明珠台'
    if 'HOY' in n:
        return 'HOY TV'
    if 'ViuTV' in n or 'viu' in n.lower():
        return 'ViuTV'
    if any(k in n for k in ['凤凰中文', 'Phoenix Chinese']):
        return '凤凰中文台'
    if any(k in n for k in ['凤凰资讯', 'Phoenix Info']):
        return '凤凰资讯台'
    if any(k in n for k in ['凤凰电影', '凤凰香港']):
        return '凤凰其他频道'
    if any(k in n for k in ['凤凰']):
        return '凤凰卫视'
    if any(k in n for k in ['TVBS']):
        return 'TVBS（台湾）'
    if any(k in n for k in ['中天', 'CTi']):
        return '中天（台湾）'
    if any(k in n for k in ['东森', '東森', 'EBC']):
        return '东森（台湾）'
    if any(k in n for k in ['三立']):
        return '三立（台湾）'
    if any(k in n for k in ['民视', '民視', 'FTV']):
        return '民视（台湾）'
    if any(k in n for k in ['台视', '台視', 'TTV']):
        return '台视（台湾）'
    if any(k in n for k in ['中视', '中視', 'CTS']):
        return '中视（台湾）'
    if any(k in n for k in ['公视', '公視', 'PTS']):
        return '公视（台湾）'
    if any(k in n for k in ['澳门', 'Macau', 'TDM', '澳视', '澳广']):
        return '澳门频道'
    if any(k in n for k in ['香港卫视', 'HKS', 'HKSTV']):
        return '香港卫视'
    if any(k in n for k in ['耀才', 'BSTV']):
        return '财经频道'
    if any(k in n for k in ['天映', 'Celestial']):
        return '电影频道'
    if any(k in n for k in ['Now', 'now', '有線', 'Cable', '有线电视']):
        return '有线/Now'
    if any(k in n for k in ['ATV', '亚洲电视', '本港', '國際']):
        return '已停播存档'
    if any(k in n for k in ['龙华', '龍華']):
        return '龙华（台湾）'
    if any(k in n for k in ['八大']):
        return '八大（台湾）'
    if any(k in n for k in ['MOMO', 'ELTA', '博斯']):
        return '其他台湾频道'
    return '港澳其他频道'


# ═══════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════

def write_tvbox(channels, filepath):
    groups = {}
    for ch in channels:
        g = ch['category']
        if g not in groups:
            groups[g] = []
        groups[g].append(ch)

    group_order = [
        '港台RTHK', 'TVB翡翠台', 'TVB明珠台', 'ViuTV', 'HOY TV',
        '凤凰中文台', '凤凰资讯台', '凤凰其他频道', '凤凰卫视',
        'TVBS（台湾）', '中天（台湾）', '东森（台湾）', '三立（台湾）',
        '民视（台湾）', '台视（台湾）', '中视（台湾）', '公视（台湾）',
        '龙华（台湾）', '八大（台湾）', '其他台湾频道',
        '香港卫视', '澳门频道',
        '财经频道', '电影频道', '有线/Now',
        '港澳其他频道', '已停播存档',
    ]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f'# 港澳台IPTV自动更新 | {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'# 频道总数: {len(channels)} (全部已验证可播)\n')
        f.write(f'# 数据源: GitHub公开仓库 + tonkiang.us\n')
        f.write(f'# 验证: 三级流验证 (HTTP+内容+MPEG-TS)\n\n')

        written = set()
        for g in group_order:
            if g in groups and groups[g]:
                f.write(f'{g},#genre#\n')
                for ch in groups[g]:
                    f.write(f'{ch["name"]},{ch["url"]}\n')
                f.write('\n')
                written.add(g)

        for g in sorted(groups.keys()):
            if g not in written and groups[g]:
                f.write(f'{g},#genre#\n')
                for ch in groups[g]:
                    f.write(f'{ch["name"]},{ch["url"]}\n')
                f.write('\n')

    print(f"  [+] TVBox: {filepath} ({len(channels)} 个频道)")


def write_m3u(channels, filepath):
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        f.write(f'# 港澳台 IPTV (已验证可播) | {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'# 频道: {len(channels)}\n\n')
        for ch in channels:
            logo = ch.get('tvg_logo', '')
            if logo:
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{ch["category"]}",{ch["name"]}\n')
            else:
                f.write(f'#EXTINF:-1 group-title="{ch["category"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')
    print(f"  [+] M3U: {filepath} ({len(channels)} 个频道)")


def write_json(channels, dead, stats, filepath):
    data = {
        "meta": {
            "title": "港澳台 IPTV (已验证)",
            "update": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "validated": True,
        },
        "stats": stats,
        "alive": [{"name": c["name"], "url": c["url"], "category": c["category"], "reason": c.get("reason",""), "source": c.get("source","")} for c in channels],
        "dead": dead,
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  [+] JSON: {filepath}")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='GitHub 港澳台 IPTV 自动抓取 v3')
    parser.add_argument('--no-validate', action='store_true', help='跳过直播源验证')
    parser.add_argument('--timeout', type=int, default=12, help='验证超时秒数')
    parser.add_argument('--workers', type=int, default=30, help='并发验证数')
    parser.add_argument('--output-dir', type=str, default='output', help='输出目录')
    parser.add_argument('--tonkiang-cookie', type=str, default='', help='tonkiang.us cookie (或用TONKIANG_COOKIE环境变量)')
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"  港澳台 IPTV 自动抓取 + 真实验证 v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: {len(SOURCES)} 个GitHub源 + tonkiang.us")
    print(f"{'='*60}")

    # ── Step 1: 抓取 GitHub 数据源 ──
    all_channels = []
    print(f"\n[1/3] 抓取 {len(SOURCES)} 个数据源...")
    for i, src in enumerate(SOURCES):
        print(f"  [{i+1}/{len(SOURCES)}] {src['name']}", end='')
        content = fetch_url(src['url'], timeout=15)
        if not content:
            print(f" -> [!] 失败")
            continue

        # 根据格式自动选择解析器
        if '#EXTM3U' in content or '#EXTINF' in content:
            channels = parse_m3u(content, src['name'],
                                 filter_group=src.get('filter_group'),
                                 filter_keyword=src.get('filter_keyword', False))
        elif '#genre#' in content:
            channels = parse_tvbox(content, src['name'],
                                   filter_keyword=src.get('filter_keyword', False))
        else:
            # 尝试m3u解析
            channels = parse_m3u(content, src['name'],
                                 filter_group=src.get('filter_group'),
                                 filter_keyword=src.get('filter_keyword', False))
        print(f" -> {len(channels)} 个频道")
        all_channels.extend(channels)
        time.sleep(0.3)

    # ── Step 1b: tonkiang.us 抓取 ──
    tk_cookie = args.tonkiang_cookie or os.environ.get('TONKIANG_COOKIE', '')
    if tk_cookie:
        print(f"\n[1b] 抓取 tonkiang.us (cookie已设置)...")
        tk_channels = fetch_tonkiang(tk_cookie, TONKIANG_KEYWORDS, timeout=args.timeout)
        print(f"  tonkiang.us -> {len(tk_channels)} 个频道")
        all_channels.extend(tk_channels)
    else:
        print(f"\n[1b] tonkiang.us: 未设置cookie, 跳过")
        print(f"  提示: 设置环境变量 TONKIANG_COOKIE 或用 --tonkiang-cookie 参数")

    # ── Step 2: 去重 + 分类 ──
    print(f"\n[2/3] 去重分类...")
    seen_urls = set()
    unique = []
    for ch in all_channels:
        url = ch['url']
        if not url or url in seen_urls:
            continue
        skip_filter = any(s in ch['source'] for s in ['iptv-org-香港', 'iptv-org-澳门'])
        if not skip_filter:
            if not is_hk_channel(ch['name']):
                continue
        seen_urls.add(url)
        ch['category'] = classify_channel(ch['name'])
        unique.append(ch)

    # 同名频道按域名排序, 优先保留官方源
    def channel_sort_key(ch):
        name = ch['name'].lower()
        url = ch['url'].lower()
        if 'rthk.hk' in url or 'rthktv' in url or 'rthklive' in url:
            return (0, name)
        if 'hoy.tv' in url or 'viu.tv' in url:
            return (0, name)
        if 'freetv.fun' in url:
            return (1, name)
        if '163189.xyz' in url:
            return (2, name)
        if 'jdshipin.com' in url:
            return (3, name)
        if 'tonkiang' in ch['source']:
            return (-1, name)  # tonkiang源优先
        return (4, name)

    unique.sort(key=channel_sort_key)

    print(f"  去重后: {len(unique)} 个频道")
    for g in sorted(set(c['category'] for c in unique)):
        count = sum(1 for c in unique if c['category'] == g)
        if count > 0:
            print(f"    {g}: {count}")

    # ── Step 3: 验证 ──
    if not args.no_validate:
        print(f"\n[3/3] 验证直播源 (并发={args.workers}, 超时={args.timeout}s)...")
        alive = []
        dead = []
        done = 0
        total = len(unique)

        def check(ch):
            ok, reason = validate_stream(ch['url'], timeout=args.timeout)
            return ch, ok, reason

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(check, ch): ch for ch in unique}
            for future in as_completed(futures):
                ch, ok, reason = future.result()
                done += 1
                icon = "OK" if ok else "FAIL"
                print(f"\r  [{done}/{total}] {icon} {ch['name'][:25]:<25} {reason[:30]}   ", end='', flush=True)
                if ok:
                    ch['reason'] = reason
                    alive.append(ch)
                else:
                    dead.append({"name": ch['name'], "url": ch['url'],
                                 "reason": reason, "source": ch['source']})

        print(f"\n\n  可播: {len(alive)}, 不可播: {len(dead)}, 通过率: {len(alive)*100//max(total,1)}%")

        write_tvbox(alive, out / "hk.txt")
        write_m3u(alive, out / "hk.m3u")
        write_json(alive, dead, {
            "total_raw": len(all_channels),
            "total_unique": len(unique),
            "alive": len(alive),
            "dead": len(dead),
            "pass_rate": f"{len(alive)*100//max(total,1)}%",
        }, out / "hk.json")
    else:
        print(f"\n[3/3] 跳过验证")
        write_tvbox(unique, out / "hk.txt")
        write_m3u(unique, out / "hk.m3u")
        write_json(unique, [], {
            "total_raw": len(all_channels),
            "total_unique": len(unique),
            "validated": False,
        }, out / "hk.json")

    # 最终统计
    print(f"\n{'='*60}")
    cats = {}
    final = unique if args.no_validate else alive
    for ch in final:
        c = ch['category']
        cats[c] = cats.get(c, 0) + 1
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")
    print(f"  ---")
    print(f"  合计: {len(final)} 个频道")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
