"""类型标注体检：统计/列出项目里（除 ``libs``）仍缺参数或返回标注的函数。

用法::

    python tools/checkTypeHints.py                # 汇总（按文件倒序）
    python tools/checkTypeHints.py pyglgame       # 只看某个目录/文件
    python tools/checkTypeHints.py --list pyglgame/render/RenderBuffer.py

判定口径：``self`` / ``cls`` 不算参数；``-> None`` 之外都算"缺返回"。嵌套函数
按各自作用域计入。
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"libs", "__pycache__", "dist", ".git", "build", ".idea", ".vscode",
             "node_modules", "Game4.build", "Game4.dist", "samples"}


def iterFiles(targets):
    for target in targets:
        path = target if os.path.isabs(target) else os.path.join(ROOT, target)
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in sorted(filenames):
                    if name.endswith(".py"):
                        yield os.path.join(dirpath, name)
        elif path.endswith(".py"):
            yield path


def collect(path):
    """→ [(行号, 限定名, 缺参数的参数名, 是否缺返回)]"""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    rows = []

    def visit(body, prefix=""):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a for a in node.args.args if a.arg not in ("self", "cls")]
                args += list(node.args.kwonlyargs)
                if node.args.vararg:
                    args.append(node.args.vararg)
                if node.args.kwarg:
                    args.append(node.args.kwarg)
                params = [a.arg for a in args if a.annotation is None]
                if params or node.returns is None:
                    rows.append((node.lineno, prefix + node.name, params,
                                 node.returns is None))
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef):
                        visit([child], prefix)

    visit(tree.body)
    return rows


def main():
    argv = sys.argv[1:]
    verbose = "--list" in argv
    targets = [a for a in argv if not a.startswith("--")] or [ROOT]
    total_ret = total_params = affected = files = clean = 0
    detail = []
    for path in iterFiles(targets):
        rel = os.path.relpath(path, ROOT)
        if set(rel.split(os.sep)) & SKIP_DIRS:
            continue
        try:
            rows = collect(path)
        except SyntaxError as exc:
            print("SKIP (syntax)", rel, exc)
            continue
        files += 1
        if not rows:
            clean += 1
            continue
        affected += len(rows)
        total_ret += sum(1 for row in rows if row[3])
        total_params += sum(len(row[2]) for row in rows)
        detail.append((len(rows), rel, rows))
    detail.sort(reverse=True)
    for count, rel, rows in detail:
        print(f"{count:5}  {rel}")
        if verbose:
            for lineno, name, params, no_ret in rows:
                bits = []
                if no_ret:
                    bits.append("缺返回")
                if params:
                    bits.append("参数: " + ", ".join(params))
                print(f"       {lineno:5} {name:<44} {' | '.join(bits)}")
    print("-" * 70)
    print(f"扫描 {files} 个文件（已干净 {clean} 个）；有问题的函数 {affected} 个，"
          f"缺返回 {total_ret} 处，缺参数 {total_params} 个")


if __name__ == "__main__":
    main()
