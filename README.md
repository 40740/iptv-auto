# 港澳台 IPTV 自动抓取 + 真实验证

自动从 GitHub 公开仓库抓取港澳台直播源，通过三级流验证确保可播，输出 TVBox/APTV 兼容格式。

## 特性

- **17个数据源**: sammy0101、iptv-org、Free-TV、MercuryZz、Joker-Cold、imDazui、Guovin、Kimentanm 等
- **tonkiang.us 支持**: 通过 cookie 方式抓取 (需手动设置)
- **三级流验证**: HTTP连通 → 内容检测 → MPEG-TS分片验证 (0x47同步字节)
- **自动分类**: RTHK、TVB翡翠台、ViuTV、HOY TV、凤凰卫视、台湾各台等
- **GitHub Actions**: 每天北京时间 8:10 自动运行
- **输出格式**: TVBox txt / 标准 M3U / JSON (含验证详情)

## 使用方法

### 直接使用
下载  导入 TVBox / APTV 即可。

### tonkiang.us 数据源
tonkiang.us 有 reCAPTCHA + Cloudflare 防护，需要手动获取 cookie:

1. 在浏览器打开 https://www.tonkiang.us/ 并通过验证
2. F12 → Application → Cookies → 复制  值
3. 设置环境变量:
   ```bash
   export TONKIANG_COOKIE="cf_clearance=你的值"
   ```
4. 运行爬虫，tonkiang 数据源会自动启用

**注意**: cf_clearance 绑定 IP 地址，需在同一网络下使用。

### 本地运行
```bash
python scraper.py                         # 完整验证
python scraper.py --no-validate           # 跳过验证 (快速)
python scraper.py --timeout 8 --workers 50
```

## 自动更新
GitHub Actions 每天北京时间 8:10 自动运行，结果保存在 `output/` 目录。

手动触发: 仓库 → Actions → daily-iptv → Run workflow

## 频道分类
| 分组 | 说明 |
|------|------|
| 港台RTHK | RTHK 31/32/33/34/35 |
| TVB翡翠台 | 翡翠台/无线新闻 |
| ViuTV | ViuTV 96A/99 |
| HOY TV | HOY TV 76/77/78 |
| 凤凰卫视 | 凤凰中文/资讯/香港 |
| TVBS/中天/东森 | 台湾主要频道 |
| 其他 | 澳门TDM/有线/Now等 |

## License
MIT
