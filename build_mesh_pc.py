"""按骨骼 mesh 的三角面，从 PC 图集里取像素合成立绘（本目录的最终成品流程）。

为什么是这条流程
----------------
图集矩形里塞了很多“别人的sprite”（同一角色的每个表情、其它变体）。旧的“整块贴图 + 事后清洗”
把矩形整块贴到画布上，再删岛 / 换色 / 事后修 alpha —— 修补本身就会把画面弄糊。
骨骼里本来就有真实形状：身体上下两半都是 `MeshAttachment`（23 顶点 / 21 三角形），
按三角面渲染只会采到游戏自己采的那批像素，塞在同一矩形里的其它sprite正好落在网格外，
**不需要任何事后清洗**。

UV 的唯一正确来源
----------------
spine-core 的 `MeshAttachment.updateRegion()` 只有在 `region instanceof TextureAtlasRegion`
时才按**未裁剪的原始图框**（`offsetX / originalWidth`）展开 UV —— 游戏里的 mesh UV 就是
按原图归一化的；否则走 `else` 分支，把 UV 当成"裁剪矩形内的 0..1"。
两者差一个缩放 + 平移。所以加载必须走官方整条链：

    new TextureAtlas(atlasText)
    new AtlasAttachmentLoader(atlas)
    new SkeletonBinary(loader).readSkeletonData(skel)

手搓一个 `{u,v,u2,v2}` 的 region stub 会静默掉进 `else` 分支，网格贴到错误的像素上。
（这就是 `tools/spine_probe/dump_official.mjs` 存在的意义；由它产出 pose JSON。）

用法
----
    node tools/spine_probe/dump_official.mjs <atlas> <skel> <json> [anim] [time]
    python build_mesh_pc.py --pose tools/spine_probe/official_pc_idle125.json \
                            --out out/CH0295_Idle_01_mesh_pc.png

    # setup pose（不加 anim/time 导出的 JSON）
    python build_mesh_pc.py --pose tools/spine_probe/official_pc.json \
                            --out out/CH0295_setup_mesh_pc.png

    # --ppu 覆盖画布 px / 骨骼单位（默认 = atlas page scale）

坐标系
------
rig（spine world，y 向上）→ canvas（图集 px，y 向下）：

    canvas_px = ((world_x - data.x) * K, (data.y + data.height - world_y) * K)
    K = canvas_w / data.width = atlas 的 page scale（PC 包 1.3）

每个顶点各自映射；非加权（无 bones）的 mesh 整体就是一个相似变换，weighted 的逐三角面
插值，所以整张图基本等于图集原始像素，没有重采样损失。
"""
import argparse
import json
import os
import re

import cv2
import numpy as np
from PIL import Image

DEFAULT_POSE = 'tools/spine_probe/official_pc_idle125.json'
DEFAULT_ATLAS = 'spine_src/ch0295_spr/CH0295_spr.atlas'
DEFAULT_PNG = 'spine_src/ch0295_spr/CH0295_spr.png'


def atlas_meta(path):
    """(scale, max_offsets_w, max_offsets_h) from the .atlas text."""
    scale = None
    best = (0, 0)
    for line in open(path, encoding='utf-8', errors='replace'):
        m = re.match(r'^\s*scale:\s*([\d.]+)', line)
        if m:
            scale = float(m.group(1))
        m = re.match(r'^\s*offsets:\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)', line)
        if m:
            ow, oh = int(m.group(3)), int(m.group(4))
            if ow * oh > best[0] * best[1]:
                best = (ow, oh)
    return scale, best[0], best[1]


def canvas_size(pose, atlas):
    """canvas in atlas px = reference resolution * page scale.

    Falls back to the biggest `offsets` box when the atlas has no `scale:` line.
    """
    c = pose['canvas']
    if atlas[0]:
        return max(1, round(c['width'] * atlas[0])), max(1, round(c['height'] * atlas[0]))
    return atlas[1], atlas[2]


