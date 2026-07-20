/*
 * File: earth.c
 * System: Earth
 * Description: Imports real-world geometry (photogrammetry tiles) into all-range
 * levels as Scenery360 objects, so it collides and takes fire like native scenery.
 * This spike stage loads a single test cube from mods/earth.o2r.
 * This is not part of the original game.
 */

#include "global.h"
#include "sf64object.h"

// Empty entry in gObjectInfo, kept free by the game. The table is not const, so
// the mod claims this slot at runtime instead of patching fox_edata_info.c.
#define EARTH_OBJ_ID OBJ_SCENERY_UNK_155

// Must match the --radius the archive was built with, since the hitbox is sized
// from it here rather than read back out of the geometry.
#define EARTH_CUBE_RADIUS 300

// Every vanilla model is an archive path rather than a Gfx array (see
// include/assets/*.h), and Scenery360_Draw feeds info.dList straight to
// gSPDisplayList, which resolves the __OTR__ prefix. Chunks therefore live in
// mods/earth.o2r, built by tools/earth-import/mk_o2r.py.
static const ALIGN_ASSET(2) char sEarthCubeOtr[] = "__OTR__earth/cubeDL";

// Format read by Object_CheckSingleHitbox (fox_enmy.c): enabled, then offset and
// half-extent for z, y, x in that order.
static f32 sEarthCubeHitbox[7] = {
    1.0f, 0.0f, EARTH_CUBE_RADIUS, 0.0f, EARTH_CUBE_RADIUS, 0.0f, EARTH_CUBE_RADIUS,
};

// A ring around where the player enters all-range, at the player's own altitude.
// Real geometry will sit on the ground, but for the spike this makes collision and
// gunfire testable without flying precisely: whatever heading the player leaves on,
// a cube is a second away. Player_CollisionCheck only considers scenery within
// 1100 units, so the ring has to stay inside that to be collidable on arrival.
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
static s32 sEarthDelay = 0;
static s32 sEarthFirstSlot = -1;

static void Earth_LoadChunks(void) {
    ObjectInfo* info = &gObjectInfo[EARTH_OBJ_ID];
    s32 i;
    s32 slot;

    info->dList = (Gfx*) sEarthCubeOtr;
    info->action = NULL;
    info->hitbox = sEarthCubeHitbox;
    info->damage = 40;

    for (i = 0, slot = 0; (i < ARRAY_COUNT(sEarthSpawnOffsets)) && (slot < 200); i++) {
        for (; slot < 200; slot++) {
            if (gScenery360[slot].obj.status != OBJ_FREE) {
                continue;
            }

            if (sEarthFirstSlot < 0) {
                sEarthFirstSlot = slot;
            }

            Scenery360_Initialize(&gScenery360[slot]);
            gScenery360[slot].obj.status = OBJ_ACTIVE;
            gScenery360[slot].obj.id = EARTH_OBJ_ID;
            gScenery360[slot].obj.pos.x = gPlayer[0].pos.x + sEarthSpawnOffsets[i].x;
            gScenery360[slot].obj.pos.y = gPlayer[0].pos.y + sEarthSpawnOffsets[i].y;
            // pos.z is progress along the level path; trueZpos is where the Arwing
            // actually is, and it is what Scenery360 positions are measured against.
            gScenery360[slot].obj.pos.z = gPlayer[0].trueZpos + sEarthSpawnOffsets[i].z;
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
        sEarthDelay = 0;
        sEarthFirstSlot = -1;
        return;
    }

    // The level places the player some frames after all-range starts; spawning on
    // the first frame would anchor every chunk to the origin instead of the arena.
    if (!sEarthLoaded) {
        if (++sEarthDelay < 60) {
            return;
        }
        sEarthLoaded = true;
        Earth_LoadChunks();
        return;
    }

    // A retry clears gScenery360 without ever leaving all-range, so watch the slot
    // we claimed and rebuild the chunks once the level has taken it back.
    if ((sEarthFirstSlot >= 0) && (gScenery360[sEarthFirstSlot].obj.id != EARTH_OBJ_ID)) {
        sEarthLoaded = false;
        sEarthDelay = 0;
        sEarthFirstSlot = -1;
    }
}
