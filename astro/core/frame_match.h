/* SPDX-License-Identifier: GPL-2.0-only */
/* How far the current view is from a reference photo: shift, rotation, scale, outline. */
#ifndef ASTRO_FRAME_MATCH_H
#define ASTRO_FRAME_MATCH_H

#include <stdint.h>
#include "wcs.h"

/* Matched when the centres are this close and the rotation is this small. */
#define ASTRO_MATCH_SEPARATION_1E4DEG 1000 /* 0.1 degree */
#define ASTRO_MATCH_ROTATION_DDEG     5    /* 0.5 degree */

typedef struct {
    uint32_t separation; /* centre to centre, 1e-4 degree */
    /* Reference centre in the current image (Q4). Valid only when centre_visible is set;
     * otherwise follow arrow_q14, the direction towards it. */
    int centre_visible;
    int32_t centre_q4[2];
    int32_t arrow_q14[2];
    /* The reference content appears rotated by this much in the current image, 0.1 degree,
     * positive = clockwise on screen. Rolling the camera clockwise (seen from behind) by the
     * same amount undoes it. */
    int rotation_ddeg;
    int rotation_valid; /* 0 when the reference is too far away to compare orientation */
    int scale_permille; /* current field of view / reference field of view */
    int mirrored;       /* one image is flipped: cannot be matched by moving the camera */
    int outline_valid;
    int32_t outline_q4[4][2]; /* reference corners (TL, TR, BR, BL) in the current image */
    int matched;
} astro_frame_match;

/* A solution for a planned pointing: tangent point (ra, dec) at the image centre, horizontal
 * field of view fov_cdeg, north drawn north_ddeg clockwise from up (0.1 degree). Unmirrored
 * images show east to the left of north, like the camera. Returns 0 for an unusable FOV. */
int astro_wcs_from_pointing(uint32_t ra, int32_t dec, int32_t north_ddeg, int32_t fov_cdeg,
                            int mirrored, int width, int height, astro_wcs *out);

/* Compares two solutions of the same image size. Returns 0 if either transform is unusable. */
int astro_frame_compare(const astro_wcs *reference, const astro_wcs *current, int width, int height,
                        astro_frame_match *out);

#endif