def rgb_premultiply(rgba):
    a = rgba[:, :, 3:4] / 255.0
    out = np.empty_like(rgba)
    out[:, :, :3] = rgba[:, :, :3] * a
    out[:, :, 3:4] = rgba[:, :, 3:4]
    return out


# spine BlendMode 枚举顺序
BLEND_NAMES = {0: 'normal', 1: 'additive', 2: 'multiply', 3: 'screen'}


def composite(dst, src, mask, blend, tint=(1.0, 1.0, 1.0, 1.0)):
    """把一个 attachment 叠进预乘 alpha 累加器（就地修改 dst）。

    src 已预乘 alpha。tint 是 slot 颜色（rgba 0..1），BA 靠它的 alpha 调配件强度 ——
    漏掉它就会把只该淡淡叠 7% 的光晕按 100% 叠上去（金色光环直接变白）。预乘色彩
    的调色就是逐通道相乘，alpha 另算。

    四种混合都按预乘形式写：
      normal  dst = s + d*(1-sa)
      additive dst = s + d            （v0.2+ 的 BA 光环辉光大多是这一支）
      screen  dst = s + d*(1-s)
      multiply dst = d*(s + 1-sa)
    alpha 一律走 over（乘法混合不改 alpha 以外的量）。
    """
    ta = float(tint[3])
    s = src * mask[:, :, None]
    s[:, :, :3] *= [tint[0] * ta, tint[1] * ta, tint[2] * ta]
    s[:, :, 3:4] *= ta
    sa = s[:, :, 3:4] / 255.0
    d = dst
    if blend == 1:                                  # additive
        d += s
    elif blend == 3:                                # screen
        d[:, :, :3] += s[:, :, :3] * (1.0 - d[:, :, :3] / 255.0)
        d[:, :, 3:4] += s[:, :, 3:4] * (1.0 - d[:, :, 3:4] / 255.0)
    elif blend == 2:                                # multiply
        d[:, :, :3] *= s[:, :, :3] / 255.0 + (1.0 - sa)
        d[:, :, 3:4] = s[:, :, 3:4] + d[:, :, 3:4] * (1.0 - sa)
    else:                                           # normal (over)
        d[:, :, :3] = s[:, :, :3] + d[:, :, :3] * (1.0 - sa)
        d[:, :, 3:4] = s[:, :, 3:4] + d[:, :, 3:4] * (1.0 - sa)
    np.clip(d, 0, 255, out=d)


