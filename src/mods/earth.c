/*
 * File: earth.c
 * System: Earth
 * Description: Imports real-world geometry (photogrammetry tiles) into all-range
 * levels as Scenery360 objects, so it collides and takes fire like native scenery.
 * The zone is a real district imported from OpenStreetMap by
 * tools/earth-import/osm2earth.py into mods/earth.o2r.
 * This is not part of the original game.
 */

#include "global.h"
#include "sf64object.h"

// Empty entry in gObjectInfo, kept free by the game. The table is not const, so
// the mod claims this slot at runtime instead of patching fox_edata_info.c.
#define EARTH_OBJ_ID OBJ_SCENERY_UNK_155

// Every vanilla model is an archive path rather than a Gfx array (see
// include/assets/*.h), and Scenery360_Draw feeds info.dList straight to
// gSPDisplayList, which resolves the __OTR__ prefix. The zone therefore lives in
// mods/earth.o2r, built by tools/earth-import/osm2earth.py.
//
// The whole zone is a single display list, so it costs one Scenery360 slot and one
// of the 4608 matrices a frame allows, whatever the building count. Positions are
// baked into the geometry relative to the zone origin, so there is nothing to
// place here but that origin.
static const ALIGN_ASSET(2) char sEarthZoneOtr[] = "__OTR__earth/zoneDL";

static bool sEarthLoaded = false;
static s32 sEarthDelay = 0;
static s32 sEarthFirstSlot = -1;
static bool sEarthMissing = false;

static void Earth_LoadChunks(void) {
    ObjectInfo* info = &gObjectInfo[EARTH_OBJ_ID];
    s32 slot;

    // gSPDisplayList resolves the __OTR__ path and dereferences the result without
    // checking it, so an archive that is absent, stale or holding a different zone
    // takes the whole game down inside the draw loop. Resolve it here first, where
    // a miss can simply mean "stay vanilla".
    if (LOAD_ASSET_RAW(sEarthZoneOtr) == NULL) {
        sEarthMissing = true;
        return;
    }

    info->dList = (Gfx*) sEarthZoneOtr;
    info->action = NULL;
    // No hitbox yet: one box around a whole district would be meaningless. Real
    // per-building collision is M4.
    info->hitbox = gNoHitbox;
    info->damage = 40;

    for (slot = 0; slot < 200; slot++) {
        if (gScenery360[slot].obj.status != OBJ_FREE) {
            continue;
        }

        sEarthFirstSlot = slot;
        Scenery360_Initialize(&gScenery360[slot]);
        gScenery360[slot].obj.status = OBJ_ACTIVE;
        gScenery360[slot].obj.id = EARTH_OBJ_ID;
        // Drop the district around the player, sitting on the level's ground.
        gScenery360[slot].obj.pos.x = gPlayer[0].pos.x;
        gScenery360[slot].obj.pos.y = gGroundHeight;
        // pos.z is progress along the level path; trueZpos is where the Arwing
        // actually is, and it is what Scenery360 positions are measured against.
        gScenery360[slot].obj.pos.z = gPlayer[0].trueZpos;
        gScenery360[slot].obj.rot.y = 0.0f;
        Object_SetInfo(&gScenery360[slot].info, gScenery360[slot].obj.id);
        break;
    }
}

void Earth_Update(void) {
    // Nothing to import: leave the level exactly as vanilla rather than retrying
    // the lookup every frame.
    if (sEarthMissing) {
        return;
    }

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
