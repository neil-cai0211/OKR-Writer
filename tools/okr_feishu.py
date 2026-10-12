#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OKR 飞书读写封装（零依赖，只包一层 lark-cli）。

设计原则：
1. **不碰凭据**。app_id / app_secret / token 全部由 lark-cli 自己管理，本脚本既不读也不存。
2. **写操作默认只预演**。预演会打印目标、完整内容和内容 SHA-256，供本人核对；加 --execute 才真正写入。
3. **写入固定用本人身份**（--as user），读他人时才可能用 bot。
4. **读不到就说读不到**。命令失败原样抛错，不返回编造数据。
5. **不冒充真人批准**。本脚本没有独立的人确认门禁，--execute 只表示"执行这条命令"，
   不代表"使用者本人已批准"；确认流程由宿主 AI 助手按技能规则执行，并需要如实告知使用者。

前置条件：已装 lark-cli 并完成授权，见 docs/02-连接飞书.md。
参数按 lark-cli 1.0.96 的 --help 编写（该版本是**当初核对参数时的版本**，
不代表本机默认命中的路径就是这个版本，见 check-cli 子命令）；换版本前先核对 --help。

用法示例：
    python okr_feishu.py check-cli
    python okr_feishu.py whoami
    python okr_feishu.py find-user "张三"
    python okr_feishu.py cycles --user-id ou_xxx
    python okr_feishu.py okrs --cycle-id 7000000000000000001
    python okr_feishu.py categories
    python okr_feishu.py create --cycle-id 7000000000000000001 --input-file okr.json          # 默认预演
    python okr_feishu.py create --cycle-id 7000000000000000001 --input-file okr.json --execute
    python okr_feishu.py get-content --level key-result --target-id 7001# 读当前正文+指纹
    python okr_feishu.py update-content --level key-result --target-id 7001 --content "改后的 KR"
    python okr_feishu.py update-content --level key-result --target-id 7001 --content "改后的 KR" \
        --expect-current-fp 9ec17db282bf7146 --execute                                         # 写前复核后写入
    python okr_feishu.py patch-notes --target-id 7002 --notes "口径说明"
    python okr_feishu.py progress --target-type key-result --target-id 7003 --content "本周进展"
    python okr_feishu.py comment-list --target-type key-result --target-id 7003
    python okr_feishu.py comment-create --target-type objective --target-id 7002 --select-all --content "建议收敛表述"
    python okr_feishu.py comment-create --target-type key-result --target-id 7003 --selected-text "600ms" --content "口径建议写清统计范围"
    python okr_feishu.py comment-create --target-type cycle --target-id 7000000000000000001 --content "本周期整体提醒"
    python okr_feishu.py notify --user-id ou_xxx --text "请查收"

改本人已有 O/KR 正文/备注的执行模式下，必须先用 get-content 读当前内容拿到指纹，
再带 --expect-current-fp 执行：确认之后、落笔之前远端若已变化，本脚本会中止，不盲目覆盖。

本机有多个 lark-cli 时（实测 npm 全局 1.0.96、PATH 上 1.0.97），
用 check-cli 子命令看本脚本**实际**会调用哪条；也可以设环境变量 OKR_LARK_CLI 钉死路径。
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 本项目核对过**参数形状**的 CLI 版本。命令参数以实际 --help 为准，换版本先核对。
# 这是一个**声明常量**，不是运行时保证：脚本不会因为版本不符就自动停下，
# 所以下面的 check_cli_version() 只在需要时给出提醒，由使用者决定是否继续。
#
# ⚠️ 这个版本号对应的是**当初据以核对参数的那条路径**，不代表本机现在默认命中的
# 那条路径也是这个版本（find_cli 是 PATH 优先，见该函数说明）。
# 要确认"这台机器上实际会用哪个版本"，跑 `check_cli` 子命令看它打印的路径与版本。
SUPPORTED_CLI_VERSION = "1.0.96"


def check_cli_version(exe=None):
    """尽力读出实际会用到的 lark-cli 版本，供使用者/助手核对。

    返回 (version_or_None, note)。读不到不报错——本函数只是提醒，
    不能因为读不到版本就把正常流程卡死。

    ⚠️ 本机可能存在**多个** lark-cli（实测 npm 全局 1.0.96 与 PATH 上的 1.0.97
    两个）。find_cli() 默认 **PATH 优先**，所以实际命中的通常是 PATH 上那一个。
    本函数返回的 note 里会写明**这次实际命中的路径**，核对版本只认这个路径的结果，
    不要拿另一条显式路径的输出来替整套封装声明版本。
    """
    try:
        path = exe or find_cli()
    except CliError as e:
        return None, str(e).splitlines()[0]
    try:
        p = subprocess.run([path, "--version"], capture_output=True, timeout=30)
    except Exception as e:  # 超时/权限等一律不当成版本不符
        return None, "读取版本失败（%s），不影响继续。" % type(e).__name__
    out = (p.stdout or b"").decode("utf-8", "replace") + \
          (p.stderr or b"").decode("utf-8", "replace")
    # 只认第一行里 "lark-cli version X.Y.Z" 的那个版本号。
    # 不能全输出扫 token —— 升级提示里也会出现 latest 版本号（例如
    # "current 1.0.96, latest 1.0.97"），扫全量会把 latest 当成当前版本。
    first = out.strip().splitlines()[0] if out.strip() else ""
    marker = "version"
    if marker in first:
        tail = first.split(marker, 1)[1].strip()
        token = tail.split()[0] if tail.split() else ""
        if token.startswith("1.") and token[1:].replace(".", "").isdigit():
            return token, ("实际使用路径：%s；版本 %s。本项目核对过的版本是 %s。"
                           % (path, token, SUPPORTED_CLI_VERSION))
    return None, "无法从 --version 输出中解析版本号（原始输出：%s）。" % first[:120]

# 代理环境变量。默认**保留**使用者自己的代理设置；只有显式加 --no-proxy 才剔除。
PROXY_VARS = (
    "http_proxy", "https_proxy", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
)

LARK_CLI_CANDIDATES = [
    r"C:\Users\%s\AppData\Roaming\npm\node_modules\@larksuite\cli\bin\lark-cli.exe",
    "/usr/local/bin/lark-cli",
    "/opt/homebrew/bin/lark-cli",
]

# 允许调用方把 lark-cli 的路径钉死（设置环境变量 OKR_LARK_CLI）。
# 多版本共存时，用它可以明确指定用哪一条，避免"同一台机器两个版本、说不清实际用了哪个"。
CLI_PATH_ENV = "OKR_LARK_CLI"


class CliError(RuntimeError):
    pass


