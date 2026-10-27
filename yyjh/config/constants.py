# -*- coding: utf-8 -*-
"""算法常量 —— 全部来自对游戏二进制的逆向结果，集中在此便于更新时统一替换。

逆向依据（更新后重新确认时看这里）:
  libcocos2dlua.so
    LuaStack::loadCai            @0x650C94  -> 签名 "BIGDA"/"KEIIY" + m_pXXTEAKey
    ResourceGroup::decryptPlistFile @0xBBE4DC -> ROL3 变换
    _spUtil_readFileXXTea        @0x9708E8  -> 5 字节魔术字 + XXTEA(10 字符 key)
  extracted/ 里的 smali + assets/web/shared/save_decrypt.js
    ProtocolEnvelope.key(String, byte[])     -> key0 / realKey 拼法
    H5 存档的 YANYU201 容器（PBKDF2 + AES-GCM）
"""

# ───────────────────────── XXTEA ─────────────────────────
XXTEA_DELTA = 0x9E3779B9
MASK32 = 0xFFFFFFFF
# 密文长度 n 个字时，末字（明文长度）必须落在 [n*4-7, n*4-4]
# 随机猜中概率 4/2^32 ≈ 1e-9  —— 所有"爆破钥匙"都靠它当预言机
XXTEA_LEN_TAIL = 7
XXTEA_LEN_HEAD = 4

# ──────────────────── .whale / res_encrypt ──────────────
# 模块签名 -> 循环 XOR 的密钥
RES_SIGN_XOR = {
    b'BIGDA': b'JJyyt973',
    b'KEIIY': b'NanShuGo',
}
# 两者的 XXTEA 密钥都是它（LuaStack::m_pXXTEAKey）
CACHE_XXTEA_KEY = b'LanJingNo1'
# 解密后 lua 字节码魔术字
LUA_MAGIC = b'\x1b\x73\x62'          # "\x1bsb"
# 通用文件约定：5 字节魔术字 + XXTEA(data[5:], 10 字符 key)
GENERIC_FILE_MAGIC_LEN = 5
GENERIC_FILE_KEY_LEN = 10

# .whale 容器头: [u32 data_end][u16 module_count][u16 version]
WHALE_HEADER = '<IHH'

# .whale 模块密文是 4 字节对齐的，自然边界有时会多出 4/8 字节，
# 用长度预言机收缩搜索（单位：4 字节）
WHALE_MAX_SHRINK_WORDS = 12

# ─────────────────────── 存档格式 ───────────────────────
# 新版: marker(8) + base64( XXTEA( base64(版本号(8) + XXTEA(zlib(json), 内层密钥)), 外层密钥 ) )
# 初版:              base64(              版本号(8) + XXTEA(zlib(json), 内层密钥) )
SAVE_MARKER_LEN = 8
SAVE_VERSION_LEN = 8

# 外层标记 -> 可能对应的版本号（同一标记在不同版本复用）
SAVE_MARKER_MAP = {
    'kjie00o0': ['00072280'],
    'js9qf8me': ['00072288'],
    'nfidfug8': ['00072294', '00073288'],
    'tsidfug8': ['00073288'],
    'cdexdmk5': ['00073302'],
    'dnkemiea': ['00073314', '00073420', '00073440'],   # 当前版本用的固定魔数
}

# 兜底用的外层密钥：解不开时才会试到它。
# 一般留空 —— 密钥会自动记到 keys.json，不用写死在这里。
DEFAULT_OUTER_KEY = ''

# ───────────────────── 协议（抓包） ─────────────────────
PROTOCOL_MIDDLE = 'eakec'        # realKey = acc[:5] + "eakec" + innerK[:6]
PROTOCOL_SEED_ZERO = '000000'
PROTOCOL_KEY_LEN = 16
PROTOCOL_INNER_MIN = 1
PROTOCOL_INNER_MAX = 128

# ────────────── v1.8 存档：固定平台密钥（单层） ──────────────
# 「烟雨江湖 · 本地存档工具」（以及解析网站）认的 v1.8 格式：
#
#     content = base64( 版本号(8) + XXTEA( zlib(json), 平台固定密钥 ) )
#
# 和"初版"**同一个形状**，只是内层密钥换成下面这几把**固定的**。
# 它不靠版本号推导密钥，工具是拿来一把一把试的（见 save_decrypt.js
# 的 decryptV18SaveContent / SAVE_V18_PLATFORM_KEYS）。
#
# 注意：这是**历史**密钥体系（游戏那时就这么存）；现在的游戏已经改成
# 按版本推导的内层密钥 + 每账号一把的外层密钥。
V18_PLATFORM_KEYS = {
    'ios': '3598gjjdtekelaw9',
    'android': '3598gjjdtekelad9',
    'pre_ios': '3598gjjdbeceead3',
    'pre_android': '3598gjjdbeceead3',
    'older': '3598gjjdchdidqz2',
    'oversea': '3598gjjdaecjjyc4',
    'korea': '3598gjjdd3tczm6d',
    'japan': '3598gjjdtekelad9',
    'test': 'aaaabbbbccccdddd',
    'windows': 'windows0',
}
V18_DEFAULT_PLATFORM = 'android'

# ─────────────────────── 解包 / 逆向 ────────────────────
# 游戏 APK 的 native 库（安卓）
SO_MAIN = 'libcocos2dlua.so'
SO_APP = 'libapp.so'

# libcocos2dlua.so 里的关键函数（基址 0x400023000000 + 这些偏移）
KNOWN_SYMS = {
    0x729218: 'xxtea_decrypt',
    0x72915C: 'xxtea_encrypt',
    0x626764: 'JHCrypto::decryptXXTEA',
    0x626C7C: 'JHCrypto::decryptXXTEATS',
    0x626BDC: 'JHCrypto::decryptXXTEALua',
    0x623500: 'JHCommonUtils::getFileData',
    0x650C94: 'LuaStack::loadCai',
    0xBBE4DC: 'ResourceGroup::decryptPlistFile',
    0x9708E8: '_spUtil_readFileXXTea',
    0x62AC94: 'JHLuaEngine::getInstance',
    0x627730: 'JHCrypto::decodeEasyStringLua',
}

# exe / APK 里用来提取密钥表的关键字（按需扩充）
KEY_TABLE_HINT = b'npswsuffix'
