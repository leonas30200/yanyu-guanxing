# -*- coding: utf-8 -*-
"""跨层共享的数据类 —— 让各层不互相 import，避免过耦合。"""
from dataclasses import dataclass, field


@dataclass
class Step:
    """一条流水线日志，报告里会按顺序列出。"""
    label: str
    value: str = ''
    ok: bool = True


@dataclass
class SaveResult:
    """一条存档（不管来自抓包 / dat.db）的统一结果。"""
    source: str = ''          # 输入文件
    kind: str = ''            # har | db | yanyu | json
    runtime: str = ''         # android | ios

    account: str = ''         # 账号（安卓是 32 位 hex，iOS 是邮箱）
    save_id: str = ''         # save 表 id = md5(账号)
    server: str = ''          # 服号

    version: str = ''         # 存档版本号，如 00073440
    marker: str = ''          # 外层标记（新版才有）
    outer_key: str = ''       # 每账号一把
    inner_key: str = ''       # 版本+渠道
    save_format: str = ''     # 输出 content 用的是哪种：v18 | current

    content: str = ''         # **初版格式** content（可直接写进 dat.db）
    content_new: str = ''     # 新版格式 content（带标记 + 外层加密）

    data: dict = field(default_factory=dict)   # 解密后的存档 JSON
    player: str = ''          # save.player 明文摘要

    steps: list = field(default_factory=list)      # [Step]
    warnings: list = field(default_factory=list)   # [str]

    # ── 便捷属性 ──
    @property
    def nick(self):
        return ((self.data.get('MainRole') or {}).get('Name')) or ''

    @property
    def field_count(self):
        return len(self.data)

    def step(self, label, value='', ok=True):
        self.steps.append(Step(label, str(value), ok))
        return self

    def warn(self, msg):
        self.warnings.append(str(msg))
        return self

    def as_dict(self):
        """给 JSON 输出用的精简视图（不含完整存档）。"""
        return {
            'source': self.source,
            'kind': self.kind,
            'runtime': self.runtime,
            'account': self.account,
            'save_id': self.save_id,
            'server': self.server,
            'version': self.version,
            'marker': self.marker,
            'outer_key': self.outer_key,
            'inner_key': self.inner_key,
            'save_format': self.save_format,
            'nick': self.nick,
            'field_count': self.field_count,
            'warnings': self.warnings,
        }