def find_cli():
    """定位 lark-cli 可执行文件。找不到就给出明确的安装指引。

    **实际查找顺序（如实说明，不要当成"只用固定路径"）**：

      1. 环境变量 ``OKR_LARK_CLI`` —— 显式指定，优先级最高。
         指向的文件不存在时**直接报错**，不静默回退，避免以为钉死了其实没钉死。
      2. ``PATH`` 上的 ``lark-cli``（``shutil.which``）—— **PATH 优先于固定候选路径**。
      3. ``LARK_CLI_CANDIDATES`` 里按顺序探测的确定路径（npm 全局安装目录等）。

    ⚠️ 本机实测同时存在两个 lark-cli（npm 全局 1.0.96、PATH 上 1.0.97）。
    因为第 2 步优先，**默认命中的通常是 PATH 上那一个**。
    所以判断"实际用了哪个版本"，必须看 ``check_cli_version()`` 打印的
    「实际使用路径 + 版本」，不能拿另一条路径的版本号来推断。
    """
    pinned = (os.environ.get(CLI_PATH_ENV) or "").strip()
    if pinned:
        if Path(pinned).exists():
            return pinned
        raise CliError(
            "环境变量 %s 指向的路径不存在：%s\n"
            "已按你的显式指定停止，没有回退去用 PATH 上的其它版本。\n"
            "请修正该环境变量，或把它清空以改用自动查找。" % (CLI_PATH_ENV, pinned)
        )
    exe = shutil.which("lark-cli")
    if exe:
        return exe
    for tpl in LARK_CLI_CANDIDATES:
        path = Path(tpl % os.environ.get("USERNAME", "")) if "%s" in tpl else Path(tpl)
        if path.exists():
            return str(path)
    raise CliError(
        "找不到 lark-cli。请先安装：\n"
        "  npm install -g @larksuite/cli\n"
        "装完再运行  lark-cli --version  确认。"
    )


def _err_detail(text):
    """把 lark-cli 的错误输出提炼成人话。"""
    try:
        d = json.loads(text)
    except Exception:
        return (text or "").strip()[:1000]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            msg = e.get("message") or e.get("subtype") or json.dumps(e, ensure_ascii=False)
            p = e.get("param")
            return ("%s%s" % (msg, ("（参数 %s）" % p) if p else "")).strip()
        if d.get("msg"):
            return str(d["msg"])
    return str(d)[:1000]


