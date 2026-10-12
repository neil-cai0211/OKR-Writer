# 飞书读写

> 这份文件给助手看。**没接飞书的话，整个文件可以跳过**——用户粘贴 Leader OKR，效果完全一样。

## 什么时候才需要连飞书

只在用户明确要的时候用：

1. 用户说「帮我读一下我 Leader 的 OKR」「读一下我自己的 OKR」——或者承接场景里，Leader OKR 用户懒得粘。
2. 用户明确说「帮我写进飞书」「帮我改一下系统里的这条」「帮我评论 / 通知某人」。

其余一律不碰飞书。用户没提，就别主动去连、别主动去读。

## 支持的版本

- 本文参数按 **lark-cli 1.0.96** 的 `--help` 写。不同版本参数可能不同，动手前先跑 `python tools/okr_feishu.py check-cli` 确认这台机器上脚本实际会用哪个路径、哪个版本（本机实测 npm 全局 1.0.96、`PATH` 上 1.0.97，**`PATH` 优先**）。
- 参数以 `lark-cli okr +patch --help` 这类**实际帮助输出**为准，不要凭记忆写命令。
- 只验证过本机这一条路径的环境；别的机器、别的版本是否可用，本项目没有验证过。

## 权限与隐私边界

这四条比任何便利都重要，宁可少做也不能破：

1. **读的范围**：用户本人、用户的直属 Leader（或用户**明确指定**的评论对象）。
   - 不自动读同级同事、不读隔壁部门、不读「群里看到的其他人」。
   - 读 Leader 时**必须明确是哪个人、哪个周期**；姓名有歧义先跟用户核对，不能因为搜到一个同名的人就往下载入。
   - 给指定对象写评论时，只读**定位这条评论所需的那部分内容**，不顺手把对方的 OKR 全量拉下来。
2. **改的范围**：只改**用户自己**的 O/KR 正文、备注、进展。
   - 不修改、不删除别人的任何 O/KR 正文、进展或评论。
   - 改用户自己已有的条目时，先把「原来是什么 → 要改成什么」摆出来给用户看，并**保留原 ID**（不能用新建一条来冒充修改）。
3. **评论的范围**：可以给**用户明确指定**的他人 OKR 写评论。
   - 对象不明确、或没有权限时**不发**，先回报。
   - 不自动评论、不批量评论、不替对方审核、不改对方正文。
4. **通知的范围**：只发给**用户明确指定**的人。
   - 默认一对一发送，**不群发、不定时、不自动发**。
   - 消息里出现的 @ 提及**不算**收件人授权。

**所有写入、更新、评论、通知之前，都必须用户本人明确确认**，而且要把**完整内容原样**摆出来，不是摘要。

- 用户说「写得差不多」不算确认，要说「写」/「改」/「发」这类明确的。
- 确认过一次只对那一次、那一条有效；内容或目标变了，旧确认作废，重新确认。
- **确认不跨时间、不跨会话攒着**。用户说「先记着，我明天确认」时，明天必须**重新展示一遍**完整内容再确认，不能拿昨天那次当批准；换了对话窗口也一样。
- **如实说明**：确认由助手按上面的规则执行；脚本本身**没有**独立的真人门禁，也不能用 `--execute` 或任何命令参数证明「用户本人批准过」。所以不要向用户宣称「工具已经强制校验过是你本人同意的」。

## 绝对不做的操作

即使用户明确要求，也不做：

- 改 `score`（OKR 分数/评分）——分数是绩效口径，助手不碰
- 改**权重、排序、对齐关系、截止日期**
- **删除**任何东西（O、KR、进展记录、评论）
- 未经明确指定就给别人的 OKR 写评论或进展
- 修改别人 OKR 的正文

碰到这类请求：说清这超出助手的范围，指给用户找 Leader 或自己手动操作。

## 读

### 读用户自己的 OKR

```bash
lark-cli contact +get-user --as user                        # 先拿到自己的 open_id
lark-cli okr +cycle-list --user-id <open_id> --as user      # 周期列表
lark-cli okr +cycle-detail --cycle-id <cycle_id> --as user  # 该周期下的 O + KR
```

### 读 Leader 的 OKR

```bash
lark-cli contact +search-user --query "<姓名>" --as user    # 拿 open_id（先跟用户核对是哪个人）
lark-cli okr +cycle-list --user-id <open_id> --as user
lark-cli okr +cycle-detail --cycle-id <cycle_id> --as user
```

