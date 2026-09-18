## What changed / 改了什么

<!-- One or two sentences. 一到两句话说明改动。 -->

## Why / 为什么

<!-- The problem this solves, and the issue it closes if any. 解决的问题，以及关联的 issue。 -->

## How you verified / 怎么验证的

<!-- Paste the commands you ran and their result. 贴出你跑的命令与结果。 -->

- [ ] `cd docuagent && python -m pytest -q` passes / 后端测试通过
- [ ] `npm test` in `docuagent/frontend` passes / 前端测试通过
- [ ] `npm run build` in `docuagent/frontend` passes / 前端构建通过

## Checklist / 检查项

- [ ] Backend runtime still uses only the Python standard library / 后端运行时仍只用标准库
- [ ] No new module imports `docuagent/codeintel/` except `main_routes_codeintel.py` / 除 `main_routes_codeintel.py` 外没有新模块引入代码智能包
- [ ] Project state writes go through the atomic write helpers / 项目状态写入走原子写入
- [ ] User-facing strings are Chinese / 面向用户的文案是中文
- [ ] Tests added or updated for the change / 已补充或更新测试
- [ ] Commits are English, verb-first, one change each / 提交信息为英文、动词开头、一事一提交

## Screenshots or output / 截图或输出

<!-- For user-visible changes, show before and after. 用户可见改动请附前后对比。 -->
