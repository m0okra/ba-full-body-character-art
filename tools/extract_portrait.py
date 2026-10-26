#!/usr/bin/env python3
"""从 BA 客户端 bundle 里导出立绘 spine 的图集与 skel（PC 高清版），跨平台。

用法（在本目录执行）:

    python tools/extract_portrait.py --char ch0295_spr          # 找 + 导出 ch0295_spr
    python tools/extract_portrait.py --char ch0295_spr --find   # 只列 bundle，不导出
    python tools/extract_portrait.py --list-codes               # 列出全部立绘代号
    python tools/extract_portrait.py --char airi_spr --out-dir spine_src/airi_spr
    python tools/extract_portrait.py --char np0001_spr --cli /opt/AssetStudioModCLI.dll

    --char 是 bundle 名里的那个代号（spinecharacters-<代号>-），原样精确匹配：
    ch0295_spr / airi_spr / np0001_spr / aris_noweapon_spr …… 不一定以 CHxxxx 开头。

    客户端根目录（含 BlueArchive_Data 的那一层）按以下顺序决定：
      --game-dir > 环境变量 BA_GAME_DIR > 本机常见安装位置
    bundle 目录也可以，脚本认 <root>/BlueArchive_Data/StreamingAssets/AssetBundles 或它本身。

注意: 客户端目录只读（有 XIGNCODE 反作弊），本脚本只读它，bundle 先拷到
      out/bundles/ 再交给 AssetStudio 解析。
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 本脚本在 tools/ 下
STREAMING = Path('BlueArchive_Data') / 'StreamingAssets' / 'AssetBundles'


def style(text, code, on):
    return '\x1b[%sm%s\x1b[0m' % (code, text) if on else text


def enable_ansi():
    """Windows 10+ 控制台要显式打开 VT 序列，其它平台直接可用。"""
    if not sys.stdout.isatty() or os.environ.get('NO_COLOR'):
        return False
    if os.name != 'nt':
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


GREEN, YELLOW, RED, CYAN = '32', '33', '31', '36'


def default_game_dirs():
    env = os.environ.get('BA_GAME_DIR')
    if env:
        yield Path(env).expanduser()
    if os.name == 'nt':
        yield Path(r'D:\Program Files\YostarGames\BlueArchive_JP')
        yield Path(r'C:\Program Files\YostarGames\BlueArchive_JP')
        yield Path(r'C:\Program Files (x86)\Steam\steamapps\common\BlueArchive')
        yield Path(r'D:\SteamLibrary\steamapps\common\BlueArchive')
    else:
        for base in ('~/.steam/steam', '~/.local/share/Steam', '~/.steam/root'):
            yield Path(base).expanduser() / 'steamapps' / 'common' / 'BlueArchive'


def find_bundles_dir(game_dir):
    """接受客户端根目录 / StreamingAssets / AssetBundles 三种层级。"""
    for c in (game_dir / STREAMING, game_dir, game_dir / 'AssetBundles',
              game_dir / 'StreamingAssets' / 'AssetBundles'):
        if c.is_dir():
            return c.resolve()
    return None


def locate_bundles_dir(explicit):
    """-> (bundles_dir or None, list of paths tried)"""
    tried = []
    cands = [explicit] if explicit else list(default_game_dirs())
    for g in cands:
        g = Path(g).expanduser()
        tried.append(g)
        hit = find_bundles_dir(g)
        if hit:
            return hit, tried
    return None, tried


def bundle_head(path, n=12):
    with open(path, 'rb') as f:
        return f.read(n).decode('latin-1', 'replace')


def bundle_kind(path):
    head = bundle_head(path)
    if head.startswith('UnityFS'):
        return 'UnityFS(未加密)'
    if 'CAB' in head:
        return 'CAB/裸资产'
    clean = ''.join(ch if 32 <= ord(ch) < 127 else '.' for ch in head)
    return '未知头(%s)' % clean


def human(nbytes):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if nbytes < 1024 or unit == 'GB':
            return ('%.1f %s' % (nbytes, unit)) if unit != 'B' else ('%d B' % nbytes)
        nbytes /= 1024.0


def find_cli(explicit):
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    names = (['AssetStudioModCLI.exe', 'AssetStudioModCLI.dll'] if os.name == 'nt'
             else ['AssetStudioModCLI.dll', 'AssetStudioModCLI.exe'])
    dirs = [
        ROOT / 'tools' / 'bin' / 'AssetStudioModCLI' / 'AssetStudioModCLI_net8_portable',
        ROOT / 'bin' / 'AssetStudioModCLI' / 'AssetStudioModCLI_net8_portable',
        ROOT.parent / 'seia_swimsuit' / 'extract' / 'bin' / 'AssetStudioModCLI'
        / 'AssetStudioModCLI_net8_portable',
    ]
    if os.environ.get('BA_ASSETSTUDIO'):
        dirs.insert(0, Path(os.environ['BA_ASSETSTUDIO']).expanduser().parent)
    for d in dirs:
        for n in names:
            if (d / n).exists():
                return d / n
    return None


def cli_command(cli):
    """Windows 直接跑 exe；.dll 走 dotnet（Linux/macOS 用）。"""
    if cli.suffix.lower() == '.dll':
        return [shutil.which('dotnet') or 'dotnet', str(cli)]
    return [str(cli)]


def flatten(out_dir):
    """AssetStudio 会按 bundle 名建子目录，这里把文件拉平到 out_dir。"""
    moved = 0
    for p in sorted(out_dir.rglob('*')):
        if p.is_file() and p.parent != out_dir:
            dst = out_dir / p.name
            if dst.exists():
                print(style('  [覆盖] %s（不同 bundle 里的同名文件）' % dst.name, YELLOW, C))
            shutil.move(str(p), str(dst))
            moved += 1
    for d in sorted((d for d in out_dir.rglob('*') if d.is_dir()), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return moved


def main():
    global C
    C = enable_ansi()
    ap = argparse.ArgumentParser(description='从 BA 客户端 bundle 导出立绘 spine 素材（PC 高清图集）')
    ap.add_argument('--char',
                    help='角色代号，与 bundle 名里的完全一致：ch0295_spr / airi_spr / np0001_spr')
    ap.add_argument('--game-dir', help='客户端根目录（含 BlueArchive_Data 的那一层）')
    ap.add_argument('--out-dir', help='导出目录（默认 spine_src/<代号>）')
    ap.add_argument('--pose-dir', default='tools/spine_probe',
                    help='setup pose JSON 的输出目录（默认 %(default)s）')
    ap.add_argument('--cli', help='AssetStudioModCLI.exe / .dll；不给就在常见位置里找')
    ap.add_argument('--find', action='store_true', help='只列出匹配的 bundle，不导出')
    ap.add_argument('--list-codes', action='store_true',
                    help='列出目录里所有立绘代号（spinecharacters-<代号>-）')
    ap.add_argument('--keep-bundles', action='store_true',
                    help='导出后保留 out/bundles/ 里临时拷出的 bundle')
    args = ap.parse_args()

    bundles_dir, tried = locate_bundles_dir(args.game_dir)
    if bundles_dir is None:
        print(style('找不到 bundle 目录，试过:', RED, C))
        for t in tried:
            print('  ' + str(t))
        print('用 --game-dir 指定客户端根目录（含 BlueArchive_Data 的那一层），'
              '或设置环境变量 BA_GAME_DIR')
        return 1

    all_bundles = list(bundles_dir.rglob('*.bundle'))
    total = sum(p.stat().st_size for p in all_bundles)
    print('bundle 目录: %s' % bundles_dir)
    print('已下载: %d 个文件, %s' % (len(all_bundles), human(total)))

    if args.list_codes:
        codes = set()
        for p in all_bundles:
            low = p.name.lower()
            i = low.find('spinecharacters-')
            if i < 0:
                continue
            rest = low[i + len('spinecharacters-'):]
            j = rest.find('-')
            if j > 0:
                codes.add(rest[:j])
        print('')
        print(style('共 %d 个立绘代号:' % len(codes), CYAN, C))
        for c in sorted(codes):
            print('  ' + c)
        return 0

    code = (args.char or '').strip()
    if not code:
        print(style('需要 --char <代号>（不知道代号就先 --list-codes）', RED, C))
        return 2
    leaf = code                                  # 原样用：不做任何前缀/后缀改写
    key = code.lower()
    pattern = 'spinecharacters-' + key + '-'
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else ROOT / 'spine_src' / leaf

    hits = sorted((p for p in all_bundles if pattern in p.name.lower()),
                  key=lambda p: p.name)
    print('')
    if not hits:
        print(style('[未找到] *%s*：素材可能还没下载完，或代号不对' % pattern, YELLOW, C))
        print('  代号必须和 bundle 名里的完全一致（不一定是 CHxxxx，也有 airi_spr / '
              'np0001_spr / arona_spr 这种）')
        print('  用 --list-codes 列出全部代号；立绘包名形如')
        print('    assets-_mx-spinecharacters-<代号>-_mxdependency-textures-....bundle')
        print('    ch0295_home（记忆大厅 Live2D）和 cafe-characteranimation-（小人）都不是立绘')
        return 0

    print(style('[找到] %d 个 bundle:' % len(hits), GREEN, C))
    for h in hits:
        print('  %-104s %8s  %s' % (h.name, human(h.stat().st_size), bundle_kind(h)))
    if args.find:
        return 0

    cli = find_cli(args.cli)
    if not cli:
        print(style('[缺工具] 没找到 AssetStudioModCLI', RED, C))
        if args.cli:
            print('  --cli 给的路径不存在: %s' % args.cli)
        print('  下载 AssetStudioMod (net8) 放到 tools/bin/AssetStudioModCLI/...，或用 --cli 指定')
        print('  非 Windows 用 .dll 那份: --cli <path>/AssetStudioModCLI.dll（需要 dotnet）')
        print('  备选: BAMT-CLI.exe（BA 专用，能处理加密/CRC，仅 Windows）')
        return 1
    cmd = cli_command(cli)
    if cli.suffix.lower() == '.exe' and os.name != 'nt':
        print(style('[警告] 在非 Windows 上选了 .exe，多半跑不起来；请改用 .dll + dotnet',
                    YELLOW, C))
    if cli.suffix.lower() == '.dll' and not shutil.which('dotnet'):
        print(style('[缺工具] 需要 dotnet 运行时才能跑 .dll', RED, C))
        return 1

    stage = ROOT / 'out' / 'bundles' / key
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for h in hits:
        shutil.copy2(h, stage / h.name)
        print('staged %s (%s)' % (h.name, human(h.stat().st_size)))

    out_dir.mkdir(parents=True, exist_ok=True)
    print('')
    print(style('== AssetStudioModCLI export (tex2d + textAsset) ==', CYAN, C))
    run = cmd + [str(stage), '-m', 'export', '-t', 'tex2d,textAsset',
                 '-g', 'fileName', '-f', 'assetName', '-o', str(out_dir),
                 '--log-level', 'info']
    print('$ ' + ' '.join(run))
    rc = subprocess.call(run)
    if rc != 0:
        print(style('AssetStudio 失败 (exit=%d)。bundle 头可能被加密，改用 BAMT-CLI：'
                    % rc, YELLOW, C))
        print('  BAMT-CLI.exe extract <bundle> ...   （仅 Windows）')
        return 1

    flatten(out_dir)
    files = sorted((p for p in out_dir.iterdir() if p.is_file()),
                   key=lambda p: -p.stat().st_size)
    print('')
    print(style('== 导出结果 ==', CYAN, C))
    for f in files:
        print('  %10s  %s' % (human(f.stat().st_size), f.name))

    if not args.keep_bundles:
        shutil.rmtree(stage, ignore_errors=True)
        try:
            next(stage.parent.iterdir())
        except StopIteration:
            stage.parent.rmdir()

    atlas = [f for f in files if f.suffix.lower() == '.atlas']
    skel = [f for f in files if f.suffix.lower() == '.skel']
    pngs = [f for f in files if f.suffix.lower() == '.png']
    if not atlas or not skel:
        print(style('[警告] 没导出 .atlas / .skel —— 检查 bundle 匹配对不对', YELLOW, C))
        return 1
    if not pngs:
        print(style('[警告] 没导出 .png —— 图集可能和 skel 不在同一个 bundle', YELLOW, C))

    dump = ROOT / 'tools' / 'spine_probe' / 'dump_official.mjs'
    core = ROOT / 'tools' / 'spine_probe' / 'node_modules' / '@esotericsoftware' / 'spine-core'
    pose_out = Path(args.pose_dir).expanduser() / (leaf + '_setup.json')
    if not pose_out.is_absolute():
        pose_out = ROOT / pose_out
    if shutil.which('node') and dump.exists() and core.exists():
        pose_out.parent.mkdir(parents=True, exist_ok=True)
        print('')
        print(style('== 用官方 spine-core 读一遍（验证素材 + 列动画 + 导出 setup pose）==',
                    CYAN, C))
        if subprocess.call([shutil.which('node'), str(dump), str(atlas[0]), str(skel[0]),
                            str(pose_out)]) != 0:
            print(style('[警告] dump_official.mjs 失败：atlas/skel 与 spine-core 版本不匹配？',
                        YELLOW, C))
    else:
        print(style('[跳过] 没装 node 或 tools/spine_probe/node_modules'
                    '（npm i @esotericsoftware/spine-core）', YELLOW, C))

    def rel(p):
        try:
            return os.path.relpath(str(p))
        except ValueError:            # Windows 上跨盘符
            return str(p)

    print('')
    print(style('== 下一步 ==', CYAN, C))
    print('  # 动画名见上面打印的 animations；time 决定取哪一帧（任意时刻）'
          '（不加 anim/time = setup pose）')
    print('  node tools/spine_probe/dump_official.mjs %s %s '
          'tools/spine_probe/%s_idle.json <anim> <time>' % (rel(atlas[0]), rel(skel[0]), leaf))
    print('  python build_mesh_pc.py --pose tools/spine_probe/%s_idle.json '
          '--out out/%s_idle_mesh_pc.png' % (leaf, leaf))
    return 0


if __name__ == '__main__':
    sys.exit(main())