def run(args, no_proxy=False, check=True):
    """执行一条 lark-cli 命令，返回解析后的 JSON。

    注意：lark-cli 成功时把 JSON 打到 stdout，失败时打到 stderr 且退出码非 0。
    """
    exe = find_cli()
    env = dict(os.environ)
    if no_proxy:
        for k in PROXY_VARS:
            env.pop(k, None)
    proc = subprocess.run(
        [exe] + args,
        capture_output=True, timeout=180, env=env,
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()

    if proc.returncode != 0:
        raise CliError(_err_detail(err or out) or "命令失败，退出码 %s" % proc.returncode)

    if not out:
        raise CliError(
            "lark-cli 没有返回内容。\nstderr: %s\n排查：lark-cli doctor" % (err or "（空）")
        )
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise CliError("返回不是 JSON，原始输出：\n" + out[:2000])

    # 少数命令把 ok/code 放在同一层，这里两种形状都判一次
    if check and isinstance(data, dict):
        if data.get("ok") is False:
            raise CliError(_err_detail(json.dumps(data, ensure_ascii=False)))
        if data.get("code") not in (0, None):
            raise CliError("接口报错 code=%s msg=%s" % (data.get("code"), data.get("msg")))
    return data


def emit(data):
    """统一输出。终端编码不稳时落盘，避免中文变乱码。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    except Exception:
        out = Path("okr_feishu_output.json")
        io.open(str(out), "w", encoding="utf-8").write(text)
        print("终端编码不支持中文，结果已写入：%s" % out.resolve())


def content_arg(text):
    """把一段纯文本包成 lark-cli 要的半纯文本 JSON。"""
    return json.dumps({"text": text}, ensure_ascii=False)


def _fp(text):
    """内容指纹，供本人在预演时核对这段文字没被改过。不是安全门禁。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_pos_int(v):
    """飞书的 category_id 等 ID 校验为正整数（int64）。"""
    try:
        return int(str(v)) > 0
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------
# ContentBlock 解析：明确本解析器**支持**哪些形状
# ------------------------------------------------------------------
# 为什么要写清楚这份清单：解析结果一旦只能覆盖"认出的一部分"，
# 却拿去当整段内容的指纹做写前复核，就等于用残缺信息确认整段备注 ——
# 认不出的那部分会被新内容整个替换掉，而使用者毫不知情。
#
# 本解析器把结构里的键分成四类：
#   1. 内容节点 _CONTENT_NODE_KEYS：text_run，能抽出纯文本。
#   2. 元数据键 _META_KEYS：**已证明在该上下文里不含可读内容**的键
#      （块 id、样式、语言、层级等），且**值的形态必须符合声明**才跳过。
#   3. _OPAQUE_KEYS：已知可能承载内容、但本解析器**不解析**的键
#      （如 caption）。见到就判不可确认，绝不当成"无内容"丢掉。
#   4. 其余键 = 容器或未知块，**递归下去看它到底能不能被完整读出来**：
#        - 值是 dict / list，且递归后完整可读 → 视为已知容器
#          （飞书块类型键名很多：paragraph / heading1 / bullet / ordered /
#            quote / code / todo …，不能按名字硬编一张表，否则正常内容全被拒）；
#        - 递归后读不完整，或值是裸标量而这个键又不属于上面三类
#          → 视为**未知内容块**，整段判为"不可确认"，一律停止。
#
# 所以"支持"的定义落在**叶子能不能读出来**，不是键名在不在表里。
# 这样既不会把正常嵌套容器误拒，也不会把读不出的块悄悄丢掉。
#
# 注意这里刻意**不**把图片/附件/表格单元这类块的字段列进元数据：
# 那些块本身承载内容，只跳过它的样式字段等于假装块里没有东西，
# 反而更危险 —— 必须让它们触发"不可确认"从而停止。
_CONTENT_NODE_KEYS = ("text_run",)

# --- 元数据键：键名 → 该键在此上下文下**被支持的值的形态** -------------
# 为什么要连形态一起声明：v6 只按键名跳过，于是
#   {"text_run":{"text":""}, "children":[{"text_run":{"text":"unread"}}]}
# 里的 children 被当成"只是子块 id 引用"直接跳过，unread 那段内容凭空消失，
# 指纹只覆盖空文本就放行了备注替换 —— 认不出的部分会被整段覆盖掉。
# 声明形态之后：形态符合才当元数据；**不符合就拒绝**（解析不了就不猜）。
#
# 形态校验函数返回 True 表示"确认是无内容元数据，可以跳过"。
_META_KEY_SPEC = {
    # ── 引用类：值必须是字符串 id（扁平结构里 blocks 靠 id 关联） ──
    "block_id":   lambda v: isinstance(v, str),
    "parent_id":  lambda v: v is None or isinstance(v, str),
    "revision":   lambda v: isinstance(v, int) and not isinstance(v, bool),
    "children":   lambda v: (isinstance(v, list)
                             and all(isinstance(c, str) for c in v)),
    # ── 样式类：值必须是 dict（样式对象），不接受 str/list ──
    "style":              lambda v: isinstance(v, dict),
    "text_element_style": lambda v: isinstance(v, dict),
    "text_run_style":     lambda v: isinstance(v, dict),
    # ── 枚举/标量类：必须是 bool / int / str 这类标量 ──
    "lang":          lambda v: isinstance(v, str),
    "language":      lambda v: isinstance(v, str),
    "align":         lambda v: isinstance(v, str),
    "text_align":    lambda v: isinstance(v, str),
    "folded":        lambda v: isinstance(v, bool),
    "is_toggleable": lambda v: isinstance(v, bool),
    "word_count":    lambda v: isinstance(v, int) and not isinstance(v, bool),
    "indent_level":  lambda v: isinstance(v, int) and not isinstance(v, bool),
    "list_level":    lambda v: isinstance(v, int) and not isinstance(v, bool),
    "level":         lambda v: isinstance(v, int) and not isinstance(v, bool),
    "table_width":          lambda v: isinstance(v, int) and not isinstance(v, bool),
    "table_column_width":   lambda v: isinstance(v, int) and not isinstance(v, bool),
    "row_height":           lambda v: isinstance(v, int) and not isinstance(v, bool),
}

# 键名集合（仅用于 _text_run_extra_keys_ok 之类的快速判断）
_META_KEYS = tuple(_META_KEY_SPEC)

# --- 已证明可能承载内容、但本解析器不解析的键：见到就拒绝 ---------------
# caption 是图片/表格等块的说明文字，可能是纯文本、也可能是嵌套块。
# 本解析器**没有**实现它的抽取，所以不能静默丢弃 —— 判不可确认。
# 这不是"漏做的功能"，是刻意的选择：宁可停下让人来看一眼。
_OPAQUE_KEYS = ("caption",)


def _is_meta_key(k):
    return k in _META_KEY_SPEC


def _meta_value_ok(k, v):
    """该键在此上下文下是否**确实是**可跳过的无内容元数据。

    键名对了但形态不对（children 里塞了内容对象、caption 是字符串…）
    一律返回 False —— 交给调用方拒绝，**不静默跳过**。
    """
    check = _META_KEY_SPEC.get(k)
    if check is None:
        return False
    try:
        return bool(check(v))
    except Exception:                      # noqa: BLE001 —— 形态校验不该让流程崩
        return False


def _scan_blocks(node):
    """递归扫描 ContentBlock，返回 (text, recognized, complete)。

    返回 (text, recognized, complete)：
      text       —— 拼起来的纯文本；命中过内容节点但内容全是空时是空字符串 ""
      recognized —— True 表示至少识别到一个**内容节点**（哪怕它的文本是空的）
      complete   —— True 表示**整段结构的内容都能确认**，没有任何读不出来的节点

    ⚠️ `recognized` 和 `complete` 必须分开，这是本函数存在的全部理由：
      - recognized 只说明"找到过内容节点"，不代表"没有别的东西被丢掉"；
      - complete 才回答"这段内容我能完整读出来、可以拿去做指纹和写前复核吗"。
      只要 complete 为 False，就**不得**当成合法空、**不得**给出可用于写入的
      指纹、**不得**放行写前复核 —— 因为认不出的部分可能被整段替换掉。

    逐层判定的例子：
      {"blocks": []}                                  → ("", False, True)  合法空
      {"blocks": [{"text_run": {"text": ""}}]}        → ("",  True,  True) 合法空
      {"blocks": [{"paragraph": {"elements":
                    [{"text_run": {"text": ""}}]}}]}   → ("",  True,  True) 合法空（嵌套）
      {"blocks": [{"text_run": {"text": "known"}}]}    → ("known", True, True) 有内容
      {"blocks": [{"mystery_block": {"payload":
                    {"x": 1}}}]}                      → (None, False, False) 停止
      {"blocks": [{"text_run": {"text": ""}},
                  {"unknown_block": {"payload": "..."}}]}
                                                      → ("",  True,  False) 停止
      {"blocks": [{"text_run": {"text": "known"}},
                  {"unknown_block": {"payload": "..."}}]}
                                                      → ("known", True, False) 停止
    最后两种正是 v5 的缺口：**认出一部分**不等于**全部可读**。

    再看"元数据必须连形态一起核验"这一层（v6 的缺口，r7 发现）：
      {"blocks": [{"block_id": "b1", "parent_id": "b0",
                   "style": {...}, "text_run": {"text": "有内容"}}]}
                                                      → ("有内容", True, True) 正常
      {"blocks": [{"text_run": {"text": ""},
                   "children": ["child_1"]}]}         → ("",  True,  True) 合法空
        ↑ children 是 id 引用列表，符合声明，跳过。
      {"blocks": [{"text_run": {"text": ""},
                   "children": [{"text_run":
                                 {"text": "unread child"}}]}]}
                                                      → ("",  True,  False) 停止
        ↑ children 收到了内容对象而不是 id —— 键名是元数据键不代表值也是。
      {"blocks": [{"text_run": {"text": "known"},
                   "caption": "unread caption"}]}      → ("known", True, False) 停止
        ↑ caption 可能承载说明文字，本解析器不解析它，一律拒绝而非丢弃。
      {"blocks": [{"text_run": {"text": "known"},
                   "style": "not-a-dict"}]}            → ("known", True, False) 停止
        ↑ 形态不符的样式值同样不能当作"无内容"。
    """
    if isinstance(node, dict):
        parts, hit, complete = [], False, True

        for k, v in node.items():
            if _is_meta_key(k):
                if _meta_value_ok(k, v):
                    continue               # 形态也符合声明：确认无内容，跳过
                # 键名对、形态不符（例如 children 里塞了内容对象）：
                # 不能当成"无内容元数据"跳过 —— 那会静默丢掉一段内容，
                # 让残缺指纹放行整段替换。判不可确认。
                complete = False
                continue

            if k in _OPAQUE_KEYS:
                # 已知可能承载内容、但本解析器不解析（如 caption）。
                # 不静默丢弃，直接判不可确认。
                complete = False
                continue

            if k in _CONTENT_NODE_KEYS:
                # 内容节点：text_run。text 必须是字符串，否则读不出来。
                if isinstance(v, dict) and isinstance(v.get("text"), str):
                    parts.append(v["text"])
                    hit = True
                    # text_run 里除 text 外的键：已知样式且形态符合才可跳过，
                    # 不认识的键说明这段内容没能全部读出来。
                    for tk, tv in v.items():
                        if tk == "text":
                            continue
                        if _is_meta_key(tk) and _meta_value_ok(tk, tv):
                            continue
                        complete = False
                else:
                    complete = False
                continue

            # 其余键：递归下去，看它到底是已知容器还是读不出的块。
            if isinstance(v, (dict, list)):
                t, ok, sub = _scan_blocks(v)
                if sub:
                    if ok:
                        hit = True
                        parts.append(t)
                else:
                    complete = False       # 容器里有读不出的东西
            else:
                # 裸标量出现在非元数据键下 = 一段读不出内容的字面量。
                complete = False

        if complete is False:
            # 已经有读不出的部分。无论是否命中过内容节点，都不可确认。
            return ("".join(parts) if hit else None), hit, False
        if not hit:
            # 结构全部可读，但里面没有任何内容节点 —— 是合法空，不是读不出。
            return None, False, True
        return "".join(parts), True, True

    if isinstance(node, list):
        parts, hit, complete = [], False, True
        for x in node:
            t, ok, sub = _scan_blocks(x)
            if sub:
                if ok:
                    hit = True
                    parts.append(t)
            else:
                complete = False           # 列表里有读不出的元素
        if complete is False:
            return ("".join(parts) if hit else None), hit, False
        if not hit:
            return None, False, True        # 空列表 / 全是无内容节点
        return "".join(parts), True, True

    # 裸字符串、数字、None 落在结构里 = 没被支持的裸值，读不出它是什么。
    # metadata 键的值不会走到这里（上面已 continue 掉）。
    return None, False, False


def _text_from_blocks(node):
    """从 ContentBlock 结构里递归抽纯文本；结构认不出就返回 None（不猜）。

    ⚠️ 只有**整段可确认**时才返回文本。部分可确认时返回 None，
       避免把"读到的一部分"当成整段内容交出去。
    """
    text, recognized, complete = _scan_blocks(node)
    return text if (recognized and complete) else None


def _pick_object(data, level):
    """在 get 返回里定位到那条 O/KR 对象；定位不到返回 None。"""
    if not isinstance(data, dict):
        return None
    node = data.get("data") if isinstance(data.get("data"), dict) else data
    keys = (("objective", "objectives") if level == "objective"
            else ("key_result", "keyResult", "key_results"))
    for k in keys:
        v = node.get(k)
        if isinstance(v, dict):
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[0]
    return node if isinstance(node, dict) else None


def _is_supported_empty_scalar(value):
    """确认**值本身**属于本解析器支持的"空标量"形状。

    返回 (is_empty_scalar, text)：
      (True,  "")  —— 确认是空标量（空/纯空白字符串）
      (False, None) —— 不是空标量，交给后续按结构处理

    为什么必须**先**单独确认空标量、再做完整性核验：
    v6 把 complete 检查放在 _is_known_empty_shape 之前，于是
    `notes: ""` 走到 _scan_blocks 得到 complete=False（裸字符串不是
    ContentBlock 结构），被判成 absent —— 而 v5 及本函数自己的声明都
    明确把 ""/空白字符串当作合法空。这是**回归**，不是收紧。

    但注意这条只覆盖**值本身就是标量**的情况。对象/list 一律继续走
    完整性核验，绝不会因为这里放行就绕过未知/混合块检查。
    """
    if isinstance(value, str):
        if not value.strip():
            return True, ""
        # 非空字符串：不是"支持的空标量"。第二个返回值一律为 None，
        # 调用方只在 is_empty_scalar 为 True 时才会用它。
        return False, None
    return False, None


def _is_known_empty_shape(value):
    """判断一个字段值是不是「结构有效但内容为空」。

    飞书的 content/notes 是 ContentBlock，形如 {"blocks": [...]}。
    其中 blocks 为空列表、或 blocks 里全是空 text_run，都是**合法的空备注**，
    不是读取失败。识别不出来的东西才叫结构未知。

    ⚠️ 判「合法空」必须**三条同时成立**：
      1. 整段结构可确认（_scan_blocks 的 complete 为 True）—— 只要混进
         一个读不出的块，就不能说它"是空的"，只能说"读不出来"；
      2. 结构被识别到（有 text_run，或 blocks 为空列表）；
      3. 抽出来的文本没有非空内容。
    v5 只查了第 2、3 条，于是「空 text_run + 未知块」被当成合法空，
    拿空指纹就放行写入，未知块里的内容会被整段替换掉。

    注：空字符串不在这里处理，由 _is_supported_empty_scalar 单独确认 ——
    把它放在完整性核验之后，v6 就已经证明过会失效。
    """
    if isinstance(value, str):
        # 已由 _is_supported_empty_scalar 处理过；走到这里说明非空字符串。
        return False
    if isinstance(value, list):
        return not value                      # 空列表 = 空内容
    if isinstance(value, dict):
        blocks = value.get("blocks")
        if isinstance(blocks, list):
            # blocks 存在（哪怕是空数组）说明外层结构是认得的。
            if not blocks:
                return True
            text, recognized, complete = _scan_blocks(blocks)
            if not complete:
                # 混有读不出的块（含形态不符的元数据键）：不可确认，不能当空。
                return False
            if not recognized:
                # 结构可读但一个内容节点都没有，且不是空 blocks ——
                # 这种形状本解析器不认（正常空备注不会出现），不当空。
                return False
            # 完整可读、命中过内容节点，但文本可能全是空 → 合法空内容。
            return not text.strip()
        # 没有任何 blocks 键：属于结构未知，不当空。
        return False
    return False


def _field_state_of_response(data, level, field):
    """判断远端某条 O/KR 的某个字段处于哪种状态。

    返回 (state, text)：
      "text"   —— 字段存在、内容有值，**且整段结构可完整读出**，text 为纯文本
      "empty"  —— 字段**存在、结构完整可读**，但内容为空。包括 notes: {"blocks": []}
                  和 notes: {"blocks": [{"text_run": {"text": ""}}]} 两种形状。
                  这是合法状态，不是读失败；首次补备注就属于这一种。
      "absent" —— 字段缺失；或响应结构与预期不同；或内容里混有本解析器
                  **读不出的块**（哪怕同时有能读出的部分）；或元数据键
                  （如 children / caption）收到了声明形态以外的值。
                  **不能当成"空"**，一律按不中止处理。

    区分 empty 和 absent 是修正的核心：把合法空值误判成"解析不出"，
    会让「第一次给已有 O 补备注」这种正常操作永远卡在写前复核；
    反过来把读不出的内容误判成空或"有内容"，则会静默覆盖掉认不出的部分。

    ⚠️ 「部分可读」一律归 absent：能读出"known"不代表整段备注只有"known"。
       要判断整份内容，只能在 complete 为 True 时拿 text 去算指纹。

    ⚠️ 空字符串（""、纯空白）是**支持的合法空形状**，在完整性核验**之前**
       单独确认（见 _is_supported_empty_scalar）。这条只覆盖值本身是标量的
       情况，对象/list 仍必须通过完整性核验 —— 不能借它绕过混合块检查。
    """
    obj = _pick_object(data, level)
    if not isinstance(obj, dict):
        return "absent", None
    if field not in obj:
        return "absent", None
    value = obj[field]
    if value is None:
        # 字段在，但显式为 null：结构有效、内容为空。
        return "empty", ""
    # ① 先单独确认「支持的空标量」。空/空白字符串是 v5 就支持的合法空形状，
    #    v6 把 complete 检查放在前面导致这条路径失效 —— 这里恢复它。
    #    只对**值本身就是标量**的情况生效，对象/list 仍走下面的完整性核验。
    is_empty_scalar, empty_text = _is_supported_empty_scalar(value)
    if is_empty_scalar:
        return "empty", empty_text
    # ② 再做完整性核验：混有读不出的节点（含形态不符的元数据键）
    #    一律 absent，不管读出来的那部分是什么。
    text, recognized, complete = _scan_blocks(value)
    if not complete:
        return "absent", None
    # ③ 完整性通过后，才判断是不是合法空结构（空 blocks / 全空 text_run 等）。
    if _is_known_empty_shape(value):
        return "empty", ""
    if recognized and text.strip():
        return "text", text
    # 字段在，但抽不出文本且不属于已知空形状：结构未知，不能当空。
    return "absent", None


def _field_text_of_response(data, level, field):
    """取远端某条 O/KR 的某个字段纯文本（content 或 notes）。

    取不到（字段缺失/结构未知）返回 None；字段存在但有效为空返回空字符串。
    """
    state, text = _field_state_of_response(data, level, field)
    return text if state in ("text", "empty") else None


def _get_cmd(level, target_id):
    """单条读取命令。实测 lark-cli 1.0.96 的两个 get 均为 read 风险级。"""
    if level == "objective":
        return ["okr", "objectives", "get", "--objective-id", str(target_id), "--as", "user"]
    return ["okr", "key_results", "get", "--key-result-id", str(target_id), "--as", "user"]


def _read_current_field(level, target_id, field, no_proxy=False):
    """读远端当前字段值，供写前复核比对。

    返回 (text, state, raw)：
      state == "empty" —— 远端当前确实为空，text 为空字符串，指纹照常算。
      state == "text"  —— 远端有内容，且整段结构可完整读出。
      字段缺失 / 结构不可完整读出（混有本解析器读不出的块）/ 读取失败
        → 抛 CliError，绝不猜、不静默放行。

    ⚠️ 「读得出一部分」也归入抛错：能抽出 "known" 不等于整段备注只有 "known"，
       拿这个残缺指纹去确认，等于用部分信息为整段内容背书。
    """
    data = run(_get_cmd(level, target_id), no_proxy=no_proxy)
    state, text = _field_state_of_response(data, level, field)
    if state == "absent":
        raise CliError(
            "读到了目标，但**无法确认** %s 字段的完整内容，本次不做写前复核，也不执行写入。\n"
            "可能原因（**都不能当成「远端为空」**）：\n"
            "  a) 返回里没有该字段，或响应结构与预期不同；\n"
            "  b) 内容里混有本工具读不出的块 —— 只读出了其中一部分，\n"
            "     这种情况下**不能**拿已读到的片段当整段内容的指纹来确认，\n"
            "     否则读不出的那部分会被新内容整个替换掉。\n"
            "请人工核对原始返回：\n%s"
            % (field, json.dumps(data, ensure_ascii=False)[:1500])
        )
    return text, state, data


def _preview(action, target, payload, no_proxy_note=""):
    """统一预演提示：打印要做的事、目标、完整内容和内容指纹。"""
    print("【预演模式】未真正写入，也没有发生任何真人确认。")
    print("  操作：%s" % action)
    print("  目标：%s" % target)
    print("  内容指纹（sha256 前 16 位）：%s" % _fp(payload))
    print("  完整内容：")
    for line in payload.splitlines() or [""]:
        print("    %s" % line)
    if no_proxy_note:
        print("  %s" % no_proxy_note)
    print("  确认无误后，由使用者本人明确同意，再加 --execute 执行。\n")


def _confirm_note():
    return ("【写入模式】正在执行真实写入。注意：--execute 只表示执行这条命令，"
            "不代表工具已验证'使用者本人批准过'。")


def _prewrite_check(args, level, target_id, field, label):
    """执行模式下的写前复核：重新读远端，和 --expect-current-fp 对指纹。

    这是本脚本唯一的机制性保护：确认以后、落笔以前，远端若已变化就中止，不盲目覆盖。
    对不上、读不到、解析不出，一律中止（宁可不写）。
    """
    if getattr(args, "no_current_check", False):
        print("【注意】本次已显式关闭写前复核（--no-current-check），"
              "不会检测远端内容是否已变化。")
        return
    expected = (getattr(args, "expect_current_fp", None) or "").strip()
    if not expected:
        raise CliError(
            "执行模式必须先做写前复核。请先运行：\n"
            "  python okr_feishu.py get-content --level %s --target-id %s%s\n"
            "拿到当前%s的内容指纹，再带上 --expect-current-fp <指纹> 执行。\n"
            "确实读不到时，才用 --no-current-check 显式关闭复核（会失去远端变更保护）。"
            % (level, target_id,
               " --field notes" if field == "notes" else "",
               "备注" if field == "notes" else "正文")
        )
    actual_text, state, _raw = _read_current_field(level, target_id, field,
                                                   no_proxy=args.no_proxy)
    actual_fp = _fp(actual_text)
    if state == "empty":
        print("【写前复核】远端%s当前**为空**（这是合法空值，不是读取失败）。"
              "空内容指纹：%s，与你确认时一致，继续执行。"
              % ("备注" if field == "notes" else "正文", actual_fp))
        if actual_fp != expected:
            raise CliError(
                "远端%s状态已变化，本次操作已中止。\n"
                "  你确认时的指纹：%s\n"
                "  远端当前（空）指纹：%s\n"
                "说明你看到的是内容、而远端此刻为空（或相反），请重新读取确认。\n"
                % ("备注" if field == "notes" else "正文", expected, actual_fp)
            )
        return
    if actual_fp != expected:
        raise CliError(
            "远端%s已变化，本次操作已中止，没有覆盖任何内容。\n"
            "  你确认时的指纹：%s\n"
            "  远端当前指纹：  %s\n"
            "  远端当前内容：\n%s\n"
            "请重新读取、重新展示确认，再生成一次新操作（旧确认不再有效）。"
            % ("备注" if field == "notes" else "正文", expected, actual_fp, actual_text)
        )
    print("【写前复核】远端%s与确认时一致（指纹 %s），继续执行。" % (label, actual_fp))


def cmd_check_cli(args):
    """报告**这台机器上本脚本实际会调用哪条 lark-cli、什么版本**。

    这是纯本地只读探测：不联网、不读凭据、不碰飞书数据。
    用来把"脚本实际使用的版本"和"文档里写的版本"对上，
    避免拿另一条路径的版本号替整套封装声明。
    """
    try:
        path = find_cli()
    except CliError as e:
        emit({"ok": False, "found": False, "error": str(e)})
        raise CliError(str(e))
    version, note = check_cli_version(exe=path)
    pinned = (os.environ.get(CLI_PATH_ENV) or "").strip()
    if pinned:
        source = "环境变量 %s 显式指定" % CLI_PATH_ENV
    elif shutil.which("lark-cli") == path:
        source = "PATH（shutil.which 命中，优先于固定候选路径）"
    else:
        source = "LARK_CLI_CANDIDATES 固定候选路径"
    emit({
        "ok": True,
        "resolved_path": path,
        "how_resolved": source,
        "cli_path_env": CLI_PATH_ENV,
        "cli_path_env_value": pinned or None,
        "resolved_version": version,
        "declared_parameter_checked_version": SUPPORTED_CLI_VERSION,
        "version_matches_declared": (version == SUPPORTED_CLI_VERSION)
        if version else None,
        "note": note,
        "is_declaration_not_guarantee": True,
    })


# ---------------- 读 ----------------

def cmd_whoami(args):
    emit(run(["contact", "+get-user", "--as", "user"], no_proxy=args.no_proxy))


def cmd_find_user(args):
    cmd = ["contact", "+search-user", "--query", args.name, "--as", "user"]
    if args.has_chatted:
        cmd.append("--has-chatted")
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_cycles(args):
    cmd = ["okr", "+cycle-list", "--user-id", args.user_id,
           "--user-id-type", args.user_id_type, "--as", args.identity]
    if args.time_range:
        cmd += ["--time-range", args.time_range]
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_okrs(args):
    """读一个周期下的全部 O 与 KR。"""
    emit(run(["okr", "+cycle-detail", "--cycle-id", args.cycle_id, "--as", args.identity],
             no_proxy=args.no_proxy))


def cmd_categories(args):
    owner = args.owner_type
    cmd = ["okr", "categories", "list", "--as", args.identity]
    cmd += ["--params", json.dumps({"owner_type": owner, "page_size": 100})]
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_get_content(args):
    """读一条 O/KR 的当前字段内容与指纹，供写前复核绑定用。

    会同时打印原始返回，归属人等信息由使用者/AI 按原始信息自行核对。
    """
    level = args.level
    field = args.field
    if field == "notes" and level != "objective":
        raise CliError("飞书只有 O 层有备注字段，KR 没有；请用 --field content。")
    label = "O" if level == "objective" else "KR"
    dry = getattr(args, "dry_run", False)

    if dry:
        cmd = _get_cmd(level, args.target_id) + ["--dry-run"]
        emit(run(cmd, no_proxy=args.no_proxy))
        return

    data = run(_get_cmd(level, args.target_id), no_proxy=args.no_proxy)
    print("【读取】%s %s 的当前%s" % (label, args.target_id,
                                  "备注" if field == "notes" else "正文"))
    state, text = _field_state_of_response(data, level, field)
    if state == "absent":
        print("  ⚠️ **无法确认** %s 字段的完整内容。" % field)
        print("     这**不等于**「远端为空」，也不能拿它当空值去做写前复核。可能是：")
        print("     a) 返回里没有该字段，或响应结构与预期不同；")
        print("     b) 内容混有本工具读不出的块 —— 只读出了其中一部分。")
        print("     情况 b 下请**不要**用已读到的片段当整段内容的指纹，")
        print("     否则读不出的那部分会被新内容整个替换掉。")
        print("     请人工核对原始返回：")
    elif state == "empty":
        print("  当前内容：（空）")
        print("  ⚠️ 这是**合法空值**——字段存在、结构有效，只是还没有内容。")
        print("     空内容的指纹（sha256 前 16 位）：%s" % _fp(""))
        print("     首次给这条 %s 补内容时，用这个指纹做 --expect-current-fp 即可，")
        print("     不需要用 --no-current-check 关闭写前复核。")
    else:
        print("  当前内容指纹（sha256 前 16 位）：%s" % _fp(text))
        print("  当前内容：")
        for line in text.splitlines() or [""]:
            print("    %s" % line)
    print("  请核对：这条 %s 是否属于你本人（你自己的 open_id 可用 whoami 查）。" % label)
    print("  不属于本人时，只能评论，不能改正文。")
    print("  原始返回：")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])


