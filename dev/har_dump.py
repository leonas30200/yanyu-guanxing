# -*- coding: utf-8 -*-
"""完整 dump HAR 每个条目的 URL / 头 / 请求体 / 响应体（截断）。"""
import json
import sys
import base64


def txt_of(entry, limit=None):
    c = entry.get('response', {}).get('content', {})
    t = c.get('text') or ''
    if c.get('encoding') == 'base64':
        try:
            t = base64.b64decode(t).decode('utf-8', 'replace')
        except Exception:
            try:
                t = repr(base64.b64decode(t)[:200])
            except Exception:
                t = ''
    return t[:limit] if limit else t


def main(path, limit=400):
    har = json.load(open(path, 'r', encoding='utf-8'))
    for i, e in enumerate(har['log']['entries']):
        req, resp = e['request'], e['response']
        print('=' * 90)
        print('[%d] %s %s' % (i, req['method'], req['url']))
        print('-- req headers:')
        for h in req.get('headers', []):
            print('   %s: %s' % (h['name'], h['value'][:200]))
        post = (req.get('postData') or {}).get('text', '')
        if post:
            print('-- req body (%d):' % len(post), post[:limit].replace('\n', ' '))
        print('-- resp status:', resp.get('status'))
        for h in resp.get('headers', []):
            print('   %s: %s' % (h['name'], h['value'][:200]))
        b = txt_of(e, limit)
        if b:
            print('-- resp body (%d):' % len(txt_of(e)), b.replace('\n', ' '))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 400)
