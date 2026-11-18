/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * The reference is a plate solution, not pixels: matching only needs the sky position and
 * orientation, so it survives a restart and works across lenses (a different field of view is
 * reported, not hidden).
 */
#include <dryos.h>
#include <bmp.h>
#include <notify_box.h>
#include <string.h>
#include "core/frame_match.h"
#include "camera.h"
#include "capture.h"
#include "draw.h"
#include "frame_match.h"
#include "live.h"
#include "solve_actions.h"
#include "state.h"

#define HUD_TEXT       96
#define CROSS_Q4       (12 * 16) /* half-size of the reference centre cross */
#define LENS_TOLERANCE 100       /* per mille: wider differences are reported */
#define INT16_LIMIT    32000     /* scene coordinates are int16 Q4 */
#define NOTIFY_MS      4000

static const char *const source_names[] = {"Live View", "gallery", "file", "companion"};

#define MATCHED_FRAMES 2 /* consecutive frames within tolerance before reporting a match */

static struct {
    astro_scene shown; /* what is on screen, so it can be erased exactly */
    astro_scene next;
    int drawn;
} view;

/* Read by the USB task; each field is written whole by the worker. */
static struct {
    volatile int running;
    volatile int streak;
    volatile int matched;
    volatile int rotation_valid;
    volatile uint32_t separation;
    volatile int32_t rotation_ddeg;
    volatile int panel; /* -1: the reference is not a planned panel */
} live = {.panel = -1};

static void load_reference(astro_wcs *wcs)
{
    wcs->ra0 = (uint32_t)astro_cfg_ref_ra0;
    wcs->dec0 = astro_cfg_ref_dec0;
    wcs->m_q24[0] = astro_cfg_ref_m0;
    wcs->m_q24[1] = astro_cfg_ref_m1;
    wcs->m_q24[2] = astro_cfg_ref_m2;
    wcs->m_q24[3] = astro_cfg_ref_m3;
    wcs->t_q16[0] = astro_cfg_ref_t0;
    wcs->t_q16[1] = astro_cfg_ref_t1;
}

int astro_frame_set_reference(const astro_solve_result *result, astro_reference_source source)
{
    const astro_wcs *wcs = &result->sky.wcs;

    if (!result->valid) {
        return 0;
    }
    astro_cfg_ref_ra0 = (int)wcs->ra0;
    astro_cfg_ref_dec0 = wcs->dec0;
    astro_cfg_ref_m0 = wcs->m_q24[0];
    astro_cfg_ref_m1 = wcs->m_q24[1];
    astro_cfg_ref_m2 = wcs->m_q24[2];
    astro_cfg_ref_m3 = wcs->m_q24[3];
    astro_cfg_ref_t0 = wcs->t_q16[0];
    astro_cfg_ref_t1 = wcs->t_q16[1];
    astro_cfg_ref_fov = result->sky.fov_cdeg;
    astro_cfg_ref_source = source;
    astro_cfg_ref_valid = 1;
    live.panel = -1;
    astro_set_status("Reference set: %s %s", result->ra_text, result->dec_text);
    return 1;
}

void astro_frame_set_reference_wcs(const astro_wcs *wcs, int fov_cdeg, int panel)
{
    astro_cfg_ref_ra0 = (int)wcs->ra0;
    astro_cfg_ref_dec0 = wcs->dec0;
    astro_cfg_ref_m0 = wcs->m_q24[0];
    astro_cfg_ref_m1 = wcs->m_q24[1];
    astro_cfg_ref_m2 = wcs->m_q24[2];
    astro_cfg_ref_m3 = wcs->m_q24[3];
    astro_cfg_ref_t0 = wcs->t_q16[0];
    astro_cfg_ref_t1 = wcs->t_q16[1];
    astro_cfg_ref_fov = fov_cdeg;
    astro_cfg_ref_source = ASTRO_REFERENCE_COMPANION;
    astro_cfg_ref_valid = 1;
    live.panel = panel;
    live.streak = 0;
    live.matched = 0;
    astro_set_status("Panel %d set by the companion", panel);
}

