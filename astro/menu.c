/* SPDX-License-Identifier: GPL-2.0-only */
/* Menu tables and their handlers. Handlers only post worker requests or format values. */
#include <dryos.h>
#include <menu.h>
#include <lens.h>
#include "core/catalog.h"
#include "core/wcs.h"
#include "camera.h"
#include "frame_match.h"
#include "gain.h"
#include "location.h"
#include "menu.h"
#include "overlay.h"
#include "polar.h"
#include "solve_actions.h"
#include "state.h"
#include "worker.h"

#define TAB_NAME        "Astro"
#define WIDE_FIELD_CDEG 6000 /* beyond ~60 degrees the optional SKYW index helps */

static void start(astro_action action, int close_menu)
{
    if (!astro_request(action)) {
        astro_set_status("Busy: wait or press Q");
        return;
    }
    if (close_menu) {
        gui_stop_menu();
    }
}

static MENU_SELECT_FUNC(start_solve_liveview)
{
    start(ASTRO_ACTION_SOLVE_LIVEVIEW, 0);
}

static MENU_SELECT_FUNC(start_solve_file)
{
    start(ASTRO_ACTION_SOLVE_FILE, 0);
}

static MENU_SELECT_FUNC(start_guide)
{
    start(ASTRO_ACTION_GUIDE, 1);
}

static MENU_SELECT_FUNC(start_polar_first)
{
    start(ASTRO_ACTION_POLAR_FIRST, 1);
}

static MENU_SELECT_FUNC(start_polar_second)
{
    start(ASTRO_ACTION_POLAR_SECOND, 1);
}

static MENU_SELECT_FUNC(start_polar_adjust)
{
    start(ASTRO_ACTION_POLAR_ADJUST, 1);
}

static MENU_SELECT_FUNC(start_autofocus)
{
    start(ASTRO_ACTION_AUTOFOCUS, 1);
}

static MENU_SELECT_FUNC(start_measure)
{
    start(ASTRO_ACTION_MEASURE_FOCUS, 0);
}

static MENU_SELECT_FUNC(start_read_location)
{
    start(ASTRO_ACTION_READ_LOCATION, 0);
}

static MENU_SELECT_FUNC(start_frame_match)
{
    start(ASTRO_ACTION_FRAME_MATCH, 1);
}

static MENU_SELECT_FUNC(use_last_solve)
{
    astro_frame_use_last_solve();
}

static MENU_SELECT_FUNC(clear_reference)
{
    astro_frame_clear_reference();
}

static MENU_UPDATE_FUNC(reference_update)
{
    char text[40];

    astro_frame_reference_text(text, sizeof(text));
    MENU_SET_VALUE("%s", text);
}

static MENU_UPDATE_FUNC(frame_match_update)
{
    if (!astro_cfg_ref_valid) {
        MENU_SET_VALUE("no reference");
        MENU_SET_WARNING(MENU_WARN_ADVICE, "Solve a photo, then set it as the reference.");
    }
}

static MENU_UPDATE_FUNC(guide_update)
{
    if (!astro_cfg_mark_target) {
        MENU_SET_WARNING(MENU_WARN_NOT_WORKING, "Turn on Target -> Guidance first.");
    }
}

#define STATUS_MENU_BYTES 25 /* fits beside "Solve Live View (3s)" */

/* Long values cover the row name, so the menu shows a short, fresh status only. The solve log
 * window has the full story. */
static MENU_UPDATE_FUNC(status_update)
{
    char text[STATUS_MENU_BYTES];

    if (astro_busy() || astro_status_recent()) {
        snprintf(text, sizeof(text), "%s", astro_status());
        MENU_SET_VALUE("%s", text);
    }
}

/* Caret editing (as in the intervalometer) needs UNIT_DEC and no .select. The digit count
 * follows the catalogue's range. */
