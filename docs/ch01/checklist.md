# MewCode 对话 TUI（第一步）Checklist

> 每一项通过运行代码或观察行为来验证。未标注「需真实 API」的项均可用本地 mock 服务或构造数据验证。

## 实现完整性
- [ ] 配置层可加载多份配置并校验（验证：`python -m pytest tests/test_config.py` 全绿）
- [ ] 会话层多轮增删与清空正确（验证：`python -m pytest tests/test_session.py` 全绿）
- [ ] Provider 抽象与两家实现的事件解析正确（验证：`python -m pytest tests/test_providers.py` 全绿）
- [ ] TUI 欢迎语、流式渲染、斜杠命令、错误显示齐全（验证：T12 的验证方式，mock 跑一轮）
- [ ] CLI 装配正确（验证：T13 的验证方式，退出码与欢迎语检查）

## 集成
- [ ] `create_provider` 按 protocol 分发两家实例，未知协议报错（验证：`python -c` 断言）
- [ ] 会话层不感知协议：`session.py` 不引用任何 provider 模块（验证：检查源码无 provider 导入）
- [ ] ProviderError 后本轮 user 消息被回滚，后续对话上下文不受失败输入污染（验证：mock 错误端点后继续对话，问「我刚才说过什么」不含失败那轮的内容）
- [ ] api_key 不出现在任何输出中（验证：错误场景下检查 stderr 与 TUI 输出，无密钥子串）

## 编译与测试
- [ ] `python -m compileall mewcode/ tests/` 无错误
- [ ] `python -m pytest` 全部通过

## 端到端场景（在 tmux 中运行观察，对照 spec 验收标准）

- [ ] **场景 1 纯对话闭环**（AC2/AC3/AC6/AC7）：tmux 启动 → 欢迎语含配置名 → 提问 → 回复流式逐字出现 → 第二轮引用第一轮内容回答正确（如先让它记住「我叫小明」，再问「我叫什么」）→ `/help` 列出命令 → `/clear` 后再问「我叫什么」答不出 → `/exit` 正常退出
- [ ] **场景 2 Markdown 渲染**（AC4）：提问「用 markdown 给出一个表格和一段 Python 代码」，观察标题/列表/表格排版、代码块语法高亮
- [ ] **场景 3 思考展示**（AC5）：Claude 配置 `thinking: true`，观察思考区灰色实时展示、与正式回复样式区分（可用 mock 发 thinking_delta 事件验证展示逻辑）；`thinking: false` 时不出现思考区
- [ ] **场景 4 协议切换**（AC1）：配置含 anthropic 与 openai 两份，`--profile` 分别启动，各完成一轮对话（可用 mock 双端点验证请求打到了对应协议端点）
- [ ] **场景 5 缺省配置**（AC9）：不带 `--profile` 启动，欢迎语显示第一份配置的 name
- [ ] **场景 6 错误不崩溃**（AC8）：错误 api_key 提问 → 界面红色错误信息、程序不退出 → 可继续输入（mock 返回 401 等价验证）
- [ ] **场景 7 配置错误退出**（AC10）：配置文件缺失/格式错误启动 → stderr 打印明确中文错误、退出码非零
- [ ] **场景 8 thinking 透传**（AC11）：openai 配置 `thinking: true` 指向本地 mock，检查 mock 收到的请求体中包含 `thinking: true` 字段

> 说明：场景 1/2 建议用真实 API 跑一次完整验证（需用户提供 key）；其余场景均可完全用 mock 完成。