用 `tools/okr_feishu.py` 可以少记命令：

```bash
python tools/okr_feishu.py whoami
python tools/okr_feishu.py find-user "<姓名>"
python tools/okr_feishu.py cycles --user-id <open_id>
python tools/okr_feishu.py okrs --cycle-id <cycle_id>
```

### 读到了什么，要交代清楚

读到 Leader OKR 后，在产出里**标明来源**：哪条 O/KR 来自 Leader 的哪一条。不要让用户以为是你凭空想的。

### 读不到怎么办

按顺序试：

1. 换身份重试（`--as user` 读自己的；部分接口读他人的要 `--as bot`）
2. 检查是不是周期选错了（`+cycle-list` 看有没有本周期）
3. 确认这个人的权限或 OKR 可见性设置
4. 都不行 → **如实说读不到，请用户手动粘贴**

**绝对不许**：根据用户职位、上下级关系推测 Leader 的 OKR 内容然后编出来。读不到就是读不到。

> 注意：`whoami` 成功只说明通讯录身份调用通了，**不代表**能读 Leader、能读分类、能写入。别拿一条成功当整条链路通过。

## 写

### 顺序不能反

```text
1. 生成完整成稿（O + KR + 分类 + 备注）——纯文本，先给用户看
2. 用户核对、修改
3. 用户明确说「写」
4. 才调接口写入
5. 回报：写进去了几条、成功 ID、失败的是什么
```

跳过第 3 步直接写 = 严重违规。

### 写入前先核验这几件事

| 核验项 | 怎么做 | 做不到时 |
| --- | --- | --- |
| 本人身份 | 写入固定用 `--as user`（本人的身份），不用 `bot` 冒充本人 | 停 |
| 条目归属 | 改已有条目前，先读该条内容（`get-content`），按返回的原始信息确认这条确实在**本人**名下 | 停，回报「无法确认归属」 |
| 目标与内容 | 把「周期 + 类型 + ID + 原文 → 改后」完整摆给用户看 | 停，重新展示确认 |
| 确认有效 | 用户确认后又改过内容或换了目标 ⇒ 旧确认作废，重新走一遍 | 停 |
| 远端未变 | 改正文/备注时，先用 `get-content` 取**当前内容指纹**，执行时带上 `--expect-current-fp`；确认之后、落笔之前远端若已变化，脚本会中止 | 停，重新读取并重新展示 |

### 怎么写

**新建一组 O/KR**（用户本周期还没有 OKR 时）：

```bash
lark-cli okr +batch-create --cycle-id <cycle_id> --input '<JSON>' --as user
```

`--input` 是一个数组，每个元素是一个 O。`text` 是 O 正文、`notes` 是 O 备注、`category_id` 是这个 O 的分类、`krs` 是它下面的 KR：

```json
[
  {
    "text": "O 的正文",
    "notes": "计算公式、基线、验收人等口径信息",
    "category_id": "<该 O 的分类 ID>",
    "krs": [
      { "text": "KR1 正文" },
      { "text": "KR2 正文" }
    ]
  }
]
```

> **分类必须逐个 O 带上**，由用户本人选。也可以用 `--category-id` 给「没单独设置分类的 O」兜底，但那只是兜底，不能代替用户逐个确认。
>
> ⚠️ `category_id` 要写成**字符串形式的正整数**（如 `"7001"`）。写纯数字会被接口拒（`category_id must be a positive int64` / `cannot unmarshal number into ... of type string`）。分类字典里的 ID 用 `categories list` 查。
>
> **分类未定**时，不生成可执行的写入请求——先把分类问清楚。

**给已有 O 补一条 KR**：

```bash
lark-cli okr +create --level key-result --objective-id <objective_id> \
  --content '{"text":"KR 正文"}' --as user
```

**补 / 改 O 备注**（只有 O 有备注字段）：

```bash
lark-cli okr +patch --level objective --target-id <objective_id> \
  --notes '{"text":"备注内容"}' --as user
```

⚠️ 备注用 `--target-id`，**不是** `--id`（旧文档写错过）。

**改本人 O / KR 的正文**：

```bash
# 改 O 正文
lark-cli okr +patch --level objective --target-id <objective_id> \
  --content '{"text":"改后的 O 正文"}' --as user

# 改 KR 正文
lark-cli okr +patch --level key-result --target-id <key_result_id> \
  --content '{"text":"改后的 KR 正文"}' --as user
```