static MENU_UPDATE_FUNC(target_number_update)
{
    int catalogue = astro_cfg_target_catalogue;
    char name[ASTRO_NAME_BYTES];

    if (catalogue < 1 || catalogue > ASTRO_CAT_COUNT) {
        catalogue = ASTRO_CAT_M;
    }
    entry->max = astro_catalogue_max[catalogue];
    if (astro_cfg_target_number > entry->max) {
        astro_cfg_target_number = entry->max;
    }
    if (astro_cfg_target_number < 1) {
        astro_cfg_target_number = 1;
    }
    astro_designation(catalogue, astro_cfg_target_number, name, sizeof(name));
    MENU_SET_RINFO("%s", name);
}

static MENU_UPDATE_FUNC(target_object_update)
{
    if (!astro_target_current()) {
        MENU_SET_VALUE("...");
        return;
    }
    MENU_SET_VALUE("%s", astro_target_info());
    if (astro_target_get()->valid) {
        MENU_SET_RINFO("%d'", (astro_target_get()->object.major_axis + 5) / 10);
    }
}

static MENU_UPDATE_FUNC(target_sky_update)
{
    astro_site site;
    astro_horizontal h;
    static const char *const states[] = {"sets in", "rises in", "never sets", "never rises"};

    if (!astro_target_current() || !astro_target_get()->valid) {
        MENU_SET_VALUE("--");
        return;
    }
    if (!astro_location_site(&site)) {
        MENU_SET_VALUE("set Location first");
        return;
    }
    const astro_dso *o = &astro_target_get()->object;
    astro_altaz(&site, astro_now_utc(), o->ra, o->dec, &h);
    MENU_SET_VALUE("alt %d az %d", (int)(h.altitude / 10000), h.azimuth / 10000);
    if (h.visibility <= ASTRO_SKY_DOWN) {
        MENU_SET_RINFO("%s %dh%02d", states[h.visibility], h.minutes_to_change / 60,
                       h.minutes_to_change % 60);
    } else {
        MENU_SET_RINFO("%s", states[h.visibility]);
    }
    if (h.altitude < 0) {
        MENU_SET_WARNING(MENU_WARN_ADVICE, "The target is below the horizon now.");
    }
}

static MENU_UPDATE_FUNC(polar_update)
{
    static const char *const steps[] = {"Capture A next", "A done: rotate RA, capture B",
                                        "Axis known: Adjust"};
    MENU_SET_VALUE("%s",
                   astro_polar_step() == 2 ? astro_polar_summary() : steps[astro_polar_step()]);
}

static MENU_UPDATE_FUNC(location_source_update)
{
    char source[64];
    astro_location_source(source, sizeof(source));
    MENU_SET_VALUE("%s", source);
    if (astro_location_age_minutes() > ASTRO_LOCATION_STALE_HOURS * 60) {
        MENU_SET_WARNING(MENU_WARN_ADVICE, "Old fix: take a new photo with GPS on and re-read.");
    }
}

static MENU_UPDATE_FUNC(location_position_update)
{
    int lat = astro_cfg_latitude < 0 ? -astro_cfg_latitude : astro_cfg_latitude;
    int lon = astro_cfg_longitude < 0 ? -astro_cfg_longitude : astro_cfg_longitude;

    if (!astro_cfg_location_valid) {
        MENU_SET_VALUE("--");
        return;
    }
    MENU_SET_VALUE("%d.%04d%s %d.%04d%s", lat / 10000, lat % 10000,
                   astro_cfg_latitude < 0 ? "S" : "N", lon / 10000, lon % 10000,
                   astro_cfg_longitude < 0 ? "W" : "E");
}

static MENU_UPDATE_FUNC(utc_offset_update)
{
    int offset = astro_cfg_utc_offset;
    int magnitude = offset < 0 ? -offset : offset;
    MENU_SET_VALUE("UTC%s%d:%02d", offset < 0 ? "-" : "+", magnitude / 60, magnitude % 60);
}