void astro_frame_match_status(astro_ptp_status *status)
{
    status->match_running = live.running;
    status->match_matched = live.matched;
    status->match_rotation_valid = live.rotation_valid;
    status->match_separation = live.separation;
    status->match_rotation_ddeg = live.rotation_ddeg;
    status->target_panel = live.panel;
}

void astro_frame_use_last_solve(void)
{
    const astro_solve_result *last = astro_last_solution();

    if (!astro_frame_set_reference(last, last->source)) {
        astro_set_status("Solve a photo first");
    }
}

void astro_frame_clear_reference(void)
{
    astro_cfg_ref_valid = 0;
    astro_set_status("Reference cleared");
}

void astro_frame_reference_text(char *out, int size)
{
    astro_wcs wcs;
    uint32_t ra;
    int32_t dec;

    if (!astro_cfg_ref_valid) {
        snprintf(out, size, "none");
        return;
    }
    load_reference(&wcs);
    if (!astro_wcs_unproject(&wcs, ASTRO_IMAGE_WIDTH * 8, ASTRO_IMAGE_HEIGHT * 8, &ra, &dec)) {
        snprintf(out, size, "unusable: set it again");
        return;
    }
    uint32_t seconds_of_time = (ra * 24 + 500) / 1000; /* 1e-4 degree -> seconds of RA */
    uint32_t minutes_of_time = (seconds_of_time + 30) / 60;
    int dec_abs = dec < 0 ? -dec : dec;
    int source = astro_cfg_ref_source >= 0 && astro_cfg_ref_source <= ASTRO_REFERENCE_COMPANION
                     ? astro_cfg_ref_source
                     : ASTRO_REFERENCE_LIVEVIEW;
    /* Short: a longer menu value covers the row name. */
    snprintf(out, size, "%02dh%02dm %s%d.%d (%s)", minutes_of_time / 60 % 24, minutes_of_time % 60,
             dec < 0 ? "-" : "+", dec_abs / 10000, dec_abs / 1000 % 10, source_names[source]);
}

static int fits_int16(int32_t value)
{
    return value > -INT16_LIMIT && value < INT16_LIMIT;
}

static void add_line(astro_scene *s, int32_t x1, int32_t y1, int32_t x2, int32_t y2)
{
    if (s->nlines < ASTRO_SCENE_LINES && fits_int16(x1) && fits_int16(y1) && fits_int16(x2) &&
        fits_int16(y2)) {
        s->lines[s->nlines++] = (astro_line){(int16_t)x1, (int16_t)y1, (int16_t)x2, (int16_t)y2};
    }
}

/* Outline of the reference frame plus a red reticle at its centre, or an arrow towards it. */
static void build_scene(const astro_frame_match *m, astro_scene *s)
{
    memset(s, 0, sizeof(*s));
    s->width = ASTRO_IMAGE_WIDTH;
    s->height = ASTRO_IMAGE_HEIGHT;
    if (m->outline_valid) {
        for (int i = 0; i < 4; i++) {
            const int32_t *a = m->outline_q4[i];
            const int32_t *b = m->outline_q4[(i + 1) % 4];
            add_line(s, a[0], a[1], b[0], b[1]);
        }
    }
    snprintf(s->target_label, sizeof(s->target_label), "Reference");
    s->target_separation = m->separation;
    int x = m->centre_q4[0];
    int y = m->centre_q4[1];
    if (m->centre_visible && x >= 0 && x < ASTRO_IMAGE_WIDTH * 16 && y >= 0 &&
        y < ASTRO_IMAGE_HEIGHT * 16) {
        s->target = ASTRO_TARGET_IN_FRAME;
        s->target_x = (int16_t)x;
        s->target_y = (int16_t)y;
        add_line(s, x - CROSS_Q4, y - CROSS_Q4, x + CROSS_Q4, y + CROSS_Q4);
        add_line(s, x - CROSS_Q4, y + CROSS_Q4, x + CROSS_Q4, y - CROSS_Q4);
    } else {
        s->target = ASTRO_TARGET_OUTSIDE;
        s->target_dx = (int16_t)m->arrow_q14[0];
        s->target_dy = (int16_t)m->arrow_q14[1];
    }
}

