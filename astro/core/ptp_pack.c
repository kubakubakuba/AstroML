/* SPDX-License-Identifier: GPL-2.0-only */
#include <string.h>
#include "ptp_pack.h"

/* Byte offsets of the record. The companion app mirrors this table. */
enum {
    AT_MAGIC = 0,
    AT_LAYOUT = 4,
    AT_KIND = 6,
    AT_FLAGS = 8,
    AT_RA = 12,
    AT_DEC = 16,
    AT_FOV = 20,
    AT_NORTH = 24,
    AT_MATCHED = 28,
    AT_ELAPSED = 32,
    AT_SOLVED_UTC = 36,
    AT_SEQUENCE = 44,
    AT_WCS = 48, /* ra0, dec0, m_q24[4], t_q16[2]: 8 x 4 bytes */
    AT_CAMERA = 80
};

#define FLAG_VALID   1u
#define SOURCE_SHIFT 8

static void put16(uint8_t *at, uint32_t value)
{
    at[0] = (uint8_t)value;
    at[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *at, uint32_t value)
{
    put16(at, value & 0xFFFF);
    put16(at + 2, value >> 16);
}

static void put64(uint8_t *at, int64_t value)
{
    uint64_t bits = (uint64_t)value;
    put32(at, (uint32_t)bits);
    put32(at + 4, (uint32_t)(bits >> 32));
}

void astro_ptp_pack(const astro_ptp_record *r, uint8_t out[ASTRO_PTP_RECORD_BYTES])
{
    uint32_t flags = (r->valid ? FLAG_VALID : 0) | ((uint32_t)(r->source & 0xFF) << SOURCE_SHIFT);

    memset(out, 0, ASTRO_PTP_RECORD_BYTES);
    memcpy(out + AT_MAGIC, "ASTR", 4);
    put16(out + AT_LAYOUT, ASTRO_PTP_LAYOUT);
    put16(out + AT_KIND, (uint32_t)r->kind);
    put32(out + AT_FLAGS, flags);
    put32(out + AT_RA, r->ra);
    put32(out + AT_DEC, (uint32_t)r->dec);
    put32(out + AT_FOV, (uint32_t)r->fov_cdeg);
    put32(out + AT_NORTH, (uint32_t)r->north_ddeg);
    put32(out + AT_MATCHED, (uint32_t)r->matched);
    put32(out + AT_ELAPSED, r->elapsed_ms);
    put64(out + AT_SOLVED_UTC, r->solved_utc);
    put32(out + AT_SEQUENCE, r->sequence);
    put32(out + AT_WCS, r->wcs.ra0);
    put32(out + AT_WCS + 4, (uint32_t)r->wcs.dec0);
    for (int i = 0; i < 4; i++) {
        put32(out + AT_WCS + 8 + 4 * i, (uint32_t)r->wcs.m_q24[i]);
    }
    put32(out + AT_WCS + 24, (uint32_t)r->wcs.t_q16[0]);
    put32(out + AT_WCS + 28, (uint32_t)r->wcs.t_q16[1]);
    for (int i = 0; i < ASTRO_PTP_CAMERA_BYTES - 1 && r->camera[i]; i++) {
        out[AT_CAMERA + i] = (uint8_t)r->camera[i];
    }
}

/* Status record offsets after the common header (magic, layout, kind). */
enum {
    ST_FLAGS = 8,
    ST_ACTION = 12,
    ST_EXPOSURE_STATE = 16,
    ST_EXPOSURE_NUMBER = 20,
    ST_EXPOSURE_MS = 24,
    ST_FOLDER = 28,
    ST_FILE = 32,
    ST_SEPARATION = 36,
    ST_ROTATION = 40,
    ST_SEQUENCE = 44,
    ST_PANEL = 48
};

enum {
    STATUS_BUSY = 1u << 0,
    STATUS_LIVEVIEW = 1u << 1,
    STATUS_MATCH_RUNNING = 1u << 2,
    STATUS_MATCHED = 1u << 3,
    STATUS_ROTATION_VALID = 1u << 4
};

void astro_ptp_pack_status(const astro_ptp_status *s, uint8_t out[ASTRO_PTP_RECORD_BYTES])
{
    uint32_t flags = (s->busy ? STATUS_BUSY : 0) | (s->liveview_ready ? STATUS_LIVEVIEW : 0) |
                     (s->match_running ? STATUS_MATCH_RUNNING : 0) |
                     (s->match_matched ? STATUS_MATCHED : 0) |
                     (s->match_rotation_valid ? STATUS_ROTATION_VALID : 0);

    memset(out, 0, ASTRO_PTP_RECORD_BYTES);
    memcpy(out + AT_MAGIC, "ASTR", 4);
    put16(out + AT_LAYOUT, ASTRO_PTP_LAYOUT);
    put16(out + AT_KIND, ASTRO_PTP_KIND_STATUS);
    put32(out + ST_FLAGS, flags);
    put32(out + ST_ACTION, (uint32_t)s->running_action);
    put32(out + ST_EXPOSURE_STATE, (uint32_t)s->exposure_state);
    put32(out + ST_EXPOSURE_NUMBER, s->exposure_number);
    put32(out + ST_EXPOSURE_MS, s->exposure_ms);
    put32(out + ST_FOLDER, (uint32_t)s->photo_folder);
    put32(out + ST_FILE, (uint32_t)s->photo_file);
    put32(out + ST_SEPARATION, s->match_separation);
    put32(out + ST_ROTATION, (uint32_t)s->match_rotation_ddeg);
    put32(out + ST_SEQUENCE, s->solution_sequence);
    put32(out + ST_PANEL, (uint32_t)s->target_panel);
    for (int i = 0; i < ASTRO_PTP_CAMERA_BYTES - 1 && s->camera[i]; i++) {
        out[AT_CAMERA + i] = (uint8_t)s->camera[i];
    }
}

int astro_ptp_north_from_up(const astro_wcs *wcs, int width, int height, int32_t *ddeg)
{
    uint32_t ra;
    int32_t dec;
    int32_t north[2];
    int32_t east[2];

    if (!astro_wcs_unproject(wcs, width * 8, height * 8, &ra, &dec) ||
        !astro_wcs_axes(wcs, ra, dec, north, east)) {
        return 0;
    }
    /* Screen y grows downwards, so "up" is -y; clockwise means towards +x. */
    int32_t angle = astro_atan2(north[0], -(int64_t)north[1]); /* 1e-4 degree */
    *ddeg = (angle + (angle >= 0 ? 500 : -500)) / 1000;
    return 1;
}
