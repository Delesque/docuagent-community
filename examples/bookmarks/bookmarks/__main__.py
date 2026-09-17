"""``python -m bookmarks`` 的入口：只把控制权交给 :func:`bookmarks.cli.main`。

本模块按架构契约只做一件事——转发命令入口，因此 ``python -m bookmarks`` 与
``python -m bookmarks.cli`` 行为一致；进程退出码由 :func:`bookmarks.cli.main`
的返回值决定（0 成功，1 领域错误，2 用法错误）。
"""

from bookmarks.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