# ---------------- 写（默认预演） ----------------

def _guard(args):
    """True = 只预演；False = 真写。"""
    return not args.execute


def cmd_create(args):
    """批量创建 O + KR。--input-file 是一个 JSON 数组。"""
    raw = io.open(args.input_file, encoding="utf-8").read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CliError("输入文件不是合法 JSON：%s" % e)
    if not isinstance(payload, list):
        raise CliError("输入必须是一个 JSON 数组，每个元素是一个 O。")
    if not payload:
        raise CliError("输入数组是空的。")

    default_cat = args.category_id
    if default_cat is not None and not _is_pos_int(default_cat):
        raise CliError("--category-id 必须是正整数（飞书要求 int64），当前=%r" % default_cat)
    for i, o in enumerate(payload):
        if not isinstance(o, dict) or not o.get("text"):
            raise CliError("第 %d 个 O 缺少 text 字段。" % (i + 1))
        cid = o.get("category_id") or default_cat
        if not cid:
            raise CliError(
                "第 %d 个 O 没有分类。分类必须由使用者本人逐个 O 确认后填入 category_id"
                "（或对确实同类的用 --category-id 显式兜底）。分类未定不能提交写入。"
                % (i + 1)
            )
        if not _is_pos_int(cid):
            raise CliError(
                "第 %d 个 O 的 category_id 必须是正整数（飞书要求 int64），当前=%r" % (i + 1, cid)
            )
        # CLI 的 --input 里 category_id 必须是「字符串形式的正整数」
        o["category_id"] = str(int(str(cid)))

    cmd = ["okr", "+batch-create", "--cycle-id", args.cycle_id,
           "--input", json.dumps(payload, ensure_ascii=False), "--as", "user"]
    if default_cat:
        cmd += ["--category-id", default_cat]

    summary = json.dumps(payload, ensure_ascii=False, indent=2)
    if _guard(args):
        cmd.append("--dry-run")
        _preview("批量创建 O + KR（共 %d 个 O）" % len(payload),
                 "cycle %s" % args.cycle_id, summary)
    else:
        print("%s\n正在创建 %d 个 O ...\n" % (_confirm_note(), len(payload)))
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_add_kr(args):
    cmd = ["okr", "+create", "--level", "key-result",
           "--objective-id", args.objective_id,
           "--content", content_arg(args.text), "--as", "user"]
    if _guard(args):
        cmd.append("--dry-run")
        _preview("给已有 O 增加一条 KR", "objective %s" % args.objective_id, args.text)
    else:
        print("%s\n正在给 O %s 增加一条 KR ...\n" % (_confirm_note(), args.objective_id))
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_update_content(args):
    """改本人已有 O / KR 的正文。保留原 ID，不新建。"""
    level = args.level  # objective | key-result
    cmd = ["okr", "+patch", "--level", level, "--target-id", str(args.target_id),
           "--content", content_arg(args.content), "--as", "user"]
    label = "O" if level == "objective" else "KR"
    if _guard(args):
        cmd.append("--dry-run")
        _preview("修改本人 %s 正文（保留原 ID）" % label,
                 "%s %s" % (label, args.target_id), args.content)
        if args.expect_current_fp:
            print("  已绑定写前复核指纹：%s\n" % args.expect_current_fp)
        else:
            print("  提示：执行前先跑 get-content 读取当前正文，确认"
                  "\u201c原文 → 改后\u201d后，执行时带 --expect-current-fp <指纹>，"
                  "这样远端若已变化会被拦下。\n")
    else:
        _prewrite_check(args, level, args.target_id, "content", label + " 正文")
        print("%s\n正在修改 %s %s 的正文 ...\n" % (_confirm_note(), label, args.target_id))
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_patch_notes(args):
    """更新 O 备注。飞书 OKR 只有 O 有备注字段，KR 没有——所以这里只接受 objective。"""
    if args.level != "objective":
        raise CliError(
            "飞书 OKR 的备注（notes）只存在于 O 层，KR 没有备注字段。\n"
            "KR 要补充口径信息，请放进 KR 正文，或写进 O 的备注里。"
        )
    cmd = ["okr", "+patch", "--level", "objective", "--target-id", str(args.target_id),
           "--notes", content_arg(args.notes), "--as", "user"]
    if _guard(args):
        cmd.append("--dry-run")
        _preview("更新 O 备注", "objective %s" % args.target_id, args.notes)
        if args.expect_current_fp:
            print("  已绑定写前复核指纹（对应备注字段）：%s\n" % args.expect_current_fp)
        else:
            print("  提示：执行前先跑 get-content --level objective --field notes，"
                  "执行时带 --expect-current-fp <指纹>。\n")
    else:
        _prewrite_check(args, "objective", args.target_id, "notes", "O 备注")
        print("%s\n正在更新 O %s 的备注 ...\n" % (_confirm_note(), args.target_id))
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_progress(args):
    """写周进展。--content 要的是文字，不是百分比。"""
    tt = _normalize_target_type(args.target_type)
    cmd = ["okr", "+progress-create", "--target-type", tt,
           "--target-id", str(args.target_id),
           "--content", content_arg(args.content), "--as", "user"]
    if args.percent is not None:
        # lark-cli 要求 --progress-percent 必须配 --progress-status
        cmd += ["--progress-percent", str(args.percent), "--progress-status", args.status]
    if _guard(args):
        cmd.append("--dry-run")
        _preview("写进展记录", "%s %s" % (tt, args.target_id), args.content)
    else:
        print("%s\n正在写入进展记录 ...\n" % _confirm_note())
    emit(run(cmd, no_proxy=args.no_proxy))


