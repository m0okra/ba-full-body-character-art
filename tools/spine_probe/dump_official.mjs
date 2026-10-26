// Read the .skel with the OFFICIAL runtime only:
//   TextureAtlas  -> real TextureAtlasRegion (offsetX/originalWidth/degrees)
//   AtlasAttachmentLoader -> MeshAttachment.updateRegion() gets the untrimmed box
//   SkeletonBinary -> Skeleton -> setup pose -> computeWorldVertices
// No hand-written region stubs (that was the earlier bug: a stub object is not a
// TextureAtlasRegion, so updateRegion() used the *trimmed* rect and the UVs came
// out scaled/offset by the trim).
import { readFileSync, writeFileSync } from 'node:fs';
import * as S from '@esotericsoftware/spine-core';

// optional: animName time(seconds)  -> pose is taken from that animation instead of setup
const [, , atlasFile, skelFile, outFile, animName, animTime] = process.argv;
const atlas = new S.TextureAtlas(readFileSync(atlasFile, 'utf8'));
const page = atlas.pages[0];
console.log('page', page.name, page.width + 'x' + page.height, 'regions', atlas.regions.length);
if (atlas.pages.length > 1) {
  console.warn('!! atlas 共 %d 页，本脚本只按第 0 页(%s) 换算 UV：多页需按 region.page 分别取图',
    atlas.pages.length, page.name);
}

const data = new S.SkeletonBinary(new S.AtlasAttachmentLoader(atlas))
  .readSkeletonData(new Uint8Array(readFileSync(skelFile)));

const out = {
  version: data.version,
  canvas: { x: data.x, y: data.y, width: data.width, height: data.height },
  page: { name: page.name, width: page.width, height: page.height },
  animations: data.animations.map((a) => a.name),
  bones: data.bones.map((b) => ({
    name: b.name, parent: b.parent?.name ?? null,
    x: b.x, y: b.y, rotation: b.rotation, scaleX: b.scaleX, scaleY: b.scaleY,
    length: b.length, inherited: b.inherit,
  })),
  slots: [],
  attachments: [],
};

const skeleton = new S.Skeleton(data);
skeleton.setToSetupPose();
if (animName) {
  const state = new S.AnimationState(new S.AnimationStateData(data));
  state.setAnimation(0, animName, true);
  state.update(Number(animTime ?? 0));
  state.apply(skeleton);
}
skeleton.updateWorldTransform(S.Physics.update);
out.pose = animName ? { anim: animName, time: Number(animTime ?? 0) } : { pose: 'setup' };

for (const slot of skeleton.drawOrder) {
  const sd = slot.data;
  out.slots.push({
    name: sd.name, bone: sd.boneData.name, blend: sd.blendMode,
    color: [sd.color.r, sd.color.g, sd.color.b, sd.color.a],
    attachment: slot.getAttachment()?.name ?? null,
  });
}

for (const slot of skeleton.drawOrder) {
  const att = slot.getAttachment();
  if (!att) continue;
  const type = att.constructor.name;
  const region = att.region;
  const rec = {
    slot: slot.data.name,
    name: att.name,
    type,
    blend: slot.data.blendMode,
    slotColor: [slot.color.r, slot.color.g, slot.color.b, slot.color.a],
    region: region && {
      name: region.name,
      page: region.page?.name ?? null,
      x: region.x, y: region.y, width: region.width, height: region.height,
      offsetX: region.offsetX, offsetY: region.offsetY,
      originalWidth: region.originalWidth, originalHeight: region.originalHeight,
      degrees: region.degrees, rotate: region.rotate,
      u: region.u, v: region.v, u2: region.u2, v2: region.v2,
    },
    weighted: type === 'MeshAttachment' ? att.bones != null && att.bones.length > 0 : false,
    world: [],
    page: [],
    triangles: [],
    uvs: [],
  };
  if (type === 'MeshAttachment') {
    const n = att.worldVerticesLength;
    const vec = new Array(n).fill(0);
    att.computeWorldVertices(slot, 0, n, vec, 0, 2);
    rec.world = vec;
    rec.uvs = Array.from(att.uvs);
    rec.triangles = Array.from(att.triangles);
    rec.hull = att.hullLength;
    rec.meshSize = [att.width, att.height];
  } else if (type === 'RegionAttachment') {
    const vec = new Array(8).fill(0);
    att.computeWorldVertices(slot, vec, 0, 2);
    rec.world = vec;
    rec.uvs = Array.from(att.uvs);
    rec.triangles = [0, 1, 2, 0, 2, 3];
    rec.attachSize = [att.width, att.height];
    rec.attachXY = [att.x, att.y, att.rotation, att.scaleX, att.scaleY];
  } else {
    continue;
  }
  for (let i = 0; i < rec.uvs.length; i += 2) {
    rec.page.push(rec.uvs[i] * page.width, rec.uvs[i + 1] * page.height);
  }
  out.attachments.push(rec);

  const xs = rec.world.filter((_, i) => i % 2 === 0);
  const ys = rec.world.filter((_, i) => i % 2 === 1);
  const px = rec.page.filter((_, i) => i % 2 === 0);
  const py = rec.page.filter((_, i) => i % 2 === 1);
  // page px per rig unit, x and y separately: must both equal the page scale (1.3)
  const spanX = (Math.max(...xs) - Math.min(...xs)) || 1e-9;
  const spanY = (Math.max(...ys) - Math.min(...ys)) || 1e-9;
  console.log(
    `${slot.data.name.padEnd(11)} ${type.replace('Attachment', '').padEnd(6)} ` +
    `verts ${String(xs.length).padStart(2)} tris ${String(rec.triangles.length / 3).padStart(2)} ` +
    `w ${rec.weighted ? 'YES' : 'no  '} ` +
    `rig ${Math.min(...xs).toFixed(1)}..${Math.max(...xs).toFixed(1)} x ` +
    `${Math.min(...ys).toFixed(1)}..${Math.max(...ys).toFixed(1)} y | ` +
    `page ${Math.min(...px).toFixed(1)}..${Math.max(...px).toFixed(1)} x ` +
    `${Math.min(...py).toFixed(1)}..${Math.max(...py).toFixed(1)} y | ` +
    `scale ${((Math.max(...px) - Math.min(...px)) / spanX).toFixed(4)} / ` +
    `${((Math.max(...py) - Math.min(...py)) / spanY).toFixed(4)}`,
  );
}

writeFileSync(outFile, JSON.stringify(out));
console.log('wrote', outFile, 'attachments', out.attachments.length);
console.log('canvas', JSON.stringify(out.canvas), 'animations', out.animations.join(', '));
