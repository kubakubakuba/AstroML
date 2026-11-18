/* SPDX-License-Identifier: GPL-2.0-only */
/* The camera this module runs on, from the table generated out of cameras.toml. */
#ifndef ASTRO_CAMERA_H
#define ASTRO_CAMERA_H

#define ASTRO_NO_KEY (-1)

typedef struct {
    const char *model;
    const char *firmware;
    int sensor_width_x10; /* 0.1 mm */
    int focus_key;        /* raw Canon button code, or ASTRO_NO_KEY */
    const char *focus_key_name;
    const char *q_key_name; /* the key ML treats as Q on this body */
} astro_camera_profile;

/* Finds the running camera and firmware. NULL means unsupported: do not load. */
const astro_camera_profile *astro_camera_detect(void);

/* The detected camera. Valid once astro_camera_detect() succeeded. */
const astro_camera_profile *astro_camera(void);

int astro_sensor_width_x10(void);
const char *astro_q_key(void);

#endif