# ---------------- 评论定位与枚举 ----------------

# 底层 CLI 的 --target-type 枚举用下划线（key_result），脚本对外统一用连字符（key-result）。
# 两种写法都接受，内部统一映射，不要求使用者记住底层拼写。
TARGET_TYPE_ALIASES = {
    "objective": "objective",
    "key-result": "key_result",
    "key_result": "key_result",
    "cycle": "cycle",
    "progress": "progress",
}

# 只有 O/KR 的评论需要"选区/全文/回复"三选一定位。
# 依据：`+comment-create --help` 对 --selected-text 的说明是
# "selected text for a new objective/key_result selection comment"，
# 周期与进展属于实体评论/回复，不适用选区。
SELECTION_REQUIRED_TYPES = {"objective", "key_result"}


def _normalize_target_type(value):
    """把使用者写的 target-type 归一到底层枚举；不认识就报错并列出可选值。"""
    key = (value or "").strip()
    if key not in TARGET_TYPE_ALIASES:
        raise CliError(
            "不认识的 --target-type：%s\n可选值：objective / key-result / cycle / progress"
            "（key_result 与 key-result 等价）" % value)
    return TARGET_TYPE_ALIASES[key]


def _count_selection(args):
    """数出使用者给了几种定位方式。"""
    n = 0
    if getattr(args, "selected_text", None):
        n += 1
    if getattr(args, "select_all", False):
        n += 1
    if getattr(args, "ref_comment_id", None):
        n += 1
    return n


