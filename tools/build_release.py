"""发布构建脚本：强制干净模板 + 构建后密钥扫描。

用法：
  python tools/build_release.py [版本号]

流程：
  1. 备份当前 sidecar/config.json 到临时目录（不留在仓库里）
  2. 用 config.example.json 生成干净模板
  3. 调 electron-builder 构建
  4. 扫描产物中是否残留密钥（lm_ / sk- / jwt_secret 非空）
  5. 恢复备份配置
  6. 输出 SHA256
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 中转站/
APP = ROOT / 'app'
SIDECAR = ROOT / 'sidecar'
EXAMPLE = SIDECAR / 'config.example.json'
TARGET = SIDECAR / 'config.json'

SECRET_PATTERNS = [
    (re.compile(rb'lm_[A-Za-z0-9_\-]{20,}'), 'lm_key'),
    (re.compile(rb'sk-[A-Za-z0-9]{16,}'), 'sk-key'),
    (re.compile(rb'19dd9007[0-9a-f]{40}'), 'jwt_secret'),
]


def build(version: str, out_dir: str) -> int:
    node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
    cmd = [
        node,
        str(APP / 'node_modules' / 'electron-builder' / 'cli.js'),
        '--win', 'portable', '--x64',
        f'--config.directories.output={out_dir}',
    ]
    print('构建命令:', ' '.join(cmd))
    proc = subprocess.run(cmd, cwd=str(APP))
    return proc.returncode


def scan(path: Path) -> list[str]:
    hits = []
    for f in path.rglob('*'):
        if not f.is_file():
            continue
        if f.suffix.lower() not in {'.json', '.py', '.js', '.html', '.css', '.txt', '.yml'}:
            continue
        try:
            data = f.read_bytes()
        except Exception:
            continue
        for pattern, name in SECRET_PATTERNS:
            if pattern.search(data):
                hits.append(f'{f.relative_to(path)} → {name}')
    return hits


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else json.loads(
        (APP / 'package.json').read_text(encoding='utf-8')
    )['version']
    print(f'=== 构建 v{version} ===')

    # 版本必须写进 package.json，否则构建出来的还是旧版本号
    pkg_path = APP / 'package.json'
    pkg = json.loads(pkg_path.read_text(encoding='utf-8'))
    if pkg.get('version') != version:
        pkg['version'] = version
        pkg_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写入版本 {version} → app/package.json')

    if not EXAMPLE.exists():
        print('缺少 config.example.json，无法生成干净模板')
        return 2

    backup_dir = Path(tempfile.mkdtemp(prefix='hub_cfg_'))
    backup = backup_dir / 'config.json'
    if TARGET.exists():
        shutil.copy2(TARGET, backup)
        print('已备份运行配置到临时目录')

    try:
        shutil.copy2(EXAMPLE, TARGET)
        print('已用 config.example.json 生成干净模板')

        code = build(version, 'dist-release')
        if code != 0:
            print(f'构建失败，退出码 {code}')
            return code

        out = APP / 'dist-release'
        exes = sorted(out.glob('*.exe'), key=lambda f: f.stat().st_mtime, reverse=True)
        exe = exes[0] if exes else None
        if len(exes) > 1:
            print('注意：目录里有多个 exe，取最新的:', exe.name)
        if not exe:
            print('没找到构建产物')
            return 3

        print('=== 扫描密钥 ===')
        hits = scan(out / 'win-unpacked')
        if hits:
            print('发现疑似密钥，**不要发布**：')
            for h in hits:
                print('  ', h)
            return 4
        print('未发现密钥残留')

        import hashlib

        sha = hashlib.sha256(exe.read_bytes()).hexdigest()
        print('=== 产物 ===')
        print('  文件:', exe.name)
        print('  大小:', round(exe.stat().st_size / 1048576, 1), 'MB')
        print('  SHA256:', sha)
        return 0
    finally:
        if backup.exists():
            shutil.copy2(backup, TARGET)
            print('已恢复运行配置')
        else:
            print('没有原配置可恢复（保持模板）')


if __name__ == '__main__':
    sys.exit(main())
