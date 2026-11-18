/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * AstroML: plate solving, target guiding, frame matching, polar alignment and star focus for
 * Magic Lantern. The supported cameras are listed in cameras.toml. Module entry points and
 * callbacks only; the work is done on the worker task (worker.c).
 */
#include <module.h>
#include <dryos.h>
#include <menu.h>
#include "camera.h"
#include "menu.h"
#include "playback.h"
#include "result_view.h"
#include "star_focus.h"
#include "state.h"
#include "usb.h"
#include "worker.h"

static unsigned int astro_init(void)
{
    const astro_camera_profile *camera = astro_camera_detect();
    if (!camera) {
        return CBR_RET_ERROR;
    }
    if (camera->focus_key == ASTRO_NO_KEY && astro_cfg_focus_shortcut == ASTRO_SHORTCUT_BUTTON) {
        astro_cfg_focus_shortcut = ASTRO_SHORTCUT_OFF; /* no spare button on this body */
    }
    if (!astro_worker_start()) {
        return CBR_RET_ERROR;
    }
    astro_usb_register();
    astro_menu_add();
    return 0;
}

static unsigned int astro_deinit(void)
{
    if (!astro_worker_stop()) {
        return CBR_RET_ERROR; /* still working: refuse to unload */
    }
    astro_usb_shutdown();
    astro_result_release();
    astro_menu_remove();
    return 0;
}

/* Translated keys. Return 1 to let Canon handle the key, 0 to consume it. */
static unsigned int astro_keypress(unsigned int key)
{
    if (astro_result_visible()) {
        if (key == MODULE_KEY_Q || key == MODULE_KEY_PRESS_SET) {
            astro_result_hide();
            gui_open_menu();
            return 0;
        }
        return 1;
    }
    if (astro_busy() && key == MODULE_KEY_Q) {
        astro_cancel();
        return 0;
    }
    if (!astro_playback_key(key)) {
        return 0;
    }
    return astro_focus_key(key);
}

static unsigned int astro_keypress_raw(unsigned int event_pointer)
{
    return astro_focus_raw_key(event_pointer);
}

/* Runs on ML's 1 Hz timer: keep it short, anything heavy goes to the worker. */
static unsigned int astro_seconds(unsigned int unused)
{
    if (astro_result_visible()) {
        astro_result_redraw();
        return 0;
    }
    astro_playback_tick();
    astro_focus_tick();
    return 0;
}

MODULE_INFO_START()
MODULE_INIT(astro_init)
MODULE_DEINIT(astro_deinit)
MODULE_INFO_END()

MODULE_CBRS_START()
MODULE_CBR(CBR_KEYPRESS, astro_keypress, 0)
MODULE_CBR(CBR_KEYPRESS_RAW, astro_keypress_raw, 0)
MODULE_CBR(CBR_SECONDS_CLOCK, astro_seconds, 0)
MODULE_CBRS_END()
