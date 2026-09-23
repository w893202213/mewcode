---
name: review
description: 审查当前项目改动：找 bug、风险与可改进点
tools:
  - run_command
  - read_file
  - find_files
  - grep_code
mode: shared
---

请审查当前项目的改动：用 run_command 执行 git diff 查看未提交改动（若工作区干净则查看最近一次提交），指出其中的 bug、风险与可改进点，并给出修改建议。

用户传入的审查重点（如有）：$ARGS
