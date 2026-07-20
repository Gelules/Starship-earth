#!/usr/bin/env python3
"""Build an .o2r archive holding real-world geometry for the earth mod.

An .o2r is a plain zip (O2rArchive.cpp opens it with libzip), and libultraship
registers XML factories for both Vertex and DisplayList resources
(src/port/Engine.cpp, RESOURCE_FORMAT_XML "Vertex"/"DisplayList", version 0).
Each resource is therefore two entries: the XML document, and a `<path>.meta`
JSON file naming its format, type and version (ResourceLoader::LoadResource).

That means no binary resource headers to reproduce, so this stays stdlib-only.

The default output is the same cube src/mods/earth.c draws from a static display
list, so swapping the C constant for "__OTR__earth/cubeDL" is an A/B test: the
frame must not change.

    python3 tools/earth-import/mk_o2r.py -o mods/earth.o2r
    python3 tools/earth-import/mk_o2r.py --self-test
"""

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
import zipfile

VTX_PATH = "earth/cubeVtx"
DL_PATH = "earth/cubeDL"

# The Scenery360 loop runs RCP_SetupDL_29 before drawing, which leaves the
# combiner on texture*shade with no texture bound for this object. Swap to
# shade-only, then restore, so the rest of the loop draws as it does in vanilla.
SHADE_ONLY = ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_SHADE")
PASS_COMBINED = ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_COMBINED")


def cube(radius):
    """Return (vertices, triangles) for a cube centred on the origin.

    A vertex is (x, y, z, s, t, r, g, b, a). With G_LIGHTING enabled the rgb
    slot carries the normal as a signed byte, so corner normals are the corner
    direction scaled down; they are emitted two's-complement because the XML
    factory reads them into an unsigned char array.
    """
    verts = []
    for z in (-radius, radius):
        for y in (-radius, radius):
            for x in (-radius, radius):
                # A corner normal is the corner direction, so it only depends on the
                # sign: scaling by the radius would overflow the signed byte.
                n = [127 if c > 0 else -127 for c in (x, y, z)]
                verts.append((x, y, z, 0, 0, *[c & 0xFF for c in n], 255))

    # Indices into the order built above: bit0=x, bit1=y, bit2=z.
    faces = [
        ((0, 2, 3), (0, 3, 1)),  # -z
        ((4, 5, 7), (4, 7, 6)),  # +z
        ((0, 1, 5), (0, 5, 4)),  # -y
        ((2, 6, 7), (2, 7, 3)),  # +y
        ((0, 4, 6), (0, 6, 2)),  # -x
        ((1, 3, 7), (1, 7, 5)),  # +x
    ]
    return verts, [t for face in faces for t in face]


def vertex_xml(verts):
    root = ET.Element("Vertex", Version="0")
    for x, y, z, s, t, r, g, b, a in verts:
        ET.SubElement(root, "Vtx", X=str(x), Y=str(y), Z=str(z), S=str(s), T=str(t),
                      R=str(r), G=str(g), B=str(b), A=str(a))
    return _serialise(root)


def _combine(root, cycle0, cycle1):
    """Emit a SetCombineLERP node.

    The alpha slots take their own enum family: ResourceFactoryDisplayList::
    GetCombineLERPValue keeps separate G_CCMUX_/G_ACMUX_ tables, and the numbering
    differs (G_CCMUX_0 is 8, G_ACMUX_0 is 7). Feeding colour names to the alpha
    slots yields an out-of-range mux and geometry that never shows up.
    """
    attrs = {}
    for suffix, mux in (("0", cycle0), ("1", cycle1)):
        for slot, value in zip("abcd", mux):
            attrs[f"{slot.upper()}{suffix}"] = value                            # colour
            attrs[f"A{slot}{suffix}"] = value.replace("G_CCMUX_", "G_ACMUX_")   # alpha
    ET.SubElement(root, "SetCombineLERP", **attrs)


