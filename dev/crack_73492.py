# -*- coding: utf-8 -*-
"""73492 存档全解链（通用）

用法: crack_73492.py <har路径> [账号] [渠道后缀]
渠道后缀默认 yoqkmrtj (iOS)。安卓常见: po06ewkx / tekelad9
"""
import sys, os, json, zlib, datetime

sys.path.insert(0, r'I:/CODE/learn/yanyu-guanxing-main-20261022')
from yyjh.core import xxtea, codecs
from yyjh.game import protocol
from yyjh.capture.har import Har


def pick_k(h):
    """取响应头 k（api_check_user / startGame 都会下发）"""
    for e in h.entries:
        v = h.header(e, 'k', 'response')
        if v:
            return v.strip(), h.header(e, 'command') or e['request'].get('url', '')[:60]
    return None, None


def pick_download(h):
    """取 command=download 的响应 JSON"""
    for e in h.entries:
        if (h.header(e, 'command') or '').strip() == 'download':
            return json.loads(e['response']['content']['text'])
    return None


def run(har_path, account=None, suffix=b'yoqkmrtj'):
    h = Har(har_path)
    if not account:
        for e in h.entries:
            a = h.header(e, 'acc')
            if a:
                account = a.strip()
                break
    k, src = pick_k(h)
    if not k:
        raise SystemExit('没找到 k 响应头（必须从"打开游戏之前"开始抓包）')
    d = protocol.derive(account, k)
    real = d['real_key'].encode()
    print('account =', account)
    print('k 头    =', k, '(来自 %s)' % src)
    print('key0    =', d['key0'])
    print('innerK  =', d['inner_k'])
    print('realKey =', d['real_key'])

    r = pick_download(h)
    if not r:
        raise SystemExit('没找到 download 响应')
    psw_b = codecs.b64d(r['extra'])
    psw = xxtea.decrypt(psw_b[:len(psw_b) // 4 * 4], real)
    save_code = xxtea.decrypt(codecs.b64d(r['saveCode'])[:12], real)
    print('saveCode ->', save_code)
    print('extra    -> psw =', psw)
    psw = psw.strip(b'\0').decode()
    inner = (psw + suffix.decode()).encode()
    print('innerKey =', inner.decode())

    raw = codecs.b64d(r['data'])
    body = raw[8:]                      # 前 8 字节 '00073492' 是版本标记
    body = body[:len(body) // 4 * 4]
    p = xxtea.decrypt(body, inner)
    if not p:
        raise SystemExit('存档解密失败')
    out = zlib.decompress(p)
    data = json.loads(out.decode('utf-8'))
    print('解压 %d 字节, 顶层字段 %d' % (len(out), len(data)))
    print('nick =', data['MainRole'].get('Name'),
          ' level =', data['MainRole'].get('Level'),
          ' 时间 =', datetime.datetime.fromtimestamp(data['RealTime']))
    return data, inner.decode(), d['real_key']


if __name__ == '__main__':
    har = sys.argv[1]
    acc = sys.argv[2] if len(sys.argv) > 2 else None
    suf = sys.argv[3].encode() if len(sys.argv) > 3 else b'yoqkmrtj'
    data, inner, real = run(har, acc, suf)
    dst = sys.argv[4] if len(sys.argv) > 4 else 'cracked_%s.json' % datetime.datetime.now().strftime('%H%M%S')
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print('已写出', dst)
