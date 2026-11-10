/* SPDX-License-Identifier: GPL-2.0-only */
/* Playback solving reads the displayed preview, not the original RAW. The photo itself is
 * never modified; annotations are bitmap overlays only. */
#include <dryos.h>
#include <bmp.h>
#include <gui-common.h>
#include <menu.h>
#include <module.h>
#include <notify_box.h>
#include "capture.h"
#include "draw.h"
#include "overlay.h"
#include "playback.h"
#include "solve.h"
#include "solve_actions.h"
#include "state.h"
#include "worker.h"

#define SOLVE_BUDGET_MS 45000
#define NOTIFY_MS       4500

typedef enum { VIEW_IDLE = 0, VIEW_PROMPT, VIEW_RUNNING, VIEW_RESULTS } view_state;

static struct {
    volatile view_state state;
    uint32_t signature; /* of the photo being solved */
    astro_solve_result result;
    astro_scene scene;
} pb;

/* paint_* draw without locking; draw_* take the bitmap lock around them. */
static void paint_prompt(void)
{
    bmp_fill(COLOR_BLACK, 40, 174, 640, 122);
    bmp_printf(FONT(FONT_LARGE, COLOR_YELLOW, COLOR_BLACK), 56, 183, "Solve this photo?");
    bmp_printf(FONT(FONT_MED, COLOR_WHITE, COLOR_BLACK), 56, 226, "SET: solve    Q: cancel");
    bmp_printf(FONT(FONT_SMALL, COLOR_WHITE, COLOR_BLACK), 56, 260,
               "No sky coordinates or lens settings needed");
}

static void paint_progress(void)
{
    bmp_fill(COLOR_BLACK, 12, 407, 695, 43);
    bmp_printf(FONT(FONT_MED, COLOR_YELLOW, COLOR_BLACK), 20, 414, "Finding sky: %s",
               astro_status());
}

static void paint_results(void)
{
    astro_draw_scene(&pb.scene, &astro_liveview_map, 0);
    astro_draw_matches(&pb.result, &astro_liveview_map);
    bmp_fill(COLOR_BLACK, 8, 6, 345, 102);
    bmp_printf(FONT(FONT_MED, COLOR_YELLOW, COLOR_BLACK), 16, 12, "SKY FOUND (%d stars)",
               pb.result.sky.matched);
    bmp_printf(FONT(FONT_MED, COLOR_WHITE, COLOR_BLACK), 16, 43, "RA: %s", pb.result.ra_text);
    bmp_printf(FONT(FONT_MED, COLOR_WHITE, COLOR_BLACK), 16, 72, "Dec: %s", pb.result.dec_text);
    bmp_printf(FONT(FONT_SMALL, COLOR_WHITE, COLOR_BLACK), 12, 450,
               "Q: hide  |  LEFT/RIGHT: other photo");
}

static void draw_prompt(void)
{
    BMP_LOCK(paint_prompt());
}

static void draw_progress(void)
{
    BMP_LOCK(paint_progress());
}

static void draw_results(void)
{
    BMP_LOCK(paint_results());
}

void astro_playback_run_solve(void)
{
    uint8_t *image = astro_image();

    pb.signature = astro_playback_signature();
    if (!is_pure_play_photo_mode() || !astro_capture_display(image)) {
        pb.state = VIEW_IDLE;
        astro_set_status("Photo playback closed");
        return;
    }
    astro_solve_status status = astro_solve_frame(image, SOLVE_BUDGET_MS, &pb.result);
    if (status == ASTRO_SOLVE_OK) {
        astro_build_overlay(&pb.result, &pb.scene);
    }
    astro_solve_release();

    if (status != ASTRO_SOLVE_OK) {
        pb.state = VIEW_IDLE;
        astro_set_status("%s", astro_solve_status_text(status));
    } else if (!is_pure_play_photo_mode() || astro_playback_signature() != pb.signature) {
        pb.state = VIEW_IDLE;
        astro_set_status("Photo changed; result hidden");
    } else {
        astro_remember_solution(&pb.result);
        astro_set_status("Solved %d stars", pb.result.sky.matched);
        pb.state = VIEW_RESULTS;
        draw_results();
        return;
    }
    NotifyBox(NOTIFY_MS, "Astro: %s", astro_status());
}

static void close_view(void)
{
    pb.state = VIEW_IDLE;
    clrscr();
}

unsigned int astro_playback_key(unsigned int key)
{
    if (!is_pure_play_photo_mode() || gui_menu_shown()) {
        if (pb.state != VIEW_RUNNING) {
            pb.state = VIEW_IDLE;
        }
        return 1;
    }
    switch (pb.state) {
        case VIEW_PROMPT:
            if (key == MODULE_KEY_PRESS_SET && astro_request(ASTRO_ACTION_SOLVE_PLAYBACK)) {
                pb.state = VIEW_RUNNING;
                clrscr();
                draw_progress();
                return 0;
            }
            close_view();
            return key == MODULE_KEY_Q ? 0 : 1;
        case VIEW_RESULTS:
            if (key == MODULE_KEY_INFO) {
                return 1;
            }
            close_view();
            return key == MODULE_KEY_Q ? 0 : 1;
        case VIEW_RUNNING:
            if (key == MODULE_KEY_Q) {
                astro_cancel();
                return 0;
            }
            return 1;
        default:
            if (key == MODULE_KEY_Q && !astro_busy()) {
                pb.state = VIEW_PROMPT;
                draw_prompt();
                return 0;
            }
            return 1;
    }
}

void astro_playback_tick(void)
{
    if (pb.state == VIEW_IDLE) {
        return;
    }
    if (!is_pure_play_photo_mode()) {
        if (pb.state != VIEW_RUNNING) {
            pb.state = VIEW_IDLE;
        }
        return;
    }
    if (pb.state == VIEW_PROMPT) {
        draw_prompt();
    } else if (pb.state == VIEW_RUNNING) {
        draw_progress();
    } else {
        draw_results();
    }
}
