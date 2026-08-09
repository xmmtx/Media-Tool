"""FileBot 风格表达式求值器（Expression Engine）。

表达式形如 ``{title_orig} ({year}) - {title_user} - [{group} {resolution}]``，
其中 ``{...}`` 为占位 token。支持：

- **Token 求值**: 把 ``{name}`` 替换为数据字典中的值，缺失字段按空字符串处理。
- **补零**: 后缀 ``_2d``（如 ``{season_2d}``、``{episode_2d}``）表示数字不足
  2 位补零；后缀可通过 ``_3d``、``_4d`` 扩展。
- **跳过检查**: ``required_fields()`` 返回表达式实际需要的字段集合，调用方可
  据此跳过未使用字段对应的提取/API 步骤（性能优化，见 PROJECT_PROMPT）。
- **清理**: 求值后折叠空白、移除空 ``[]``/``()`` 片段、合并/修剪重复分隔符。

模块为纯逻辑、零 IO、零外部依赖，便于单元测试。
"""

from __future__ import annotations

import re
from typing import Dict, Set

# 形如 {name} 或 {name_2d}
_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# 补零后缀: _2d / _3d / _4d ...
_PAD_RE = re.compile(r"_(\d)d$")

# 已知 token 集合（用于 find_unknown() 给 UI 提示；不影响求值行为）
KNOWN_TOKENS: Set[str] = {
    "title", "title_orig", "title_user", "year",
    "season", "episode", "group", "resolution",
    "quality", "codec", "audio", "container", "size",
    "name", "folder", "ext",
}


def parse_tokens(fmt: str) -> Dict[str, str]:
    """解析表达式中的所有 token，返回 ``{token名: 补零后缀}``。

    例::

        parse_tokens("{title_orig} S{season_2d}E{episode_2d} [{group} {resolution}]")
        # -> {'title_orig': '', 'season': '2d', 'episode': '2d',
        #     'group': '', 'resolution': ''}
    """
    result: Dict[str, str] = {}
    for m in _TOKEN_RE.finditer(fmt):
        raw = m.group(1)
        pm = _PAD_RE.search(raw)
        if pm:
            result[raw[: pm.start()]] = raw[pm.start() + 1:]  # 去掉 "_" 前缀, 如 "2d"
        else:
            result.setdefault(raw, "")
    return result


def required_fields(fmt: str) -> Set[str]:
    """返回表达式实际需要的数据字段（去重、去掉补零后缀）。

    调用方依据该集合决定要执行哪些提取/API 步骤：例如不含 ``resolution``
    就不跑 ffprobe，不含 ``group`` 就不做字幕组识别。
    """
    return set(parse_tokens(fmt))


def find_unknown(fmt: str) -> Set[str]:
    """返回表达式里不在 ``KNOWN_TOKENS`` 中的 token 名（用于 UI 提示/校验）。"""
    return required_fields(fmt) - KNOWN_TOKENS


def format_value(value: object, pad: str = "") -> str:
    """把单个 token 值渲染为字符串。

    - ``pad="2d"``: 数字不足 2 位补零（``1 -> "01"``）；已是 2 位及以上则不补。
    - ``None``: 返回空字符串。
    - 非数字值（如组名/标题）忽略补零，原样输出。
    """
    if value is None:
        return ""
    if pad:
        try:
            return str(int(value)).zfill(int(pad[0]))
        except (TypeError, ValueError):
            pass
    return str(value)


def evaluate(fmt: str, values: Dict[str, object], cleanup: bool = True) -> str:
    """对表达式求值，返回生成的（可选清理后的）字符串。

    - 缺失字段按空字符串处理。
    - ``cleanup=True`` 时执行 ``_cleanup()``：折叠空白、移除空 ``[]``/``()``
      片段、合并重复 `` - ``、修剪首尾分隔符。
    - 若结果为空字符串，由调用方决定回退策略（如使用原文件名）。
    """
    tokens = parse_tokens(fmt)
    out = fmt
    # 先替换带补零后缀的 token（占位符更具体），再替换普通 token，避免误伤
    for name, pad in sorted(tokens.items(), key=lambda kv: -len(kv[1])):
        placeholder = "{%s_%s}" % (name, pad) if pad else "{%s}" % name
        out = out.replace(placeholder, format_value(values.get(name), pad))
    return _cleanup(out) if cleanup else out


def _cleanup(s: str) -> str:
    """轻量清理：折叠空白、移除空括号片段、合并/修剪重复分隔符。"""
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\[\s*\]", "", s)          # 空 [ ]
    s = re.sub(r"\(\s*\)", "", s)          # 空 ( )
    s = re.sub(r"\[\s+", "[", s)           # [ 后不留空白
    s = re.sub(r"\s+\]", "]", s)           # ] 前不留空白
    s = re.sub(r"\(\s+", "(", s)           # ( 后不留空白
    s = re.sub(r"\s+\)", ")", s)           # ) 前不留空白
    s = s.replace(" -  - ", " - ")
    s = re.sub(r"\s*-\s*$", "", s)         # 尾部 -
    s = re.sub(r"^\s*-\s*", "", s)         # 头部 -
    s = re.sub(r"\s+", " ", s)
    return s.strip()