def rasterize_uv(page_pts, pos_pts, tris, size, origin):
    """Rasterise a mesh once: returns (mask, mapx, mapy).

    For every triangle the page->position map is the affine map of its 3 corner pairs,
    so a per-pixel barycentric walk of the triangle bbox gives the atlas pixel to
    fetch.  Doing the UV map first (instead of warping the atlas once per triangle)
    means the triangles composite exactly once - no alpha seams along shared edges -
    and it stays correct for *weighted* (skinned) meshes, where no single affine
    page->position map exists at all.
    """
    pw, ph = size
    ox, oy = origin
    c = np.stack([pos_pts[:, 0] - ox, pos_pts[:, 1] - oy], axis=1)   # patch space
    p = page_pts                                                   # page px

    mask = np.zeros((ph, pw), np.uint8)
    mapx = np.zeros((ph, pw), np.float32)
    mapy = np.zeros((ph, pw), np.float32)

    for tri in tris:
        c0, c1, c2 = c[tri[0]], c[tri[1]], c[tri[2]]
        den = (c1[1] - c2[1]) * (c0[0] - c2[0]) + (c2[0] - c1[0]) * (c0[1] - c2[1])
        if abs(den) < 1e-9:          # degenerate triangle: no pixels
            continue
        x0 = max(0, int(np.floor(min(c0[0], c1[0], c2[0]))))
        x1 = min(pw, int(np.ceil(max(c0[0], c1[0], c2[0]))) + 1)
        y0 = max(0, int(np.floor(min(c0[1], c1[1], c2[1]))))
        y1 = min(ph, int(np.ceil(max(c0[1], c1[1], c2[1]))) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        xs = np.arange(x0, x1, dtype=np.float64)[None, :]
        ys = np.arange(y0, y1, dtype=np.float64)[:, None]
        w0 = ((c1[1] - c2[1]) * (xs - c2[0]) + (c2[0] - c1[0]) * (ys - c2[1])) / den
        w1 = ((c2[1] - c0[1]) * (xs - c2[0]) + (c0[0] - c2[0]) * (ys - c2[1])) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        p0, p1, p2 = p[tri[0]], p[tri[1]], p[tri[2]]
        mx = w0 * p0[0] + w1 * p1[0] + w2 * p2[0]
        my = w0 * p0[1] + w1 * p1[1] + w2 * p2[1]
        sub_x = mapx[y0:y1, x0:x1]
        sub_y = mapy[y0:y1, x0:x1]
        sub_m = mask[y0:y1, x0:x1]
        sub_x[inside] = mx[inside]
        sub_y[inside] = my[inside]
        sub_m[inside] = 1

    return mask, mapx, mapy


def sample_patch(atlas, mask, mapx, mapy):
    """Fetch every masked pixel once; pixels outside the mask stay transparent."""
    if not mask.any():
        return None
    patch = cv2.remap(atlas, mapx, mapy, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    patch[mask == 0] = 0
    return patch


def page_hull(page, tris, region):
    """Triangles rasterised in *page* px; returns (mask, patch_origin)."""
    lo = np.floor(page.min(axis=0)).astype(int) - 1
    hi = np.ceil(page.max(axis=0)).astype(int) + 2
    if region is not None:
        lo = np.minimum(lo, [region['x'], region['y']])
        hi = np.maximum(hi, [region['x'] + region['width'], region['y'] + region['height']])
    size = (int(hi[0] - lo[0]), int(hi[1] - lo[1]))
    mask, _, _ = rasterize_uv(page, page, tris, size, (int(lo[0]), int(lo[1])))
    return mask, (int(lo[0]), int(lo[1]))


def alpha_outside_hull(atlas, region, hull, hull_origin):
    """How much sprite alpha of the attachment's atlas rect the triangles miss.

    Expect this to be *large* for BA portraits: the trimmed rect also carries the
    other variants (every expression) and the untrimmed frame's padding, and the
    23-vertex hull is not a silhouette-tight outline.
    """
    if region is None or hull is None:
        return 0, 0
    # 旋转 90° 的 region 图框是按未裁剪尺寸算的，可以越过页面右/下边界；
    # 不先夹到页面内，裁出来的 alpha 与 hull 遮罩宽度对不上
    ph, pw = atlas.shape[:2]
    x, y = int(region['x']), int(region['y'])
    x1 = min(pw, x + int(region['width']))
    y1 = min(ph, y + int(region['height']))
    if x1 <= x or y1 <= y:
        return 0, 0
    sx, sy = x - hull_origin[0], y - hull_origin[1]
    m = hull[sy:sy + (y1 - y), sx:sx + (x1 - x)] == 0
    if m.shape != (y1 - y, x1 - x) or not m.size:
        return 0, 0
    a = atlas[y:y1, x:x1, 3] > 8
    cut = a & m
    return int(cut.sum()), int(atlas[y:y1, x:x1, 3][cut].max()) if cut.any() else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pose', default=DEFAULT_POSE, help='dump_official.mjs 的输出')
    ap.add_argument('--atlas', default=DEFAULT_ATLAS)
    ap.add_argument('--png', default=DEFAULT_PNG)
    ap.add_argument('--out', default='out/portrait_mesh_pc.png')
    ap.add_argument('--ppu', type=float, default=None,
                    help='画布 px / 骨骼单位（默认 = atlas page scale，PC 包 1.3）')
    ap.add_argument('--no-crop', action='store_true', help='保留整张画布（默认裁到 bbox）')
    ap.add_argument('--no-diag', action='store_true', help='不写 mesh_out/ 预览与页面叠图')
    args = ap.parse_args()

    pose = json.load(open(args.pose, encoding='utf-8'))
    meta = atlas_meta(args.atlas)
    if args.ppu:
        CW = max(1, round(pose['canvas']['width'] * args.ppu))
        CH = max(1, round(pose['canvas']['height'] * args.ppu))
    else:
        CW, CH = canvas_size(pose, meta)

    atlas = np.asarray(Image.open(args.png).convert('RGBA')).astype(np.float32)
    print('atlas %dx%d  canvas %dx%d  pose %s  scale %s'
          % (atlas.shape[1], atlas.shape[0], CW, CH,
             pose.get('pose') or '%s@%.6fs' % (pose['anim'], pose['time']), meta[0]))
    if pose['page']['width'] != atlas.shape[1] or pose['page']['height'] != atlas.shape[0]:
        print('  !! pose 是以 %dx%d 页面导出的，与贴图尺寸不一致 —— UV 会错位'
              % (pose['page']['width'], pose['page']['height']))
    pname = pose['page'].get('name')
    if pname and os.path.basename(args.png) != pname:
        print('  !! atlas 页面是 %s，但 --png 给的是 %s —— 可能是多页图集拿错了页'
              % (pname, os.path.basename(args.png)))

    c = pose['canvas']
    cx0, cy1 = c['x'], c['y'] + c['height']
    KX, KY = CW / c['width'], CH / c['height']
    print('rig->canvas %.5f, %.5f px/unit  origin (%.2f, %.2f)' % (KX, KY, cx0, cy1))

    dst = np.zeros((CH, CW, 4), np.float32)          # 预乘 alpha 累加器
    for att in pose['attachments']:
        blend = int(att.get('blend') or 0)
        if blend not in BLEND_NAMES:
            print('  !! %s: 未知 blend mode %r，按 normal 处理' % (att['name'], att['blend']))
            blend = 0
        tint = tuple(att.get('slotColor') or (1.0, 1.0, 1.0, 1.0))
        if tint[3] <= 1e-4:
            print('  %-11s %-14s slot alpha≈0，跳过' % (att['slot'], att['name']))
            continue
        if any(abs(float(c) - 1.0) > 1e-4 for c in tint):
            note = '  tint rgb %s alpha %.3f' % (
                '/'.join('%.2f' % float(c) for c in tint[:3]), float(tint[3]))
        else:
            note = ''
        world = np.array(att['world'], np.float64).reshape(-1, 2)
        page = np.array(att['page'], np.float64).reshape(-1, 2)
        tris = np.array(att['triangles'], np.int32).reshape(-1, 3)
        canvas = np.stack([(world[:, 0] - cx0) * KX, (cy1 - world[:, 1]) * KY], axis=1)

        x0 = max(0, int(np.floor(canvas[:, 0].min())) - 2)
        y0 = max(0, int(np.floor(canvas[:, 1].min())) - 2)
        x1 = min(CW, int(np.ceil(canvas[:, 0].max())) + 2)
        y1 = min(CH, int(np.ceil(canvas[:, 1].max())) + 2)
        if x1 <= x0 or y1 <= y0:
            print('  %-11s %-14s 整块在画布外，跳过' % (att['slot'], att['name']))
            continue

        # 1) 画布空间：三角面 -> (UV 映射, 遮罩)；采样一次，避免共享边被重复混合
        mask, mapx, mapy = rasterize_uv(page, canvas, tris, (x1 - x0, y1 - y0), (x0, y0))
        if not mask.any():
            print('  %-11s %-14s 三角面在画布内无像素，跳过' % (att['slot'], att['name']))
            continue
        patch = sample_patch(atlas, mask, mapx, mapy)
        m = mask.astype(np.float32)

        cut_px, cut_max = alpha_outside_hull(atlas, att.get('region'),
                                            *page_hull(page, tris, att.get('region')))
        print('%-11s %-14s %-6s verts %2d tris %2d  canvas x %.0f..%.0f y %.0f..%.0f  '
              '图框内三角面外还有 %d px alpha (max %d，邻家sprite/留白，正常)%s%s'
              % (att['slot'], att['name'], att['type'].replace('Attachment', ''),
                 len(world), len(tris), canvas[:, 0].min(), canvas[:, 0].max(),
                 canvas[:, 1].min(), canvas[:, 1].max(), cut_px, cut_max,
                 '' if blend == 0 else '  blend=' + BLEND_NAMES[blend], note))

        composite(dst[y0:y1, x0:x1], rgb_premultiply(patch), m, blend, tint)

    # 预乘 -> 直通 alpha
    out = np.zeros((CH, CW, 4), np.float32)
    a = np.clip(dst[:, :, 3:4] / 255.0, 1e-4, None)
    out[:, :, :3] = np.clip(dst[:, :, :3] / a, 0, 255)
    out[:, :, 3:4] = np.clip(dst[:, :, 3:4], 0, 255)
    img = Image.fromarray(np.round(out).astype(np.uint8), 'RGBA')
    print('canvas result %dx%d  alpha px %d' % (img.width, img.height,
                                                int((np.asarray(img)[:, :, 3] > 8).sum())))

    bbox = img.getbbox()
    if args.no_crop or bbox is None:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        img.save(args.out)
        print('saved', args.out, img.size)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        img.crop(bbox).save(args.out)
        print('saved', args.out, (bbox[2] - bbox[0], bbox[3] - bbox[1]))

    if not args.no_diag:
        stem = os.path.splitext(os.path.basename(args.out))[0]
        os.makedirs('mesh_out', exist_ok=True)
        preview(img, 'mesh_out/%s_preview.png' % stem)
        page_overlay(atlas, pose, 'mesh_out/%s_page_overlay.png' % stem)
        print('saved mesh_out/%s_preview.png  mesh_out/%s_page_overlay.png' % (stem, stem))


def preview(img, path, height=900):
    """垫棋盘格压平，方便看透明区"""
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    chk = np.where((((yy // 24) + (xx // 24)) % 2)[..., None] == 0, 210, 160).astype(np.float32)
    al = a[:, :, 3:4] / 255.0
    flat = a[:, :, :3] * al + chk * (1 - al)
    Image.fromarray(np.clip(flat, 0, 255).astype(np.uint8), 'RGB').resize(
        (int(round(w * height / h)), height), Image.LANCZOS).save(path)


def page_overlay(atlas, pose, path):
    """在图集页面上画三角面：绿色三角必须正好套住身体sprite。

    这是验证 UV 分支是否走对的最快手段 —— UV 一旦按裁剪矩形展开，三角面会整体
    缩放/平移，肉眼立刻能看出来。
    """
    a = atlas.copy()
    al = a[:, :, 3:4] / 255.0
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    chk = np.where((((yy // 32) + (xx // 32)) % 2)[..., None] == 0, 200, 170).astype(np.float32)
    vis = np.clip(a[:, :, :3] * al + chk * (1 - al), 0, 255).astype(np.uint8)
    for att in pose['attachments']:
        page = np.array(att['page'], np.float64).reshape(-1, 2)
        tris = np.array(att['triangles'], np.int32).reshape(-1, 3)
        pts = np.round(page).astype(np.int32)
        for i in range(len(tris)):
            cv2.polylines(vis, [pts[tris[i]]], True, (40, 210, 60), 1, cv2.LINE_AA)
        for q in pts:
            cv2.circle(vis, tuple(q), 3, (230, 40, 40), -1, cv2.LINE_AA)
        cv2.putText(vis, att['slot'], tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2, cv2.LINE_AA)
    Image.fromarray(vis, 'RGB').save(path)


if __name__ == '__main__':
    main()
