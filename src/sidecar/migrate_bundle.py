"""配置与密钥迁移：把中转站搬到另一台电脑。

用法：
    python migrate_bundle.py export D:\\backup\\dafeiyu-bundle.zip
    python migrate_bundle.py import D:\\backup\\dafeiyu-bundle.zip

导出内容：config.json（含密钥）、archive.key（归档密钥）。
可选 --with-archive 一起打包 archive.db（可能较大）。
导入前会备份现有文件。
"""

from __future__ import annotations

import argparse
import shutil
import time
import zipfile
from pathlib import Path

from config import CONFIG_PATH, DATA_DIR

KEY_PATH = DATA_DIR / 'archive.key'
ARCHIVE_PATH = DATA_DIR / 'archive.db'


def export_bundle(dest: Path, with_archive: bool = False) -> list[str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    items = []
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in (CONFIG_PATH, KEY_PATH, ARCHIVE_PATH if with_archive else None):
            if path and path.exists():
                z.write(path, path.name)
                items.append(path.name)
    return items


def import_bundle(src: Path) -> list[str]:
    if not src.exists():
        raise FileNotFoundError(src)
    stamp = time.strftime('%Y%m%d%H%M%S')
    restored = []
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        for name, target in (
            ('config.json', CONFIG_PATH),
            ('archive.key', KEY_PATH),
            ('archive.db', ARCHIVE_PATH),
        ):
            if name not in names:
                continue
            if target.exists():
                shutil.copy2(target, target.with_name(f'{target.name}.bak_{stamp}'))
            with z.open(name) as f, open(target, 'wb') as out:
                shutil.copyfileobj(f, out)
            restored.append(name)
    return restored


def main() -> None:
    ap = argparse.ArgumentParser(description='大肥鱼中转站配置迁移')
    ap.add_argument('action', choices=['export', 'import'])
    ap.add_argument('path')
    ap.add_argument('--with-archive', action='store_true', help='导出时一并打包归档库')
    args = ap.parse_args()
    target = Path(args.path)
    if args.action == 'export':
        items = export_bundle(target, args.with_archive)
        print(f'已导出到 {target}：{", ".join(items) or "（没有可导出的文件）"}')
        print('注意：包里包含 API Key 和归档密钥，请勿上传到公开位置。')
    else:
        restored = import_bundle(target)
        print(f'已导入：{", ".join(restored) or "（没有可导入的文件）"}')
        print('原文件已备份为 .bak_<时间戳>。')


if __name__ == '__main__':
    main()
