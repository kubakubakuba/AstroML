/* SPDX-License-Identifier: GPL-2.0-only */
/* Persistent settings and shared status of the module. */
#ifndef ASTRO_STATE_H
#define ASTRO_STATE_H

#include <stdint.h>

/* Analysis image: Live View luma downscaled to 360x240 (3:2, like the 6D sensor). */
#define ASTRO_IMAGE_WIDTH  360
#define ASTRO_IMAGE_HEIGHT 240
#define ASTRO_IMAGE_BYTES  (ASTRO_IMAGE_WIDTH * ASTRO_IMAGE_HEIGHT)

#define ASTRO_DATA_DIR "ML/ASTRO/"

enum { ASTRO_FOCAL_BLIND = 0, ASTRO_FOCAL_FROM_LENS, ASTRO_FOCAL_MANUAL };
enum {
    ASTRO_SHORTCUT_OFF = 0,
    ASTRO_SHORTCUT_HALFSHUTTER,
    ASTRO_SHORTCUT_INFO,
    ASTRO_SHORTCUT_BUTTON /* the camera's spare button from cameras.toml */
};

/* Saved in ML/SETTINGS/ASTRO.CFG (see state.c for names and defaults). */
extern int astro_cfg_focal_source;
extern int astro_cfg_focal_mm;
extern int astro_cfg_target_catalogue;
extern int astro_cfg_target_number;
extern int astro_cfg_mark_target;
extern int astro_cfg_overlay_stars;
extern int astro_cfg_overlay_dso;
extern int astro_cfg_overlay_constellations;
extern int astro_cfg_overlay_dso_mag;
extern int astro_cfg_focus_step;
extern int astro_cfg_focus_radius;
extern int astro_cfg_focus_shortcut;
extern int astro_cfg_focus_readout;
extern int astro_cfg_test_image;
extern int astro_cfg_location_valid;
extern int astro_cfg_latitude;        /* 1e-4 degree */
extern int astro_cfg_longitude;       /* 1e-4 degree */
extern int astro_cfg_utc_offset;      /* camera clock minus UTC, minutes */
extern int astro_cfg_location_time;   /* fix time, seconds since J2000 UTC */
extern int astro_cfg_location_folder; /* DCIM folder number of the source photo */
extern int astro_cfg_location_file;   /* file number of the source photo */

/* Frame-match reference, one int each: ML saves only single ints. */
extern int astro_cfg_ref_valid;
extern int astro_cfg_ref_ra0;  /* 1e-4 degree */
extern int astro_cfg_ref_dec0; /* 1e-4 degree */
extern int astro_cfg_ref_m0;   /* astro_wcs m_q24[0..3] */
extern int astro_cfg_ref_m1;
extern int astro_cfg_ref_m2;
extern int astro_cfg_ref_m3;
extern int astro_cfg_ref_t0; /* astro_wcs t_q16[0..1] */
extern int astro_cfg_ref_t1;
extern int astro_cfg_ref_fov;    /* centidegrees */
extern int astro_cfg_ref_source; /* astro_frame_source */

/* One-line status of the last action, shown in menus and notifications. */
void astro_set_status(const char *format, ...);
const char *astro_status(void);
int astro_status_recent(void); /* set within the last few seconds */

/* Focal length used as a solving hint in mm, 0 for a blind solve. */
int astro_hint_focal_mm(void);

#endif