static MENU_UPDATE_FUNC(focal_source_update)
{
    if (astro_cfg_focal_source == ASTRO_FOCAL_FROM_LENS) {
        if (lens_info.lens_exists && lens_info.focal_len) {
            MENU_SET_RINFO("%dmm", lens_info.focal_len);
        } else {
            MENU_SET_WARNING(MENU_WARN_NOT_WORKING, "Lens reports no focal length: use Manual.");
        }
    } else if (astro_cfg_focal_source == ASTRO_FOCAL_MANUAL) {
        MENU_SET_RINFO("%dmm", astro_cfg_focal_mm);
    }
}

static MENU_UPDATE_FUNC(manual_focal_update)
{
    int fov = astro_lens_fov_cdeg(astro_sensor_width_x10(), astro_cfg_focal_mm);
    MENU_SET_RINFO("FOV %d.%d", fov / 100, fov / 10 % 10);
    if (astro_cfg_focal_source != ASTRO_FOCAL_MANUAL) {
        MENU_SET_WARNING(MENU_WARN_ADVICE, "Used only when the focal source is Manual.");
    } else if (fov > WIDE_FIELD_CDEG) {
        MENU_SET_WARNING(MENU_WARN_ADVICE, "Over 60 deg: install the optional SKYW index.");
    }
}

static MENU_UPDATE_FUNC(last_ra_update)
{
    const astro_solve_result *r = astro_last_solution();
    MENU_SET_VALUE("%s", r->valid ? r->ra_text : "--");
}

static MENU_UPDATE_FUNC(last_dec_update)
{
    const astro_solve_result *r = astro_last_solution();
    MENU_SET_VALUE("%s", r->valid ? r->dec_text : "--");
}

static MENU_UPDATE_FUNC(last_lens_update)
{
    const astro_solve_result *r = astro_last_solution();
    MENU_SET_VALUE("%s", r->valid ? r->lens_text : "--");
}

/* "Camera button" is the body's spare button from cameras.toml; some bodies have none. */
static MENU_UPDATE_FUNC(shortcut_update)
{
    const astro_camera_profile *camera = astro_camera();

    if (astro_cfg_focus_shortcut != ASTRO_SHORTCUT_BUTTON) {
        return;
    }
    if (camera->focus_key == ASTRO_NO_KEY) {
        MENU_SET_VALUE("Off");
        MENU_SET_WARNING(MENU_WARN_NOT_WORKING, "This camera has no spare button for it.");
    } else {
        MENU_SET_VALUE("%s button", camera->focus_key_name);
    }
}

