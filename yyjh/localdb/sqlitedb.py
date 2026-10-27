# -*- coding: utf-8 -*-
"""dat.db 读写 —— 与官方文件**逐项同构**。

官方实测参数（写库时必须对齐，否则游戏/工具的 SQLite 行为会有差异）:
    page_size = 1024      ← Python 默认 4096，必须在建表**之前**设置
    encoding  = UTF-8
    auto_vacuum = 0, user_version = 0, application_id = 0
    journal_mode = delete, synchronous = 2
    表: CREATE TABLE save (id VARCHAR PRIMARY KEY, content VARCHAR, player VARCHAR)
    隐含索引: sqlite_autoindex_save_1
    文件头: 04 00 01 01 00 40 20 20
"""
import os
import sqlite3

PAGE_SIZE = 1024
SCHEMA = ('CREATE TABLE save (id VARCHAR PRIMARY KEY, '
          'content VARCHAR, player VARCHAR)')

PRAGMA_KEYS = ['page_size', 'encoding', 'auto_vacuum', 'user_version',
               'application_id', 'journal_mode', 'synchronous', 'foreign_keys']


def connect(path):
    con = sqlite3.connect(path)
    con.text_factory = str
    return con


def read_save(path):
    """返回 dict(id, content, player)。文件里没有 save 表则抛异常。"""
    con = connect(path)
    try:
        row = con.execute('SELECT id, content, player FROM save LIMIT 1').fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f'{path} 的 save 表里没有数据')
    return {'id': row[0], 'content': row[1], 'player': row[2]}


def read_all(path):
    con = connect(path)
    try:
        rows = con.execute('SELECT id, content, player FROM save').fetchall()
    finally:
        con.close()
    return [{'id': r[0], 'content': r[1], 'player': r[2]} for r in rows]


def tables(path):
    con = connect(path)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    finally:
        con.close()


def pragmas(path):
    con = connect(path)
    out = {}
    try:
        for k in PRAGMA_KEYS:
            try:
                out[k] = con.execute(f'PRAGMA {k}').fetchone()[0]
            except sqlite3.Error:
                out[k] = None
    finally:
        con.close()
    return out


def write_save(path, save_id, content, player, overwrite=True):
    """写出与官方同构的 dat.db。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        if not overwrite:
            raise FileExistsError(path)
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.execute(f'PRAGMA page_size = {PAGE_SIZE}')
        con.execute('PRAGMA auto_vacuum = 0')
        con.execute('PRAGMA encoding = "UTF-8"')
        con.execute(SCHEMA)
        con.execute('INSERT INTO save (id,content,player) VALUES (?,?,?)',
                    (save_id, content, player))
        con.commit()
    finally:
        con.close()
    return path


def structure_diff(path_a, path_b):
    """比较两个 db 的表/索引/库级参数 —— 校验"生成的库"和"官方库"是否同构。"""
    def snap(p):
        con = connect(p)
        try:
            master = con.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
            idx = con.execute('PRAGMA index_list(save)').fetchall()
        finally:
            con.close()
        return {'master': master, 'index_list': idx, 'pragmas': pragmas(p),
                'size': os.path.getsize(p)}

    a, b = snap(path_a), snap(path_b)
    diffs = []
    if a['master'] != b['master']:
        diffs.append(('schema', a['master'], b['master']))
    if [i[1] for i in a['index_list']] != [i[1] for i in b['index_list']]:
        diffs.append(('index', a['index_list'], b['index_list']))
    for k in PRAGMA_KEYS:
        if a['pragmas'][k] != b['pragmas'][k]:
            diffs.append((f'pragma.{k}', a['pragmas'][k], b['pragmas'][k]))
    return diffs
