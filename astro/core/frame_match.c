/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Frame matching works entirely on the sky: the reference image's centre and corners are
 * unprojected with its own solution and projected with the current one. Rotation compares
 * the direction of north at the same sky point in both images.
 */
#include <string.h>
#include "frame_match.h"

#define DDEG_PER_1E4DEG 1000
#define MIN_FOV_CDEG    20    /* 0.2 degree */
#define MAX_FOV_CDEG    12000 /* 120 degrees: the gnomonic edge stays finite */
#define MDEG_PER_RADIAN 57296 /* 180 / pi * 1000 */

static int64_t determinant(const astro_wcs *wcs)
{
    return (int64_t)wcs->m_q24[0] * wcs->m_q24[3] - (int64_t)wcs->m_q24[1] * wcs->m_q24[2];
}

/* Angle of north on screen (x right, y down), 1e-4 degree, clockwise from the +x axis. */
static int north_angle(const astro_wcs *wcs, uint32_t ra, int32_t dec, int32_t *angle)
{
    int32_t north[2];
    int32_t east[2];

    if (!astro_wcs_axes(wcs, ra, dec, north, east)) {
        return 0;
    }
    *angle = astro_atan2(north[1], north[0]);
    return 1;
}

/* Difference wrapped into -180..180 degrees, converted to 0.1 degree. */
static int wrapped_ddeg(int32_t difference)
{
    while (difference > ASTRO_HALF_CIRCLE) {
        difference -= ASTRO_FULL_CIRCLE;
    }
    while (difference <= -ASTRO_HALF_CIRCLE) {
        difference += ASTRO_FULL_CIRCLE;
    }
    int rounding = difference >= 0 ? DDEG_PER_1E4DEG / 2 : -DDEG_PER_1E4DEG / 2;
    return (difference + rounding) / DDEG_PER_1E4DEG;
}

/* Carries one reference pixel into the current image. */
static int transfer(const astro_wcs *reference, const astro_wcs *current, int32_t x_q4,
                    int32_t y_q4, int32_t out_q4[2])
{
    uint32_t ra;
    int32_t dec;

    return astro_wcs_unproject(reference, x_q4, y_q4, &ra, &dec) &&
           astro_wcs_project(current, ra, dec, &out_q4[0], &out_q4[1]);
}

static void outline(const astro_wcs *reference, const astro_wcs *current, int width, int height,
                    astro_frame_match *out)
{
    const int32_t corners[4][2] = {
        {0, 0}, {width * 16, 0}, {width * 16, height * 16}, {0, height * 16}};

    out->outline_valid = 1;
    for (int i = 0; i < 4; i++) {
        out->outline_valid &=
            transfer(reference, current, corners[i][0], corners[i][1], out->outline_q4[i]);
    }
}

int astro_wcs_from_pointing(uint32_t ra, int32_t dec, int32_t north_ddeg, int32_t fov_cdeg,
                            int mirrored, int width, int height, astro_wcs *out)
{
    int32_t half_sin;
    int32_t half_cos;
    int32_t north_sin;
    int32_t north_cos;

    if (fov_cdeg < MIN_FOV_CDEG || fov_cdeg > MAX_FOV_CDEG || width <= 0 || height <= 0) {
        return 0;
    }
    /* The image edge sits at tan(fov / 2) on the tangent plane, in "millidegrees" there. */
    astro_sincos(fov_cdeg * 50, &half_sin, &half_cos);
    int64_t edge_mdeg_q16 = ((int64_t)half_sin << 16) * MDEG_PER_RADIAN / half_cos;
    /* Pixels per tangent-plane millidegree, Q24. */
    int64_t scale_q24 = ((int64_t)(width / 2) << 40) / edge_mdeg_q16;

    /* On screen (y down) north points along (sin, -cos) of its clockwise angle from up; east is
     * 90 degrees anticlockwise from north, or clockwise in a mirrored image. */
    astro_sincos(north_ddeg * DDEG_PER_1E4DEG, &north_sin, &north_cos);
    int64_t east_x = mirrored ? north_cos : -(int64_t)north_cos;
    int64_t east_y = mirrored ? north_sin : -(int64_t)north_sin;
    out->ra0 = ra % ASTRO_FULL_CIRCLE;
    out->dec0 = dec;
    out->m_q24[0] = (int32_t)(scale_q24 * east_x >> 30);
    out->m_q24[1] = (int32_t)(scale_q24 * north_sin >> 30);
    out->m_q24[2] = (int32_t)(scale_q24 * east_y >> 30);
    out->m_q24[3] = (int32_t)(scale_q24 * -(int64_t)north_cos >> 30);
    out->t_q16[0] = (int32_t)((int64_t)width << 15);
    out->t_q16[1] = (int32_t)((int64_t)height << 15);
    return 1;
}

int astro_frame_compare(const astro_wcs *reference, const astro_wcs *current, int width, int height,
                        astro_frame_match *out)
{
    uint32_t ref_ra;
    uint32_t cur_ra;
    int32_t ref_dec;
    int32_t cur_dec;
    int32_t ref_angle;
    int32_t cur_angle;

    memset(out, 0, sizeof(*out));
    if (!determinant(reference) || !determinant(current) ||
        !astro_wcs_unproject(reference, width * 8, height * 8, &ref_ra, &ref_dec) ||
        !astro_wcs_unproject(current, width * 8, height * 8, &cur_ra, &cur_dec)) {
        return 0;
    }
    out->separation = astro_separation(cur_ra, cur_dec, ref_ra, ref_dec);
    out->centre_visible =
        astro_wcs_project(current, ref_ra, ref_dec, &out->centre_q4[0], &out->centre_q4[1]);
    if (out->separation) {
        astro_wcs_direction(current, cur_ra, cur_dec, ref_ra, ref_dec, &out->arrow_q14[0],
                            &out->arrow_q14[1]);
    }
    /* Orientation is compared where both solutions are defined: at the reference centre, or
     * failing that at the current one. Far apart, only the arrow is meaningful. */
    out->rotation_valid = (north_angle(reference, ref_ra, ref_dec, &ref_angle) &&
                           north_angle(current, ref_ra, ref_dec, &cur_angle)) ||
                          (north_angle(reference, cur_ra, cur_dec, &ref_angle) &&
                           north_angle(current, cur_ra, cur_dec, &cur_angle));
    if (out->rotation_valid) {
        out->rotation_ddeg = wrapped_ddeg(cur_angle - ref_angle);
    }
    out->mirrored = (determinant(reference) > 0) != (determinant(current) > 0);

    int ref_fov = astro_wcs_fov_cdeg(reference, width, height);
    int cur_fov = astro_wcs_fov_cdeg(current, width, height);
    out->scale_permille = ref_fov ? (int)((int64_t)cur_fov * 1000 / ref_fov) : 0;

    outline(reference, current, width, height, out);
    int rotation = out->rotation_ddeg < 0 ? -out->rotation_ddeg : out->rotation_ddeg;
    out->matched = out->rotation_valid && !out->mirrored &&
                   out->separation <= ASTRO_MATCH_SEPARATION_1E4DEG &&
                   rotation <= ASTRO_MATCH_ROTATION_DDEG;
    return 1;
}