- 只透传 `--level` / `--target-id` / `--content`。**`+patch` 还支持 `--score`、`--deadline`，本项目一律不透传**。
- 只用 `--target-id` 定位**已有条目**，保留原 ID；不要「新建一条一样的」来代替修改。
- 改完**读回原 ID** 确认结果，再回报成功/失败。

**改之前必须先读当前内容，改的时候绑上指纹**（这是防止覆盖别人中途改动的那道闸）：

```bash
# ① 先读当前正文，拿到「当前内容指纹」
python tools/okr_feishu.py get-content --level key-result --target-id <kr_id>
#    输出里会有：当前内容指纹（sha256 前 16 位）：xxxxxxxxxxxxxxxx

# ② 把「原文 → 改后」摆给用户看，确认

# ③ 执行时带上那个指纹
python tools/okr_feishu.py update-content --level key-result --target-id <kr_id> \
  --content "改后的 KR 正文" --expect-current-fp xxxxxxxxxxxxxxxx --execute
```

- 执行时脚本会**重新读一次远端**、再算一遍指纹比对：对不上就中止，什么都不会写，并打印远端当前内容让你重新确认。
- 缺 `--expect-current-fp` 时执行模式**直接拒绝**，不给你盲改的机会；确实读不到内容（比如没有读权限）才用 `--no-current-check` 显式关掉，并如实告诉用户「这次没有远端变更保护」。
- 备注同理，读的时候加 `--field notes`（只有 O 有备注）。
- **未验证说明**：`get-content` 的真实读取链路在本项目**没有做过真实端到端验证**（见根目录 README「当前进度」）。指纹比对逻辑本身已用离线用例验证；真实读到的响应结构若与预期不同，脚本会中止而不会静默放行。

**写周进展**（写文字，不是拉进度条）：

```bash
lark-cli okr +progress-create --target-type key_result --target-id <kr_id> \
  --content '{"text":"本周进展文字"}' --as user
```

- 目标类型参数是 `--target-type`（值：`objective` / `key_result`），不是 `--level`；ID 是 `--target-id`，不是 `--id`。旧文档写错过。
- 顺带记百分比时，`--progress-percent` 必须同时给 `--progress-status`（`normal` / `overdue` / `done`），漏一个会被拒。

**分类**：分类打在 O 层，查可选项用：

```bash
lark-cli okr categories list --owner-type user --page-size 100 --as user
```

**把选项摆给用户让他自己选**——助手不代选、不默认、不建议选哪一类，这条红线在飞书上同样成立。

### 评论

读评论：

```bash
lark-cli okr +comment-list --target-type key_result --target-id <kr_id> --as user
```

发评论 / 回复（注意 `--target-type` 的 KR 值底层是下划线 `key_result`；脚本两种写法都收）：

```bash
# 对整条 O 发评论（O/KR 三种定位恰好给一个）
lark-cli okr +comment-create --target-type objective --target-id <objective_id> \
  --select-all --content '{"text":"评论内容"}' --as user

# 针对某段文字发选区评论
lark-cli okr +comment-create --target-type key_result --target-id <kr_id> \
  --selected-text "被选中的原文" --content '{"text":"评论内容"}' --as user

# 回复已有的评论串
lark-cli okr +comment-create --target-type key_result --target-id <kr_id> \
  --ref-comment-id <具体的 comment_id> --content '{"text":"回复内容"}' --as user

# 周期（实体评论，不需要选区）
lark-cli okr +comment-create --target-type cycle --target-id <cycle_id> \
  --content '{"text":"评论内容"}' --as user
```

- 目标类型是 `--target-type`（`cycle` / `progress` / `objective` / `key_result`）+ `--target-id`，**不是** `--level` / `--id`（旧文档写错过）。
- ⚠️ **两类目标的定位要求不同，别混用**：
  - **`objective` / `key_result`（O 与 KR）**：必须**恰好一种**定位，三者给一个就够，**多给会被接口拒**：
    - `--selected-text "原文里的真实片段"` —— 针对某段文字
    - `--select-all` —— 针对整条 O/KR 的全文
    - `--ref-comment-id <真实的 comment ID>` —— 回复已有的评论串
    - 一个都不给会被拒：`objective/key_result comments require exactly one of --selected-text, --select-all, or --ref-comment-id`
  - **`cycle` / `progress`（周期与进展）**：属于**实体评论/回复**，**不需要选区**。只给 `--content` 即可；要回复再加 `--ref-comment-id`。**给了 `--selected-text` 或 `--select-all` 会被拒**。
  - 依据：`lark-cli okr +comment-create --help` 对 `--selected-text` 的说明是 "selected text for a new **objective/key_result** selection comment"，即选区仅适用于 O/KR。
