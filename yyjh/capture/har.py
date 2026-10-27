# -*- coding: utf-8 -*-
"""HAR 读取 —— 只负责"把抓包文件变成好用的对象"，不做任何解密。"""
import json
import os
import re

# 上传存档的命令名。**必须精确匹配**：
#   以前用的是 `cmd.startswith('upload')`，于是 `uploadPower`（内力）/ `uploadLocation`
#   （坐标）也被当成了"上传存档"，候选里混进两三个假货 —— 虽然 `largest_upload`
#   取最大体通常还是对的，但 `--index` 的序号、概览列表、批量报告全都会错位。
#   这里用正则整串匹配，同时兼容以后可能出现的 upload4 / upload_auto4。
UPLOAD_CMD_RE = re.compile(r'^(?:upload|save)(?:_auto)?\d*$', re.I)

# 老名字留着，给外部代码/测试兼容（内容已改成上面的正则语义）
UPLOAD_CMDS = ('upload', 'upload2', 'upload3', 'upload_auto', 'upload_auto2',
               'upload_auto3', 'save')


def is_upload_cmd(cmd):
    """`upload` / `upload2` / `upload3` / `upload_auto3` / `save` 算，
    `uploadPower` / `uploadLocation` **不算**。"""
    return bool(UPLOAD_CMD_RE.match((cmd or '').strip()))


class Har:
    def __init__(self, path):
        self.path = path
        with open(path, encoding='utf-8') as f:
            self.raw = json.load(f)
        try:
            self.entries = self.raw['log']['entries']
        except (KeyError, TypeError):
            raise ValueError('不是合法的 HAR 文件（缺 log.entries）')

    # ── 小工具 ──
    @staticmethod
    def header(entry, name, where='request'):
        for h in (entry.get(where) or {}).get('headers', []) or []:
            if (h.get('name') or '').lower() == name.lower():
                return (h.get('value') or '').strip()
        return None

    @staticmethod
    def body(entry):
        """请求体文本；若声明了 base64 编码则自动解码成 bytes。"""
        import base64
        pd = (entry.get('request') or {}).get('postData') or {}
        txt = pd.get('text')
        if txt is None:
            return None
        if (pd.get('encoding') or '').lower() == 'base64':
            try:
                return base64.b64decode(txt)
            except Exception:
                return txt
        return txt.strip()

    def body_len(self, entry):
        b = self.body(entry)
        return len(b) if b else 0

    # ── 查询 ──
    def api_entries(self, keyword='api_command'):
        return [e for e in self.entries
                if keyword in ((e.get('request') or {}).get('url') or '')]

    def uploads(self):
        out = []
        for e in self.entries:
            if is_upload_cmd(self.header(e, 'command')):
                out.append(e)
        return out

    def largest_upload(self):
        """存档上传的请求体最大；内力/坐标上传都很小。"""
        ups = self.uploads()
        if not ups:
            return None
        return max(ups, key=self.body_len)

    def key_source(self):
        """找到带 k 响应头的那条（通常是登录或 startGame 的响应）。"""
        for e in self.entries:
            if self.header(e, 'k', 'response'):
                return e
        return None

    def k_header(self):
        e = self.key_source()
        return self.header(e, 'k', 'response') if e else None

    def account(self, entry=None):
        e = entry or self.largest_upload()
        return self.header(e, 'acc') if e else None

    def server(self, entry=None):
        e = entry or self.largest_upload()
        return self.header(e, 'area') if e else None

    def overview(self):
        return {
            'file': os.path.basename(self.path),
            'entries': len(self.entries),
            'api_entries': len(self.api_entries()),
            'uploads': [(i, self.header(e, 'command'), self.header(e, 'acc'),
                         self.header(e, 'ver'), self.header(e, 'area'),
                         self.body_len(e))
                        for i, e in enumerate(self.uploads())],
            'has_k': bool(self.k_header()),
        }
