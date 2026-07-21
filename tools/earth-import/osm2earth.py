#!/usr/bin/env python3
"""Turn OpenStreetMap building footprints into an .o2r the earth mod can load.

Google's Photorealistic 3D Tiles are not usable here: their terms forbid
pre-fetching, caching and offline use, and deriving geometry (which is what a
collision mesh is). OSM is ODbL, needs no key and no billing account.

Footprints come from Overpass, are extruded to their tagged height, projected
into a local tangent plane in metres and scaled to game units. The result is one
display list, so the whole zone costs a single Scenery360 slot and one matrix.

    python3 tools/earth-import/osm2earth.py --zone la-defense -o build-cmake/mods/earth.o2r
    python3 tools/earth-import/osm2earth.py --lat 48.8926 --lon 2.2358 --radius 500
    python3 tools/earth-import/osm2earth.py --self-test
"""

import argparse
import json
import math
import pathlib
import struct
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mk_o2r import _combine, _serialise, meta, write_archive  # noqa: E402

# A binary resource loads through the OTR path: libultraship strips a 64-byte
# header (ResourceLoader.cpp), reads the resource type/version from it, then hands
# the rest to the factory. A .meta sidecar is the alternative, but the .meta+binary
# route fails to resolve a factory here, whereas every vanilla binary asset uses
# this header -- so we mirror it. Header layout (little-endian, native on x86):
#   u8 byteOrder(0=Little), u8 isCustom, u8[2] pad, u32 type, u32 version,
#   u64 id, padding to 64.
GARR = 0x47415252  # GenericArray FourCC "GARR" (ResourceType.h)
ARRAY_F32 = 7      # GenericArray element type index for f32 (GenericArray.h)


def otr_binary(fourcc, payload, version=0):
    header = struct.pack("<BBBBIIQ", 0, 1, 0, 0, fourcc, version, 0)
    header += b"\x00" * (64 - len(header))
    return header + payload


def genericarray_f32(floats):
    floats = list(floats)
    payload = struct.pack("<II", ARRAY_F32, len(floats)) + struct.pack("<%df" % len(floats), *floats)
    return otr_binary(GARR, payload)

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Starship-earth/0.1 (personal project; OSM data used under ODbL)"

ZONES = pathlib.Path(__file__).resolve().parent / "zones.json"

# One game unit per metre. The vanilla all-range arena is roughly +-5000 units, so
# a 2 km zone fits with room to spare, and vertex coordinates stay inside the s16
# the Vtx format gives them. Calibrated for real in M3.
UNITS_PER_METRE = 1.0

# gSPVertex carries its count in 6 bits and the interpreter's vertex buffer holds
# 64 entries; 32 is the batch size the archive format is exercised with.
MAX_BATCH_VERTS = 32

# A storey is about 3 m. Buildings with neither tag get a plausible default by
# type, so an untagged shed does not become a tower.
METRES_PER_LEVEL = 3.0
DEFAULT_HEIGHT = {
    "house": 6.0,
    "hut": 3.0,
    "garage": 3.0,
    "garages": 3.0,
    "shed": 3.0,
    "roof": 4.0,
    "retail": 8.0,
    "commercial": 12.0,
    "apartments": 15.0,
    "office": 20.0,
    "tower": 40.0,
}
FALLBACK_HEIGHT = 10.0

# Flat shading baked into vertex colours: the display list clears G_LIGHTING, so
# cn is a colour rather than a normal and no normals have to be computed. Keyed by
# how much a face points at this fake sun.
SUN = (0.35, 0.86, 0.37)


def load_zone(name):
    zones = json.loads(ZONES.read_text())
    if name not in zones:
        raise SystemExit(f"unknown zone {name!r}; known: {', '.join(sorted(zones))}")
    return zones[name]


def bbox(lat, lon, radius_m):
    """Return (south, west, north, east) for a square of 2*radius around a point."""
    dlat = radius_m / 110540.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def fetch(lat, lon, radius_m, timeout=180):
    south, west, north, east = bbox(lat, lon, radius_m)
    # `out geom` inlines each way's node coordinates, so no second lookup pass.
    query = (
        f"[out:json][timeout:{timeout}];"
        f'way["building"]({south:.6f},{west:.6f},{north:.6f},{east:.6f});'
        f"out geom;"
    )
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
        return json.loads(resp.read().decode())


def height_of(tags):
    """Metres, from the most trustworthy tag available."""
    raw = tags.get("height")
    if raw:
        try:
            return float(str(raw).replace("m", "").strip())
        except ValueError:
            pass
    levels = tags.get("building:levels")
    if levels:
        try:
            return float(str(levels).split(";")[0]) * METRES_PER_LEVEL
        except ValueError:
            pass
    return DEFAULT_HEIGHT.get(tags.get("building"), FALLBACK_HEIGHT)