/* "Move 1.4 deg  Turn 3.2 deg clockwise  Lens: FOV 15.6 vs 9.8". */
static void guidance_text(const astro_frame_match *m, int current_fov, char *out, int size)
{
    int used;
    int turn = m->rotation_ddeg < 0 ? -m->rotation_ddeg : m->rotation_ddeg;

    if (m->mirrored) {
        snprintf(out, size, "Reference is mirrored: cannot match   %s: stop", astro_q_key());
        return;
    }
    used = live.panel >= 0 ? snprintf(out, size, "Panel %d: ", live.panel) : 0;
    if (m->matched) {
        used += snprintf(out + used, size - used, "MATCHED");
    } else {
        used += snprintf(out + used, size - used, "Move %d.%d deg", (int)(m->separation / 10000),
                         (int)(m->separation / 1000 % 10));
        if (m->rotation_valid && turn > ASTRO_MATCH_ROTATION_DDEG) {
            used += snprintf(out + used, size - used, "  Turn %d.%d deg %s", turn / 10, turn % 10,
                             m->rotation_ddeg > 0 ? "clockwise" : "anticlockwise");
        }
    }
    if (m->scale_permille < 1000 - LENS_TOLERANCE || m->scale_permille > 1000 + LENS_TOLERANCE) {
        used +=
            snprintf(out + used, size - used, "  Lens: FOV %d.%d vs %d.%d", current_fov / 100,
                     current_fov / 10 % 10, astro_cfg_ref_fov / 100, astro_cfg_ref_fov / 10 % 10);
    }
    snprintf(out + used, size - used, "   %s: stop", astro_q_key());
}

/* Erases exactly what was drawn before, then draws the new scene. Call under BMP_LOCK. */
static void replace_scene_on_screen(void)
{
    if (view.drawn) {
        astro_draw_scene(&view.shown, &astro_liveview_map, 1);
    }
    astro_draw_scene(&view.next, &astro_liveview_map, 0);
    view.shown = view.next;
    view.drawn = 1;
}

static void on_frame(void *unused, astro_solve_status status, const astro_solve_result *result)
{
    char text[HUD_TEXT];
    astro_wcs reference;
    astro_frame_match match;

    if (status != ASTRO_SOLVE_OK) {
        snprintf(text, sizeof(text), "%s: hold still / raise LV gain   %s: stop",
                 astro_solve_status_text(status), astro_q_key());
        astro_hud(text, COLOR_WHITE);
        return;
    }
    astro_remember_solution(result);
    load_reference(&reference);
    if (!astro_frame_compare(&reference, &result->sky.wcs, ASTRO_IMAGE_WIDTH, ASTRO_IMAGE_HEIGHT,
                             &match)) {
        astro_hud("Reference unusable: set it again", COLOR_WHITE);
        return;
    }
    live.separation = match.separation;
    live.rotation_ddeg = match.rotation_ddeg;
    live.rotation_valid = match.rotation_valid;
    live.streak = match.matched ? live.streak + 1 : 0;
    live.matched = live.streak >= MATCHED_FRAMES;
    build_scene(&match, &view.next);
    BMP_LOCK(replace_scene_on_screen());
    guidance_text(&match, result->sky.fov_cdeg, text, sizeof(text));
    astro_hud(text, match.matched ? COLOR_GREEN1 : COLOR_YELLOW);
}

void astro_frame_match_run(void)
{
    astro_live_stats stats;

    if (!astro_cfg_ref_valid) {
        astro_set_status("Set a reference first");
        NotifyBox(NOTIFY_MS, "Frame match: %s", astro_status());
        return;
    }
    if (!astro_liveview_ready()) {
        astro_set_status("Open 1x photo Live View first");
        NotifyBox(NOTIFY_MS, "Frame match: %s", astro_status());
        return;
    }
    view.drawn = 0;
    live.streak = 0;
    live.matched = 0;
    live.running = 1;
    astro_live_loop(on_frame, NULL, &stats);
    live.running = 0;
    if (view.drawn) {
        BMP_LOCK(astro_draw_scene(&view.shown, &astro_liveview_map, 1));
    }
    astro_set_status("Frame match stopped: %d/%d frames solved", stats.solved, stats.frames);
}
