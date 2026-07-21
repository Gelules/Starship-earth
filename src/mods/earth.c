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

// One axis-aligned box per building, from the OSM footprints, so flying into a
// building takes damage through the stock Scenery360 hitbox path. The archive
// stores them at bake scale as a flat float array [count, then 6 per building in
// Hitbox order]; osm2earth.py writes it, GenericArray f32.
static const ALIGN_ASSET(2) char sEarthBoxesOtr[] = "__OTR__earth/zoneBox";

// Player_CheckHitboxCollision reads count as the first float then 6 floats per
// box, so the live hitbox is exactly the archive array with every offset and size
// multiplied by EARTH_SCALE (the count is left alone). Rebuilt when the scale
// knob moves so collision tracks the geometry. Cap is a backstop; a 2 km city is
// ~1-3k buildings.
#define EARTH_MAX_BOXES 4096
static f32 sEarthHitbox[1 + 6 * EARTH_MAX_BOXES];
static const f32* sEarthBoxSrc = NULL;
static s32 sEarthBoxCount = 0;
static f32 sEarthBuiltScale = 0.0f;

static bool sEarthLoaded = false;
static s32 sEarthFirstSlot = -1;
static bool sEarthMissing = false;

static void Earth_BuildHitbox(f32 scale) {
    s32 n = sEarthBoxCount;
    s32 i;

    if (n > EARTH_MAX_BOXES) {
        printf("[earth] %d buildings over hitbox cap %d; collision limited to the first %d\n", n, EARTH_MAX_BOXES,
               EARTH_MAX_BOXES);
        n = EARTH_MAX_BOXES;
    }
    sEarthHitbox[0] = n;
    for (i = 0; i < 6 * n; i++) {
        sEarthHitbox[1 + i] = sEarthBoxSrc[1 + i] * scale;
    }
    sEarthBuiltScale = scale;
}

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
    info->damage = 40;

    // One hitbox per building, scaled to match the geometry. A zone with no box
    // resource (an older archive) simply keeps flying-through, no collision.
    sEarthBoxSrc = (const f32*) LOAD_ASSET_RAW(sEarthBoxesOtr);
    if (sEarthBoxSrc != NULL) {
        sEarthBoxCount = (s32) sEarthBoxSrc[0];
        Earth_BuildHitbox(CVarGetFloat("gEarthScale", 8.0f));
        info->hitbox = sEarthHitbox;
    } else {
        info->hitbox = gNoHitbox;
    }

    for (slot = 0; slot < 200; slot++) {
        if (gScenery360[slot].obj.status != OBJ_FREE) {
            continue;
        }

        sEarthFirstSlot = slot;
        Scenery360_Initialize(&gScenery360[slot]);
        gScenery360[slot].obj.status = OBJ_ACTIVE;
        gScenery360[slot].obj.id = EARTH_OBJ_ID;
        // The all-range arena is a square centred on the world origin (Player_-
        // CheckBounds360: |pos.x|,|pos.z| < 12500 on Fortuna), so centre the
        // district there too. The player spawns inside it, and the U-turn boundary
        // then sits symmetrically around the city instead of leaving a slab of
        // empty Fortuna on one side. A radius-1000 m zone at scale 8 is 8000 units,
        // well inside 12500. Ground height keeps the building bases on the deck.
        gScenery360[slot].obj.pos.x = 0.0f;
        gScenery360[slot].obj.pos.y = gGroundHeight;
        gScenery360[slot].obj.pos.z = 0.0f;
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
        sEarthFirstSlot = -1;
        return;
    }

    // A level is already in all-range mode during its intro flyby, where the
    // Arwing is nowhere near the arena. Wait until the player actually has
    // control, otherwise the district lands behind the opening cutscene.
    if (!sEarthLoaded) {
        if (gPlayer[0].state != PLAYERSTATE_ACTIVE) {
            return;
        }
        sEarthLoaded = true;
        Earth_LoadChunks();
        return;
    }

    // Fortuna sets its far plane to 12800 at handover, which fogs an 8x-scaled
    // district into the haze well before its far edge and clips the far side once
    // it spans more than that. Hold the plane at the value Fortuna itself uses in
    // open play: the N64 fog is fixed in normalised depth, so a farther plane also
    // pushes the haze back in world space and reveals the city. gProjectFar feeds
    // guPerspective every frame, so this has to be reasserted every frame.
    gProjectFar = 30000.0f;

    // Keep the collision boxes in step with the live scale knob.
    if (sEarthBoxSrc != NULL) {
        f32 scale = CVarGetFloat("gEarthScale", 8.0f);
        if (scale != sEarthBuiltScale) {
            Earth_BuildHitbox(scale);
        }
    }

    // A retry clears gScenery360 without ever leaving all-range, so watch the slot
    // we claimed and rebuild the chunks once the level has taken it back.
    if ((sEarthFirstSlot >= 0) && (gScenery360[sEarthFirstSlot].obj.id != EARTH_OBJ_ID)) {
        sEarthLoaded = false;
        sEarthFirstSlot = -1;
    }
}