def cmd_comment_list(args):
    """读某条 O/KR/周期/进展下的评论。"""
    tt = _normalize_target_type(args.target_type)
    cmd = ["okr", "+comment-list", "--target-type", tt,
           "--target-id", str(args.target_id), "--as", "user"]
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_comment_create(args):
    """给本人或明确指定的他人 OKR 发评论 / 回复。仅 user 身份。

    定位规则（按底层 CLI 帮助区分两类实体）：
      objective / key_result —— 必须**恰好一种**定位：选区 / 全文 / 回复。
      cycle / progress      —— 实体评论或回复，不需要选区；
                                给了选区或全文反而会被接口拒。
    """
    tt = _normalize_target_type(args.target_type)
    n = _count_selection(args)

    if tt in SELECTION_REQUIRED_TYPES:
        if n == 0:
            raise CliError(
                "飞书要求 O/KR 的评论必须明确指向一段内容，三者**恰好给一个**：\n"
                "  --selected-text \"原文里的真实片段\"   针对某段文字\n"
                "  --select-all                        针对整条 O/KR 全文\n"
                "  --ref-comment-id <真实 comment ID>  回复已有评论串\n"
                "只给正文不给定位会被接口拒绝。")
        if n > 1:
            raise CliError(
                "定位方式给了 %d 种（选区 / 全文 / 回复），**只能给一种**。\n"
                "  针对某段文字  → 只给 --selected-text\n"
                "  针对整条全文  → 只给 --select-all\n"
                "  回复已有评论  → 只给 --ref-comment-id\n"
                "请去掉多余的定位参数后重试。" % n)
    else:
        if args.selected_text or args.select_all:
            raise CliError(
                "%s 是实体评论/回复，**不需要选区或全文**（底层 CLI 的 --selected-text "
                "仅适用于 objective / key_result）。\n"
                "  新建一条实体评论 → 只给 --content\n"
                "  回复已有评论串   → 再加 --ref-comment-id <comment ID>"
                % args.target_type)

    cmd = ["okr", "+comment-create", "--target-type", tt,
           "--target-id", str(args.target_id),
           "--content", content_arg(args.content), "--as", "user"]
    if args.selected_text:
        cmd += ["--selected-text", args.selected_text]
    if args.select_all:
        cmd.append("--select-all")
    if args.ref_comment_id:
        cmd += ["--ref-comment-id", str(args.ref_comment_id)]
    target = "%s %s" % (tt, args.target_id)
    if args.selected_text:
        target += "（选区：%s）" % args.selected_text
    elif args.select_all:
        target += "（全文）"
    elif args.ref_comment_id:
        target += "（回复 %s）" % args.ref_comment_id
    if _guard(args):
        cmd.append("--dry-run")
        _preview("发评论", target, args.content)
    else:
        print("%s\n正在发送评论 ...\n" % _confirm_note())
    emit(run(cmd, no_proxy=args.no_proxy))


