"""SubAgent 系统：主 Agent 委派子任务的独立执行单元（ch16）。

「定义式」按预定义角色从空白对话执行，「Fork 式」继承父对话历史借
prompt cache 降成本；子 Agent 跑到底非交互运行，结果异步通知回主对话。
导出在 manager 完成后补全。
"""