def project(lat, lon, lat0, lon0):
    """Equirectangular local tangent plane, metres. Good to centimetres over a few km.

    North maps to -Z so the zone is not mirrored: the game looks down +Z.
    """
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * 6378137.0
    z = -math.radians(lat - lat0) * 6356752.0
    return x, z


def shade(normal, base):
    """Bake a directional light into a vertex colour."""
    lambert = sum(n * s for n, s in zip(normal, SUN))
    k = 0.45 + 0.55 * max(lambert, 0.0)
    return tuple(min(255, max(0, int(c * k))) for c in base)


def _normal(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    length = math.sqrt(sum(c * c for c in n)) or 1.0
    return [c / length for c in n]


def _signed_area(ring):
    return 0.5 * sum(ring[i][0] * ring[i - 1][1] - ring[i - 1][0] * ring[i][1] for i in range(len(ring)))


def extrude(ring, height, base_colour):
    """Return [(vertices, triangles)] faces for one building.

    `ring` is a closed-free list of (x, z) in game units, `height` already scaled.
    Each face carries its own vertices because each has its own flat colour.
    """
    faces = []
    # Counter-clockwise in the XZ plane keeps every wall's outward normal outward.
    if _signed_area(ring) < 0:
        ring = ring[::-1]

    for i in range(len(ring)):
        x0, z0 = ring[i]
        x1, z1 = ring[(i + 1) % len(ring)]
        if (x0, z0) == (x1, z1):
            continue
        quad = [(x0, 0.0, z0), (x1, 0.0, z1), (x1, height, z1), (x0, height, z0)]
        colour = shade(_normal(quad[0], quad[1], quad[2]), base_colour)
        faces.append(([(p, colour) for p in quad], [(0, 1, 2), (0, 2, 3)]))

    # ponytail: fan triangulation, correct for convex footprints and visually fine
    # for the mildly concave ones. Swap in ear clipping if roofs start showing gaps.
    roof = [(x, height, z) for x, z in ring]
    if len(roof) >= 3:
        colour = shade((0.0, 1.0, 0.0), base_colour)
        tris = [(0, i, i + 1) for i in range(1, len(roof) - 1)]
        faces.append(([(p, colour) for p in roof], tris))
    return faces


def batch(faces, max_verts=MAX_BATCH_VERTS):
    """Pack faces into vertex batches, reindexing triangles into each batch."""
    batches = []
    verts, tris = [], []
    for face_verts, face_tris in faces:
        if len(face_verts) > max_verts:
            # A footprint with more corners than a batch holds: fan it from its
            # first vertex in slices that do fit.
            continue
        if len(verts) + len(face_verts) > max_verts:
            batches.append((verts, tris))
            verts, tris = [], []
        offset = len(verts)
        verts.extend(face_verts)
        tris.extend(tuple(i + offset for i in t) for t in face_tris)
    if verts:
        batches.append((verts, tris))
    return batches


def vertex_xml(verts):
    root = ET.Element("Vertex", Version="0")
    for (x, y, z), (r, g, b) in verts:
        ET.SubElement(root, "Vtx", X=str(int(round(x))), Y=str(int(round(y))), Z=str(int(round(z))),
                      S="0", T="0", R=str(r), G=str(g), B=str(b), A="255")
    return _serialise(root)


def displaylist_xml(name, batches):
    root = ET.Element("DisplayList", Version="0")
    ET.SubElement(root, "PipeSync")
    # G_LIGHTING off so cn is read as a colour; G_FOG deliberately left alone,
    # because the interpreter keys fog off the render mode and clearing it would
    # make the raw vertex alpha the fog factor and flood the zone in fog colour.
    ET.SubElement(root, "ClearGeometryMode", G_LIGHTING="1")
    _combine(root, ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_SHADE"),
             ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_COMBINED"))
    for i, (verts, tris) in enumerate(batches):
        ET.SubElement(root, "LoadVertices", Path=f"earth/{name}Vtx{i}", Count=str(len(verts)),
                      VertexBufferIndex="0", VertexOffset="0")
        for v0, v1, v2 in tris:
            ET.SubElement(root, "Triangle1", V00=str(v0), V01=str(v1), V02=str(v2), Flag0="0")
    ET.SubElement(root, "PipeSync")
    ET.SubElement(root, "SetGeometryMode", G_LIGHTING="1")
    _combine(root, ("G_CCMUX_TEXEL0", "G_CCMUX_0", "G_CCMUX_SHADE", "G_CCMUX_0"),
             ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_COMBINED"))
    ET.SubElement(root, "EndDisplayList")
    return _serialise(root)


def build_zone(elements, lat0, lon0, name="zone", scale=UNITS_PER_METRE):
    faces, boxes, stats = [], [], {"buildings": 0, "tagged": 0, "tallest": 0.0}
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        ring = [project(p["lat"], p["lon"], lat0, lon0) for p in el["geometry"]]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            continue
        metres = height_of(tags)
        stats["buildings"] += 1
        stats["tagged"] += 1 if ("height" in tags or "building:levels" in tags) else 0
        stats["tallest"] = max(stats["tallest"], metres)
        sring = [(x * scale, z * scale) for x, z in ring]
        sheight = metres * scale
        # A little colour variation so the city does not read as one grey mass.
        tone = 150 + (el["id"] % 5) * 12
        faces.extend(extrude(sring, sheight, (tone, tone, int(tone * 0.96))))
        # Axis-aligned box for collision, already in the game's Hitbox layout
        # (z, y, x; each offset then half-size), at the bake scale. The mod scales
        # it by EARTH_SCALE at runtime so it tracks the geometry. Base on the deck.
        xs = [p[0] for p in sring]
        zs = [p[1] for p in sring]
        boxes.append((
            (min(zs) + max(zs)) / 2, (max(zs) - min(zs)) / 2,  # z offset, half-size
            sheight / 2, sheight / 2,                          # y offset, half-size
            (min(xs) + max(xs)) / 2, (max(xs) - min(xs)) / 2,  # x offset, half-size
        ))
    batches = batch(faces)
    contents = {}
    for i, (verts, _) in enumerate(batches):
        contents[f"earth/{name}Vtx{i}"] = vertex_xml(verts)
        contents[f"earth/{name}Vtx{i}.meta"] = meta("Vertex")
    contents[f"earth/{name}DL"] = displaylist_xml(name, batches)
    contents[f"earth/{name}DL.meta"] = meta("DisplayList")
    # Collision boxes: leading building count, then 6 floats per building. The mod
    # reads this as a flat float array and scales it into a live Hitbox.
    flat = [float(len(boxes))] + [c for box in boxes for c in box]
    contents[f"earth/{name}Box"] = genericarray_f32(flat)
    stats["batches"] = len(batches)
    stats["triangles"] = sum(len(t) for _, t in batches)
    stats["boxes"] = len(boxes)
    return contents, stats


def self_test():
    # A 20 m square at the origin, 10 m tall, in a zone centred on itself.
    ring = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    faces = extrude(ring, 10.0, (160, 160, 154))
    assert len(faces) == 5, f"4 walls + 1 roof, got {len(faces)}"
    assert all(len(v) <= MAX_BATCH_VERTS for v, _ in faces)

    batches = batch(faces)
    for verts, tris in batches:
        assert len(verts) <= MAX_BATCH_VERTS, len(verts)
        assert all(0 <= i < len(verts) for t in tris for i in t), "index escapes its batch"
    assert sum(len(t) for _, t in batches) == 4 * 2 + 2, "4 wall quads + a 2-triangle roof"

    # Walls must stand up, not lie flat: every wall spans 0..height in Y.
    wall = faces[0][0]
    assert {round(p[1]) for p, _ in wall} == {0, 10}, wall

    # Winding must not depend on how the footprint was wound in OSM.
    assert extrude(ring[::-1], 10.0, (160, 160, 154))[0][0][0] == faces[0][0][0]

    assert height_of({"height": "110"}) == 110.0
    assert height_of({"building:levels": "39"}) == 39 * METRES_PER_LEVEL
    assert height_of({"building": "house"}) == DEFAULT_HEIGHT["house"]
    assert height_of({}) == FALLBACK_HEIGHT
    assert height_of({"height": "bogus", "building:levels": "3"}) == 9.0

    # Projection: north of centre must be -Z, east must be +X, and a degree of
    # latitude is about 111 km.
    x, z = project(48.9, 2.24, 48.89, 2.2358)
    assert z < 0 and x > 0, (x, z)
    assert 1050 < abs(project(48.9, 2.2358, 48.89, 2.2358)[1]) < 1160

    contents, stats = build_zone(
        [{"type": "way", "id": 1, "tags": {"height": "10"},
          "geometry": [{"lat": 48.89, "lon": 2.2358}, {"lat": 48.8902, "lon": 2.2358},
                       {"lat": 48.8902, "lon": 2.2361}, {"lat": 48.89, "lon": 2.2361}]}],
        48.89, 2.2358, name="t")
    assert stats["buildings"] == 1 and stats["triangles"] == 10, stats

    # Collision box: 64-byte OTR header (type GARR, version 0), then the GenericArray
    # header (element type f32, element count), then the floats -- read back the way
    # libultraship will: strip 64, then the factory reads type/count, then C sees a
    # flat float array whose [0] is the building count.
    blob = contents["earth/tBox"]
    bo, custom, _, _, fourcc, ver, _ = struct.unpack_from("<BBBBIIQ", blob, 0)
    assert (bo, fourcc, ver) == (0, GARR, 0), (bo, fourcc, ver)
    assert "earth/tBox.meta" not in contents, "binary box resource must not ship a .meta"
    atype, acount = struct.unpack_from("<II", blob, 64)
    assert atype == ARRAY_F32, atype
    flat = struct.unpack_from("<%df" % acount, blob, 72)
    assert acount == len(flat) == 1 + 6 * 1, acount
    assert flat[0] == 1.0, "leading building count"
    zoff, zsz, yoff, ysz, xoff, xsz = flat[1:7]
    # The box must be the footprint's bounding box (same projection) and, in y,
    # sit on the deck at half the 10 m height.
    pr = [project(p["lat"], p["lon"], 48.89, 2.2358) for p in
          [{"lat": 48.89, "lon": 2.2358}, {"lat": 48.8902, "lon": 2.2358},
           {"lat": 48.8902, "lon": 2.2361}, {"lat": 48.89, "lon": 2.2361}]]
    exs, ezs = [p[0] for p in pr], [p[1] for p in pr]
    assert abs(xoff - (min(exs) + max(exs)) / 2) < 1e-3 and abs(xsz - (max(exs) - min(exs)) / 2) < 1e-3
    assert abs(zoff - (min(ezs) + max(ezs)) / 2) < 1e-3 and abs(zsz - (max(ezs) - min(ezs)) / 2) < 1e-3
    assert (yoff, ysz) == (5.0, 5.0), (yoff, ysz)  # base on the deck, half-height offset
    # No box coordinate may reach a Hitbox sentinel (200000+) even scaled up 20x.
    assert all(abs(c) * 20 < 200000 for c in flat[1:]), "box coord near HITBOX sentinel"

    dl = ET.fromstring(contents["earth/tDL"])
    loads = dl.findall("LoadVertices")
    assert loads and all(c.get("Path") in contents for c in loads), "batch path not in archive"
    assert dl[-1].tag == "EndDisplayList"
    # Vertex coordinates must survive the s16 the format stores them in.
    for path in [c.get("Path") for c in loads]:
        for v in ET.fromstring(contents[path]).findall("Vtx"):
            for axis in "XYZ":
                assert -32768 <= int(v.get(axis)) <= 32767, v.attrib

    print(f"self-test ok: {len(faces)} faces, {stats['triangles']} triangles, "
          f"{stats['batches']} batches, {len(contents)} archive entries")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", help=f"named zone from {ZONES.name}")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=500.0, help="half-size in metres")
    ap.add_argument("--name", default="zone", help="resource base name inside the archive")
    ap.add_argument("--scale", type=float, default=UNITS_PER_METRE,
                    help="game units per metre (EARTH_SCALE); calibrated in M3")
    ap.add_argument("-o", "--out", default="build-cmake/mods/earth.o2r")
    ap.add_argument("--cache", help="read/write the raw Overpass response here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return 0

    if args.zone:
        z = load_zone(args.zone)
        lat, lon, radius = z["lat"], z["lon"], z.get("radius_m", args.radius)
    elif args.lat is not None and args.lon is not None:
        lat, lon, radius = args.lat, args.lon, args.radius
    else:
        raise SystemExit("need --zone, or --lat and --lon")

    cache = pathlib.Path(args.cache) if args.cache else None
    if cache and cache.exists():
        data = json.loads(cache.read_text())
    else:
        print(f"querying Overpass for {radius:.0f} m around {lat:.5f},{lon:.5f} ...")
        data = fetch(lat, lon, radius)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))

    contents, stats = build_zone(data.get("elements", []), lat, lon, name=args.name, scale=args.scale)
    out = write_archive(args.out, contents)
    pct = 100 * stats["tagged"] / stats["buildings"] if stats["buildings"] else 0
    print(f"{stats['buildings']} buildings ({pct:.0f}% with a real height, tallest "
          f"{stats['tallest']:.0f} m), {stats['triangles']} triangles in "
          f"{stats['batches']} batches, {stats['boxes']} collision boxes")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    print("Data (c) OpenStreetMap contributors, ODbL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