def cmd_notify(args):
    """向明确指定的个人发通知。默认一对一，不做群发。"""
    if not args.user_id:
        raise CliError(
            "本项目默认只向明确指定的个人发送：请用 --user-id 指定 open_id。\n"
            "发到群属于群发性质，默认不做；确有需要请先由使用者本人明确确认。"
        )
    identity = args.as_identity
    cmd = ["im", "+messages-send", "--user-id", args.user_id,
           "--text", args.text, "--as", identity]
    if args.idempotency_key:
        cmd += ["--idempotency-key", args.idempotency_key]
    if _guard(args):
        cmd.append("--dry-run")
        _preview("向指定个人发通知", "user %s（身份 %s）" % (args.user_id, identity), args.text)
    else:
        print("%s\n正在发送通知给 %s ...\n" % (_confirm_note(), args.user_id))
    emit(run(cmd, no_proxy=args.no_proxy))


# ---------------- CLI ----------------

def build_parser():
    p = argparse.ArgumentParser(
        description="OKR 飞书读写封装（不碰凭据；写操作默认预演；写入固定用本人身份）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_identity(sp):
        sp.add_argument("--identity", default="user", choices=["user", "bot"],
                        help="读命令的调用身份，默认 user（读自己的）；读他人可能要 bot")

    def add_no_proxy(sp):
        sp.add_argument("--no-proxy", action="store_true",
                        help="显式剔除代理环境变量后调用（默认保留使用者自己的代理）")

    def add_execute(sp):
        sp.add_argument("--execute", action="store_true",
                        help="真正写入；不加则只预演。--execute 不代表工具已校验本人批准")

    sp = sub.add_parser("check-cli",
                        help="报告本机实际会调用的 lark-cli 路径与版本（纯本地只读探测）")
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_check_cli)

    sp = sub.add_parser("whoami", help="获取当前用户")
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("find-user", help="按姓名查 open_id")
    sp.add_argument("name", help="姓名或邮箱关键词")
    sp.add_argument("--has-chatted", action="store_true", help="只看有过会话的人")
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_find_user)

    sp = sub.add_parser("cycles", help="列出某人的 OKR 周期")
    sp.add_argument("--user-id", required=True)
    sp.add_argument("--user-id-type", default="open_id")
    sp.add_argument("--time-range", help="本地过滤，格式 2026-01--2026-04")
    add_identity(sp)
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_cycles)

    sp = sub.add_parser("okrs", help="读取某周期下的全部 O 与 KR")
    sp.add_argument("--cycle-id", required=True)
    add_identity(sp)
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_okrs)

    sp = sub.add_parser("categories", help="查询可选的 OKR 分类（由用户自己选）")
    sp.add_argument("--owner-type", default="user", choices=["user", "department"])
    add_identity(sp)
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_categories)

    sp = sub.add_parser("comment-list", help="读某条 O/KR/周期/进展下的评论")
    sp.add_argument("--target-type", required=True,
                    choices=["objective", "key-result", "key_result", "cycle", "progress"],
                    help="key_result 与 key-result 等价")
    sp.add_argument("--target-id", required=True)
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_comment_list)

    sp = sub.add_parser("get-content",
                        help="读一条 O/KR 的当前正文/备注与指纹（供写前复核绑定）")
    sp.add_argument("--level", required=True, choices=["objective", "key-result"])
    sp.add_argument("--target-id", required=True)
    sp.add_argument("--field", default="content", choices=["content", "notes"],
                    help="notes 仅 O 有；默认 content")
    sp.add_argument("--dry-run", action="store_true", help="只打印请求，不真正读取")
    add_no_proxy(sp)
    sp.set_defaults(func=cmd_get_content)

    sp = sub.add_parser("create", help="批量创建 O + KR（默认预演；分类必须逐个 O 齐备）")
    sp.add_argument("--cycle-id", required=True)
    sp.add_argument("--input-file", required=True, help="JSON 数组文件")
    sp.add_argument("--category-id", help="给没有单独设 category_id 的 O 兜底（不能代替逐个确认）")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("add-kr", help="给已有 O 增加一条 KR（默认预演）")
    sp.add_argument("--objective-id", required=True)
    sp.add_argument("--text", required=True, help="KR 正文")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_add_kr)

    sp = sub.add_parser("update-content", help="改本人 O/KR 正文，保留原 ID（默认预演）")
    sp.add_argument("--level", required=True, choices=["objective", "key-result"])
    sp.add_argument("--target-id", required=True, help="要修改的 O 或 KR 的 ID")
    sp.add_argument("--content", required=True, help="改后的正文")
    sp.add_argument("--expect-current-fp",
                    help="确认时 get-content 给出的当前正文指纹；执行模式必填")
    sp.add_argument("--no-current-check", action="store_true",
                    help="执行模式显式关闭写前复核（不推荐，会失去远端变更保护）")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_update_content)

    sp = sub.add_parser("patch-notes", help="更新 O 备注（默认预演；KR 无备注字段）")
    sp.add_argument("--level", default="objective", choices=["objective"],
                    help="飞书只有 O 层有备注，这里固定为 objective")
    sp.add_argument("--target-id", required=True)
    sp.add_argument("--notes", required=True)
    sp.add_argument("--expect-current-fp",
                    help="确认时 get-content --field notes 给出的当前备注指纹；执行模式必填")
    sp.add_argument("--no-current-check", action="store_true",
                    help="执行模式显式关闭写前复核（不推荐，会失去远端变更保护）")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_patch_notes)

    sp = sub.add_parser("progress", help="写进展记录（默认预演）")
    sp.add_argument("--target-type", required=True,
                    choices=["objective", "key-result", "key_result"],
                    help="key_result 与 key-result 等价")
    sp.add_argument("--target-id", required=True)
    sp.add_argument("--content", required=True, help="进展文字")
    sp.add_argument("--percent", type=float, help="可选：同时带百分比")
    sp.add_argument("--status", default="normal", choices=["normal", "overdue", "done"],
                    help="配合 --percent 使用，默认 normal")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_progress)

    sp = sub.add_parser("comment-create",
                        help="发评论 / 回复（默认预演；仅 user 身份。"
                             "O/KR 需恰好一种定位；周期/进展不需定位）")
    sp.add_argument("--target-type", required=True,
                    choices=["objective", "key-result", "key_result", "cycle", "progress"],
                    help="key_result 与 key-result 等价")
    sp.add_argument("--target-id", required=True)
    sp.add_argument("--content", required=True, help="评论内容")
    sp.add_argument("--selected-text", help="选区评论：必须是原文里真实存在的片段（仅 O/KR）")
    sp.add_argument("--select-all", action="store_true", help="全文选择型评论（仅 O/KR）")
    sp.add_argument("--ref-comment-id", help="回复已有评论串时，填实际的 comment ID")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_comment_create)

    sp = sub.add_parser("notify", help="向明确指定的个人发通知（默认预演；不做群发）")
    sp.add_argument("--user-id", required=True, help="收件人的 open_id")
    sp.add_argument("--text", required=True, help="消息正文")
    sp.add_argument("--as-identity", default="user", choices=["user", "bot"],
                    help="发送身份，按使用者选择和实际可用权限确定，默认 user")
    sp.add_argument("--idempotency-key", help="幂等键，防止重复发送")
    add_no_proxy(sp)
    add_execute(sp)
    sp.set_defaults(func=cmd_notify)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except CliError as e:
        print("失败：%s" % e, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print("失败：文件不存在 %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