static struct menu_entry astro_menu[] = {
    {
        .name = "Solve Live View (3s)",
        .select = start_solve_liveview,
        .update = status_update,
        .icon_type = IT_ACTION,
        .help = "Waits 3 s, solves the 1x photo Live View without hints, shows the result.",
    },
    {
        .name = "Target",
        .select = menu_open_submenu,
        .update = target_object_update,
        .help = "Pick a catalogue target to mark and guide to.",
        .children =
            (struct menu_entry[]){
                {.name = "Catalogue",
                 .priv = &astro_cfg_target_catalogue,
                 .min = 1,
                 .max = ASTRO_CAT_COUNT,
                 .choices = CHOICES("Messier (M)", "NGC", "IC", "Caldwell (C)", "Barnard (B)",
                                    "Sharpless (Sh2)")},
                {.name = "Number",
                 .priv = &astro_cfg_target_number,
                 .min = 1,
                 .max = 110,
                 .unit = UNIT_DEC,
                 .update = target_number_update,
                 .help = "LEFT/RIGHT: digit, UP/DOWN: value. M 1-110, NGC 1-7840, IC 1-5386."},
                {.name = "Object",
                 .update = target_object_update,
                 .icon_type = IT_ACTION,
                 .help = "Catalogue entry for this number (ML/ASTRO/DSO.BIN)."},
                {.name = "In the sky now",
                 .update = target_sky_update,
                 .icon_type = IT_ACTION,
                 .help = "Altitude/azimuth and rise/set, from Location and the camera clock."},
                {.name = "Guidance",
                 .priv = &astro_cfg_mark_target,
                 .max = 1,
                 .help = "Mark the target, or an arrow to it, on solved frames; enables Guide."},
                MENU_EOL,
            },
    },
    {
        .name = "Guide to target",
        .select = start_guide,
        .update = guide_update,
        .icon_type = IT_ACTION,
        .help = "1x photo Live View: re-solves and points to the target. Q stops.",
        .help2 = "Set the lens focal length for much faster updates.",
    },
    {
        .name = "Frame match",
        .select = menu_open_submenu,
        .update = frame_match_update,
        .help = "Return to the position and rotation of an earlier, solved photo.",
        .children =
            (struct menu_entry[]){
                {.name = "Reference", .update = reference_update, .icon_type = IT_ACTION},
                {.name = "Use last solve",
                 .select = use_last_solve,
                 .icon_type = IT_ACTION,
                 .help = "Solve the photo first (gallery, Live View or file), then pick this.",
                 .help2 = "In the gallery, SET on the solve result also sets it."},
                {.name = "Match in Live View",
                 .select = start_frame_match,
                 .icon_type = IT_ACTION,
                 .help = "1x photo Live View: shows the reference frame and how to move. Q stops."},
                {.name = "Clear reference", .select = clear_reference, .icon_type = IT_ACTION},
                MENU_EOL,
            },
    },
    {
        .name = "Star focus",
        .select = menu_open_submenu,
        .help = "Star-size readout and lens autofocus on a star (no Bahtinov mask).",
        .children =
            (struct menu_entry[]){
                {.name = "Star size readout",
                 .priv = &astro_cfg_focus_readout,
                 .max = 1,
                 .help =
                     "Live score without moving the lens. Same star, 5x/10x; lower is sharper."},
                {.name = "Measure once", .select = start_measure, .icon_type = IT_ACTION},
                {.name = "Autofocus",
                 .select = start_autofocus,
                 .icon_type = IT_ACTION,
                 .help = "Electronic AF lens only. Moves the focus motor. Not hardware validated."},
                {.name = "Motor step size",
                 .priv = &astro_cfg_focus_step,
                 .min = 1,
                 .max = 3,
                 .help = "Canon focus step command. Start at 1."},
                {.name = "Scan radius",
                 .priv = &astro_cfg_focus_radius,
                 .min = 1,
                 .max = 6,
                 .help = "Motor steps tested on each side of the start position."},
                {.name = "Shortcut",
                 .priv = &astro_cfg_focus_shortcut,
                 .max = 3,
                 .update = shortcut_update,
                 .choices = CHOICES("Off", "Double half-press", "INFO button", "Camera button"),
                 .help = "Photo Live View only. Off keeps the Canon button behaviour."},
                MENU_EOL,
            },
    },
    {
        .name = "Live View gain",
        .select = astro_gain_select,
        .update = astro_gain_update,
        .icon_type = IT_PERCENT_OFF,
        .depends_on = DEP_PHOTO_MODE,
        .help = "Brighter preview, photo mode only. ISO and saved photos are unchanged.",
        .help2 = "LEFT/RIGHT: Off to +6 EV (64x). Also amplifies noise.",
    },
    {
        .name = "Overlays",
        .select = menu_open_submenu,
        .help = "What is drawn on solved frames.",
        .children =
            (struct menu_entry[]){
                {.name = "Star names",
                 .priv = &astro_cfg_overlay_stars,
                 .max = 1,
                 .help = "Stars to magnitude 4.5, with Greek Bayer letters."},
                {.name = "Deep-sky outlines", .priv = &astro_cfg_overlay_dso, .max = 1},
                {.name = "Constellations", .priv = &astro_cfg_overlay_constellations, .max = 1},
                {.name = "DSO magnitude limit",
                 .priv = &astro_cfg_overlay_dso_mag,
                 .min = 6,
                 .max = 14,
                 .help = "NGC/IC brighter than this are outlined. M and C always are."},
                MENU_EOL,
            },
    },
    {
        .name = "Location",
        .select = menu_open_submenu,
        .update = location_position_update,
        .help = "Get the current location from the latest photo taken.",
        .children =
            (struct menu_entry[]){
                {.name = "Read newest photo",
                 .select = start_read_location,
                 .icon_type = IT_ACTION,
                 .help = "Take any photo with GPS on first. Shows which photo and how old."},
                {.name = "Source", .update = location_source_update, .icon_type = IT_ACTION},
                {.name = "Position", .update = location_position_update, .icon_type = IT_ACTION},
                {.name = "Clock offset",
                 .priv = &astro_cfg_utc_offset,
                 .min = -840,
                 .max = 840,
                 .update = utc_offset_update,
                 .help = "Camera clock minus UTC, learned from GPS time. Adjust if wrong."},
                MENU_EOL,
            },
    },
    {
        .name = "Lens",
        .select = menu_open_submenu,
        .help = "Set the lens focal length, optional for plate solving.",
        .children =
            (struct menu_entry[]){
                {.name = "Focal length source",
                 .priv = &astro_cfg_focal_source,
                 .max = 2,
                 .choices = CHOICES("None (blind)", "From lens", "Manual"),
                 .update = focal_source_update,
                 .help = "A hint restricts the image scale to +-30 %: much faster solves."},
                {.name = "Manual focal length",
                 .priv = &astro_cfg_focal_mm,
                 .min = 4,
                 .max = 1200,
                 .unit = UNIT_DEC,
                 .update = manual_focal_update,
                 .help = "Millimetres. LEFT/RIGHT: digit, UP/DOWN: value."},
                MENU_EOL,
            },
    },
    {
        .name = "Last solve",
        .select = menu_open_submenu,
        .update = last_ra_update,
        .help = "Frame centre of the most recent solution (J2000).",
        .children =
            (struct menu_entry[]){
                {.name = "Status", .update = status_update, .icon_type = IT_ACTION},
                {.name = "RA", .update = last_ra_update, .icon_type = IT_ACTION},
                {.name = "Dec", .update = last_dec_update, .icon_type = IT_ACTION},
                {.name = "Measured lens",
                 .update = last_lens_update,
                 .icon_type = IT_ACTION,
                 .help = "Focal length measured from the solved field."},
                MENU_EOL,
            },
    },
    {
        .name = "Polar alignment",
        .select = menu_open_submenu,
        .update = polar_update,
        .help = "Two solves around the RA axis find the mount axis; then adjust alt/az.",
        .children =
            (struct menu_entry[]){
                {.name = "1. Capture A",
                 .select = start_polar_first,
                 .icon_type = IT_ACTION,
                 .help = "Point near the pole, tracking on. Solves the Live View frame."},
                {.name = "2. Rotate RA, capture B",
                 .select = start_polar_second,
                 .icon_type = IT_ACTION,
                 .help = "Turn the RA axis 30-90 degrees first."},
                {.name = "3. Adjust",
                 .select = start_polar_adjust,
                 .icon_type = IT_ACTION,
                 .help = "Turn the alt/az knobs until the reticle sits in the circle. Q stops.",
                 .help2 = "Read the location first for altitude/azimuth directions."},
                {.name = "Status", .update = polar_update, .icon_type = IT_ACTION},
                MENU_EOL,
            },
    },
    {
        .name = "Developer",
        .select = menu_open_submenu,
        .help = "Test files for QEMU and the desktop. Not needed for shooting.",
        .children =
            (struct menu_entry[]){
                {.name = "Solve image file",
                 .select = start_solve_file,
                 .icon_type = IT_ACTION,
                 .help = "Solves the selected 360x240 8-bit PGM from ML/ASTRO."},
                {.name = "Image file",
                 .priv = &astro_cfg_test_image,
                 .max = 5,
                 .choices = CHOICES("TEST.PGM", "TEST1.PGM", "TEST2.PGM", "TEST3.PGM", "RAW1.PGM",
                                    "RAW2.PGM")},
                MENU_EOL,
            },
    },
};

void astro_menu_add(void)
{
    menu_add(TAB_NAME, astro_menu, COUNT(astro_menu));
}

void astro_menu_remove(void)
{
    menu_remove(TAB_NAME, astro_menu, COUNT(astro_menu));
}
