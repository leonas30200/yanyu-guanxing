# -*- coding: utf-8 -*-
"""快速扫描 HAR：列出存档相关请求、平台、版本、响应头关键字段。"""
import json
import sys
import re
from collections import Counter


def txt_of(entry):
    c = entry.get('response', {}).get('content', {})
    t = c.get('text')
    if t is None:
        return ''
    if c.get('encoding') == 'base64':
        import base64
        try:
            return base64.b64decode(t).decode('utf-8', 'replace')
        except Exception:
            return ''
    return t


def main(path):
    har = json.load(open(path, 'r', encoding='utf-8'))
    ents = har['log']['entries']
    print('entries:', len(ents))
    hosts = Counter()
    for e in ents:
        u = e['request']['url']
        m = re.match(r'https?://([^/]+)', u)
        if m:
            hosts[m.group(1)] += 1
    print('hosts:', hosts.most_common(12))
    print('---- 存档相关请求 ----')
    for i, e in enumerate(ents):
        u = e['request']['url']
        if not re.search(r'(save|arch|dat|user|game|check|login|start|role|update|upload|download)', u, re.I):
            continue
        req = e['request']
        hdrs = {h['name'].lower(): h['value'] for h in req.get('headers', [])}
        resp = e['response']
        rhdrs = {h['name'].lower(): h['value'] for h in resp.get('headers', [])}
        post = (req.get('postData') or {}).get('text', '')
        body = txt_of(e)
        tags = []
        for k in ('plat', 'k', 'plaintext', 'gameversion', 'ver', 'version', 'channel', 'sid', 'account'):
            if k in hdrs:
                tags.append('req.%s=%s' % (k, hdrs[k]))
            if k in rhdrs:
                tags.append('res.%s=%s' % (k, rhdrs[k]))
        print('[%d] %s %s' % (i, req['method'], u[:150]))
        if tags:
            print('     ', ' | '.join(tags))
        if post:
            print('      POST:', post[:300].replace('\n', ' '))
        if body:
            print('      RESP:', body[:300].replace('\n', ' '))
    print('---- 含 URL 内 key 字段的请求 ----')
    for i, e in enumerate(ents):
        u = e['request']['url']
        if 'upload3' in u or 'download' in u or 'save' in u.lower():
            print('[%d]' % i, u[:220])


if __name__ == '__main__':
    main(sys.argv[1])
