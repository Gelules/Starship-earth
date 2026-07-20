/*
 * File: earth.c
 * System: Earth
 * Description: Imports real-world geometry (photogrammetry tiles) into all-range
 * levels as Scenery360 objects, so it collides and takes fire like native scenery.
 * This spike stage renders a single test cube from a hand-built display list.
 * This is not part of the original game.
 */

#include "global.h"
#include "sf64object.h"

// Empty entry in gObjectInfo, kept free by the game. The table is not const, so
// the mod claims this slot at runtime instead of patching fox_edata_info.c.
#define EARTH_OBJ_ID OBJ_SCENERY_UNK_155

#define EARTH_CUBE_RADIUS 300
#define R EARTH_CUBE_RADIUS

// Corner normals: a cube lit smoothly is enough to read orientation and scale.
// Object_DrawAll runs RCP_SetupDL_29 before the Scenery360 loop, which enables
// G_LIGHTING, so cn holds normals rather than colors.
#define EARTH_VTX(x, y, z) \
    { .n = { { x, y, z }, 0, { 0, 0 }, { (x) / 4, (y) / 4, (z) / 4 }, 255 } }

static Vtx sEarthCubeVtx[8] = {
    EARTH_VTX(-R, -R, -R), EARTH_VTX(R, -R, -R), EARTH_VTX(R, R, -R), EARTH_VTX(-R, R, -R),
    EARTH_VTX(-R, -R, R),  EARTH_VTX(R, -R, R),  EARTH_VTX(R, R, R),  EARTH_VTX(-R, R, R),
};

// SETUPDL_29 leaves the combiner on texture*shade, and no texture is bound for
// this object; swap to shade-only for the cube, then restore so the rest of the
// Scenery360 loop draws exactly as it does in vanilla.
static Gfx sEarthCubeDL[] = {
    gsDPPipeSync(),
    gsDPSetCombineMode(G_CC_SHADE, G_CC_PASS2),
    gsSPVertex(sEarthCubeVtx, 8, 0),
    gsSP2Triangles(0, 2, 1, 0, 0, 3, 2, 0), // -Z
    gsSP2Triangles(4, 5, 6, 0, 4, 6, 7, 0), // +Z
    gsSP2Triangles(0, 1, 5, 0, 0, 5, 4, 0), // -Y
    gsSP2Triangles(3, 7, 6, 0, 3, 6, 2, 0), // +Y
    gsSP2Triangles(0, 4, 7, 0, 0, 7, 3, 0), // -X
    gsSP2Triangles(1, 2, 6, 0, 1, 6, 5, 0), // +X
    gsDPPipeSync(),
    gsDPSetCombineMode(G_CC_MODULATEIDECALA, G_CC_PASS2),
    gsSPEndDisplayList(),
};

// Format read by Object_CheckSingleHitbox (fox_enmy.c): enabled, then offset and
// half-extent for z, y, x in that order.
static f32 sEarthCubeHitbox[7] = {
    1.0f, 0.0f, EARTH_CUBE_RADIUS, 0.0f, EARTH_CUBE_RADIUS, 0.0f, EARTH_CUBE_RADIUS,
};

// A ring around where the player enters all-range, at the player's own altitude.
// Real geometry will sit on the ground, but for the spike this makes collision and
// gunfire testable without flying precisely: whatever heading the player leaves on,
// a cube is a second away.
#define EARTH_RING 900.0f
#define EARTH_RING_DIAG 636.0f // EARTH_RING / sqrt(2)

static Vec3f sEarthSpawnOffsets[] = {
    { 0.0f, 0.0f, -EARTH_RING },
    { 0.0f, 0.0f, EARTH_RING },
    { -EARTH_RING, 0.0f, 0.0f },
    { EARTH_RING, 0.0f, 0.0f },
    { -EARTH_RING_DIAG, 0.0f, -EARTH_RING_DIAG },
    { EARTH_RING_DIAG, 0.0f, -EARTH_RING_DIAG },
    { -EARTH_RING_DIAG, 0.0f, EARTH_RING_DIAG },
    { EARTH_RING_DIAG, 0.0f, EARTH_RING_DIAG },
};

static bool sEarthLoaded = false;

static void Earth_LoadChunks(void) {
    ObjectInfo* info = &gObjectInfo[EARTH_OBJ_ID];
    s32 i;
    s32 slot;

    info->dList = sEarthCubeDL;
    info->drawType = 0;
    info->action = NULL;
    info->hitbox = sEarthCubeHitbox;
    info->damage = 40;

    for (i = 0, slot = 0; (i < ARRAY_COUNT(sEarthSpawnOffsets)) && (slot < 200); i++) {
        for (; slot < 200; slot++) {
            if (gScenery360[slot].obj.status != OBJ_FREE) {
                continue;
            }

            Scenery360_Initialize(&gScenery360[slot]);
            gScenery360[slot].obj.status = OBJ_ACTIVE;
            gScenery360[slot].obj.id = EARTH_OBJ_ID;
            gScenery360[slot].obj.pos.x = gPlayer[0].pos.x + sEarthSpawnOffsets[i].x;
            gScenery360[slot].obj.pos.y = gPlayer[0].pos.y + sEarthSpawnOffsets[i].y;
            gScenery360[slot].obj.pos.z = gPlayer[0].pos.z + sEarthSpawnOffsets[i].z;
            gScenery360[slot].obj.rot.y = 0.0f;
            Object_SetInfo(&gScenery360[slot].info, gScenery360[slot].obj.id);
            slot++;
            break;
        }
    }
}

void Earth_Update(void) {
    if (gLevelMode != LEVELMODE_ALL_RANGE) {
        sEarthLoaded = false;
        return;
    }

    if (!sEarthLoaded) {
        sEarthLoaded = true;
        Earth_LoadChunks();
    }
}