def displaylist_xml(vtx_path, vtx_count, tris, debug=False):
    root = ET.Element("DisplayList", Version="0")
    ET.SubElement(root, "PipeSync")
    if debug:
        # Flat red, unlit and double-sided: proves the chunk reaches the screen
        # without depending on winding or lighting.
        # G_FOG stays SET on purpose. RCP_SetupDL_29 leaves the blender on
        # G_RM_FOG_SHADE_A, and the interpreter keys fog off the render mode
        # (interpreter.cpp: use_fog = other_mode_l >> 30 == G_BL_CLR_FOG), not the
        # geometry mode. With G_FOG cleared the RSP stops writing the depth-derived
        # fog factor and the raw vertex alpha (255) is used instead, painting the
        # chunk 100% fog colour — invisible against a hazy level.
        ET.SubElement(root, "ClearGeometryMode", G_CULL_BACK="1", G_LIGHTING="1")
        _combine(root, ("G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_0", "G_CCMUX_PRIMITIVE"), PASS_COMBINED)
        ET.SubElement(root, "SetPrimColor", M="0", L="0", R="255", G="0", B="0", A="255")
    else:
        _combine(root, SHADE_ONLY, PASS_COMBINED)
    ET.SubElement(root, "LoadVertices", Path=vtx_path, Count=str(vtx_count),
                  VertexBufferIndex="0", VertexOffset="0")
    for v0, v1, v2 in tris:
        ET.SubElement(root, "Triangle1", V00=str(v0), V01=str(v1), V02=str(v2), Flag0="0")
    ET.SubElement(root, "PipeSync")
    if debug:
        ET.SubElement(root, "SetGeometryMode", G_CULL_BACK="1", G_LIGHTING="1")
    # G_CC_MODULATEIDECALA / G_CC_PASS2, the state RCP_SetupDL_29 set up.
    _combine(root, ("G_CCMUX_TEXEL0", "G_CCMUX_0", "G_CCMUX_SHADE", "G_CCMUX_0"), PASS_COMBINED)
    ET.SubElement(root, "EndDisplayList")
    return _serialise(root)


def _serialise(root):
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def meta(resource_type):
    return json.dumps({"format": "XML", "type": resource_type, "version": 0}, indent=2) + "\n"


def build(radius, debug=False):
    """Return the archive contents as {archive path: text}."""
    verts, tris = cube(radius)
    return {
        VTX_PATH: vertex_xml(verts),
        VTX_PATH + ".meta": meta("Vertex"),
        DL_PATH: displaylist_xml(VTX_PATH, len(verts), tris, debug),
        DL_PATH + ".meta": meta("DisplayList"),
    }


def write_archive(out_path, contents):
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # No `version` entry: Archive::Load treats it as optional and Starship
    # leaves mValidGameVersions empty, so adding one only risks a mismatch.
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in contents.items():
            z.writestr(name, text)
    return out


def self_test():
    contents = build(300)

    verts, tris = cube(300)
    assert len(verts) == 8, len(verts)
    assert len(tris) == 12, len(tris)
    # gSPVertex carries the count in 6 bits and the vertex buffer holds 64
    # entries (interpreter.h MAX_VERTICES); nothing bounds-checks it at runtime.
    assert len(verts) <= 32, "vertex batch too large for one LoadVertices"
    assert all(0 <= i < len(verts) for tri in tris for i in tri), "triangle index out of range"
    # Every corner must be distinct, else a face collapses.
    assert len({v[:3] for v in verts}) == 8

    dl = ET.fromstring(contents[DL_PATH])
    assert dl.tag == "DisplayList"
    # Colour slots take G_CCMUX_, alpha slots G_ACMUX_; mixing them silently
    # produces an invalid mux and invisible geometry.
    for lerp in dl.findall("SetCombineLERP"):
        for name, value in lerp.attrib.items():
            family = "G_ACMUX_" if name.startswith("A") and len(name) == 3 else "G_CCMUX_"
            assert value.startswith(family), f"{name}={value} should be {family}*"
    assert dl[-1].tag == "EndDisplayList", "display list must terminate"
    loads = dl.findall("LoadVertices")
    assert len(loads) == 1 and loads[0].get("Path") == VTX_PATH
    assert int(loads[0].get("Count")) == len(verts)
    assert len(dl.findall("Triangle1")) == len(tris)

    vtx = ET.fromstring(contents[VTX_PATH])
    assert len(vtx.findall("Vtx")) == len(verts)
    assert all(0 <= int(v.get(c)) <= 255 for v in vtx.findall("Vtx") for c in "RGBA"), \
        "colour/normal bytes must fit in an unsigned char"

    for name in (VTX_PATH, DL_PATH):
        parsed = json.loads(contents[name + ".meta"])
        assert parsed["format"] == "XML" and parsed["version"] == 0, parsed
    assert json.loads(contents[VTX_PATH + ".meta"])["type"] == "Vertex"
    assert json.loads(contents[DL_PATH + ".meta"])["type"] == "DisplayList"

    # Round-trip through a real zip, since that is what libzip will read.
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in contents.items():
            z.writestr(n, t)
    with zipfile.ZipFile(buf) as z:
        assert z.testzip() is None
        assert sorted(z.namelist()) == sorted(contents)
        assert z.read(DL_PATH).decode() == contents[DL_PATH]

    print("self-test ok:", len(verts), "vertices,", len(tris), "triangles,",
          len(contents), "archive entries")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="mods/earth.o2r", help="archive to write")
    ap.add_argument("--radius", type=int, default=300,
                    help="half-extent in game units; must match EARTH_CUBE_RADIUS in src/mods/earth.c")
    ap.add_argument("--self-test", action="store_true", help="validate the generator and exit")
    ap.add_argument("--debug-visible", action="store_true",
                    help="flat red, unlit, double-sided: locate the chunk in a busy scene")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return 0

    out = write_archive(args.out, build(args.radius, args.debug_visible))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