- `+comment-create` 只支持 `--as user`。
- 选区评论要用 `--selected-text` 给出**真实存在的原文片段**。**选区不能凭空编**；拿不到原文就先读出来再选。
- 回复已有评论串必须用**实际的 comment ID**（先 `+comment-list` 取），不要猜。
- 范围：本人 OKR，或**用户明确指定**的他人 OKR。对象不明确 → 不发，先回报。
- 发之前把「人员 + 周期/对象 + 必要的原文上下文 + 完整评论」摆给用户看，确认后再发。
- 回报时给出**真实的 comment ID**，或如实说失败/未验证。**不盲目重试**，避免重复发评论。

### 给指定的人发通知

```bash
lark-cli im +messages-send --user-id <open_id> --text "消息内容" --as user
```

- 默认**一对一**发送。要发到群需要 `--chat-id`，属于群发性质，**本项目默认不做**；用户明确要求且确认对象时才考虑。
- 发送身份（`--as user` / `--as bot`）按用户选择和实际可用权限确定，不擅自替用户挑。
- 发之前核对三样：**收件人、发送身份、完整正文**。
- 消息文本里出现的 @ 提及**不算**收件人授权。
- 评论成功 ≠ 通知成功；写入成功也不会自动触发通知。三件事分别回报。
- 支持 `--idempotency-key`，重复发送风险高时用；仍以实际返回结果为准，**不盲目重试造成重复发送**。
- **IM 权限和 OKR 权限是两套**。缺发消息权限时：如实说明「应用没开消息权限，我发不出去」，给出补权限的办法（见 [权限清单](../../../docs/03-权限清单.md)），或把消息正文交给用户自己发——**不要**改用评论、别的手段变相送达。

### 写入前先预演

所有写操作都支持 `--dry-run`，先预演一遍确认请求体长什么样，再真写。工具脚本 `tools/okr_feishu.py` 默认就是预演模式，要加 `--execute` 才真写。

⚠️ 预演通过**不等于**真实写入成功；脚本执行成功也**不等于**用户本人批准过。这两件事每次都要如实区分。

### 写完的回报

写完必须回报清楚，不能含糊：

- 成功写入几条，对应哪些 ID
- 失败/跳过了什么，失败原因是什么
- 建议用户自己去系统里确认一眼

**不允许**因为「生成了成稿」「预演跑通了」「模拟成功了」就说「已写入」。只把接口明确返回成功的算成功。

## 不确定就停下来

- 不知道用户要写到哪个周期 → 问
- 不知道该写几条 KR、归到哪个 O → 问
- 姓名有歧义、找不到明确的人 → 先核对，别猜
- 接口报错 → 如实报错误，不要换个姿势重试到成功为止
- 读到的数据明显异常（比如周期时间对不上）→ 说出来，让用户判断

## 排错

| 现象 | 处理 |
| --- | --- |
| 提示未登录 | 走 [连接飞书](../../../docs/02-连接飞书.md) 重新授权 |
| 提示权限不足 | 缺哪个 scope 去 [权限清单](../../../docs/03-权限清单.md) 查，补完**重新发版** |
| 读自己 OKR 失败但读别人成功 | 身份问题，试试 `--as user` / `--as bot` 互换 |
| `unknown flag "--id"` | 参数名过时：备注/正文用 `--target-id`，进展用 `--target-type` + `--target-id` |
| `unknown flag "--level"`（写进展时） | 进展用 `--target-type key_result`，不是 `--level` |
| 写入报 `invalid parameters` 且提示分类 | 租户强制分类，走上面的分类流程让用户自己选 |
| 执行时提示「必须先做写前复核」 | 先跑 `get-content` 拿当前内容指纹，再加 `--expect-current-fp`；确实读不到才用 `--no-current-check` |
| 执行时提示「远端内容已变化，已中止」 | 别人中途改过了。重新展示新的「原文 → 改后」，确认后再来一次；旧确认已作废 |
| 执行时提示「没能解析出正文」 | 读到了目标但响应结构与本项目预期不同。不要绕过，如实回报并手动核对 |
| 命令超时/网络错 | 检查代理环境变量；或直接降级为「给文本让用户自己粘」 |
