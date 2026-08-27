# -*- coding: utf-8 -*-
"""把台南心理衛生資源的四個來源抓成本地快照。

為什麼要快照而不是現場即時抓：
  明天現場約 12 位學員、同一個 IP、同一時間。
  一起打政府網站有觸發速率限制的風險，而且診所 WiFi 品質不明。
  → repo 裡放快照＋抓取時間戳，MCP 預設讀本地，
    只留一支工具走線上，用來示範「資料是活的」。

反爬紀律：序列不並發，每次請求之間喘 1.5 秒。

所有網址都是 2026-08-27 經過對抗性查核驗證過的（100 筆通過、22 筆剔除）。
"""
import io
import os
import re
import sys
import json
import time
import subprocess
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

H = 'https://health.tainan.gov.tw'

SOURCES = [
    # (代號, 網址, 說明, 預期內容的驗證關鍵字)
    ('map_kml',
     'https://www.google.com/maps/d/kml?mid=1YhZw1eQCaI3gtDb_E8WBO1XP_-E&forcekml=1',
     '臺南市心理健康資源地圖（KML，141 個標點、4 圖層）',
     '心理健康資源地圖'),

    ('roster',
     H + '/page.asp?mainid=0A3943E7-033E-4686-8514-7BD39368D32D',
     '心理諮商所、心理治療所名冊（治療所 12＋諮商所 20）',
     '心理治療所'),

    ('free_public',
     H + '/page.asp?mainid=586CAFE0-5DA5-49E6-831F-1D54499B808C',
     '免付費心理諮商－公部門地點（40 點）',
     '衛生所'),

    ('free_private',
     H + '/page.asp?mainid=499FD2AD-07B9-4A27-AA13-E0397489855A',
     '免付費心理諮商－需場地費地點（22 家民間，含寬欣）',
     '場地費'),

    ('free_howto',
     H + '/page.asp?mainid=62EF11E9-15AB-4E7F-B50D-B96EF2742503',
     '免付費心理諮商－預約方式',
     '預約'),

    ('centers',
     H + '/page.asp?mainid=%7B6048F242-90E2-4CC2-9DAB-45D4CCB2ACAD%7D',
     '社區心理衛生中心據點（6 處，服務區域涵蓋 37 區）',
     '社區心理衛生中心'),

    ('free_csv',
     'https://data.tainan.gov.tw/File/ResourceCsvDownload/351ee754-b3ca-4efc-bdc0-5ecb4c0ec57b',
     '免費心理諮商服務據點 CSV（43 筆，含行政區代碼）',
     None),

    ('area_csv',
     'https://data.tainan.gov.tw/File/ResourceCsvDownload/5b389f60-dee6-425a-8a40-f5cb2f949cf1',
     '臺南市各區代碼 CSV（37 區）',
     None),
]


def fetch(url, timeout=60):
    r = subprocess.run(
        ['curl', '-sL', '--max-time', str(timeout), '-A', UA, url],
        capture_output=True, timeout=timeout + 20)
    return r.stdout


def main():
    os.makedirs(DATA, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    manifest = {'fetched_at': stamp, 'sources': []}

    print('  抓取時間：%s' % stamp)
    print('  存放位置：%s' % DATA)
    print()

    for key, url, desc, must in SOURCES:
        time.sleep(1.5)                      # 反爬：序列、有間隔
        raw = fetch(url)
        ext = '.xml' if key.endswith('kml') else (
            '.csv' if key.endswith('csv') else '.html')
        path = os.path.join(DATA, key + ext)

        ok = bool(raw) and len(raw) > 500
        text = ''
        if ok:
            for enc in ('utf-8', 'utf-8-sig', 'big5', 'cp950'):
                try:
                    text = raw.decode(enc)
                    break
                except Exception:
                    continue
            if must and must not in text:
                ok = False

        if ok:
            open(path, 'wb').write(raw)

        manifest['sources'].append({
            'key': key, 'url': url, 'desc': desc,
            'file': os.path.basename(path) if ok else None,
            'bytes': len(raw) if raw else 0,
            'ok': ok,
        })
        print('  %s %-14s %8s bytes  %s'
              % ('✓' if ok else '✗', key, len(raw) if raw else 0, desc))
        if not ok and must:
            print('       ⚠ 內容裡找不到「%s」，可能被擋或改版了' % must)

    json.dump(manifest, open(os.path.join(DATA, '_manifest.json'), 'w',
                             encoding='utf-8'),
              ensure_ascii=False, indent=1)

    good = sum(1 for s in manifest['sources'] if s['ok'])
    print()
    print('  完成：%d / %d 個來源' % (good, len(SOURCES)))
    print('  清單：data/_manifest.json')


if __name__ == '__main__':
    main()
