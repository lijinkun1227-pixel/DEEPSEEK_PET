from pathlib import Path
import sys
import argparse

skip_dirs = {".git","node_modules","__pycache__"}
DEFAULT_ROOT = (r'E:\pm learning\学习_AI橙C')


def should_skip(rel:Path) -> bool:  # rel是参数  ：Path 是注释⭐
    if rel.name == 'README.md':
        return True
    return any (p in skip_dirs or p.startswith('_tmp_') for p in rel.parts)


def parse_args() -> argparse.Namespace :
    ap = argparse.ArgumentParser(description = '找出没有被README登记的md文件')
    ap.add_argument('--root',type = Path,default = DEFAULT_ROOT,help = "要扫描的目录(默认:学习_AI橙c)")
    return ap.parse_args()


def collect(root:Path) -> tuple[list[str],list[str]]: 
    """收集除预设跳过文件外，根目录下所有文件"""
    rows = []
    skipped = []
    for f in root.rglob('*'):
        if not f.is_file() or f.suffix != ".md":
            continue
        relative_path = f.relative_to(root)
        if should_skip(relative_path):
            continue
        try:
            f.read_text(encoding = 'utf-8')
        except UnicodeDecodeError:
            skipped.append(relative_path.as_posix()) # 把非utf-8的文件放到skipped[]
            continue
        rows.append(relative_path.as_posix())
    return rows,skipped # 本质是个tuple，所以注释写tuple(list[str],list[str])


def main() -> int:
    """收集README.md里的文件，比较得出哪些文件没被收录"""
    root = parse_args().root.resolve()  # 相对路径按"当前目录"解析，resolve 掉这个坑

    try:
        readme = (root / 'README.md').read_text(encoding = 'utf-8')
    except FileNotFoundError:
        print('Fail:未找到文件')
        return  1

    rows,skipped = collect(root)

    print(f'扫描{len(rows)}个md,跳过{len(skipped)}个')
    for s in skipped:
        print(f'跳过{s}个非utf-8')


    unmentioned = [r for r in rows if Path(r).name not in readme]

    if unmentioned:
        print(f'Fail:{len(unmentioned)}个文件未登记')
        for u in unmentioned:
            print(f'{u}')
        return 1

    print("OK：全部完成")
    return 0


if __name__ == '__main__':
    sys.exit(main())