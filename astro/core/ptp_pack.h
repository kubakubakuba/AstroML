/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * The USB (PTP) answer format, shared with the companion app (companion/.../protocol.py).
 * Fixed little-endian bytes, independent of compiler layout. Change ASTRO_PTP_LAYOUT whenever
 * the record changes.
 */
#ifndef ASTRO_PTP_PACK_H
#define ASTRO_PTP_PACK_H

#include <stdint.h>
#include "wcs.h"

/* Vendor operation. ML core uses 0xA1E8 (ML) and 0x9999 (CHDK). */
#define ASTRO_PTP_OPCODE 0xA570

/* param1 of the operation. */
enum {
    ASTRO_PTP_VERSION = 0,   /* no data; response params: layout, API version */
    ASTRO_PTP_SOLUTION = 1,  /* data: one record, the most recent solve */
    ASTRO_PTP_REFERENCE = 2, /* data: one record, the frame-match reference */
    ASTRO_PTP_SOLVE = 3,     /* no data; starts a Live View solve; param: 1 accepted, 0 busy */
    ASTRO_PTP_STATUS = 4,    /* data: one status record */
    ASTRO_PTP_EXPOSE = 5,    /* param2: bulb exposure in ms; param: 1 accepted, 0 busy/invalid */
    /* param2 RA, param3 Dec (1e-4 degree), param4 north from up (0.1 degree, low 16 bits,
     * signed) | FOV in cdeg << 16, param5 panel number. Sets the frame-match reference and
     * starts Live View guidance; param: 1 accepted, 0 busy */
    ASTRO_PTP_SET_TARGET = 6,
    ASTRO_PTP_CANCEL = 7 /* stops a running match, guide or solve (not a bulb exposure) */
};

#define ASTRO_PTP_LAYOUT       1
#define ASTRO_PTP_API          2
#define ASTRO_PTP_RECORD_BYTES 96
#define ASTRO_PTP_CAMERA_BYTES 16

enum { ASTRO_PTP_KIND_SOLUTION = 1, ASTRO_PTP_KIND_REFERENCE = 2, ASTRO_PTP_KIND_STATUS = 3 };

typedef struct {
    int kind;
    int valid;
    int source;          /* 0 Live View, 1 gallery, 2 file */
    uint32_t ra;         /* frame centre, 1e-4 degree, J2000 */
    int32_t dec;         /* 1e-4 degree */
    int32_t fov_cdeg;    /* horizontal field of view */
    int32_t north_ddeg;  /* direction of north, clockwise from screen up, 0.1 degree */
    int32_t matched;     /* stars matched in the solve */
    uint32_t elapsed_ms; /* solve time */
    int64_t solved_utc;  /* seconds since J2000 (camera clock and offset), 0 if unknown */
    uint32_t sequence;   /* increases with every new solution */
    astro_wcs wcs;       /* full solution for the 360x240 analysis image */
    char camera[ASTRO_PTP_CAMERA_BYTES];
} astro_ptp_record;

/* Writes exactly ASTRO_PTP_RECORD_BYTES. */
void astro_ptp_pack(const astro_ptp_record *record, uint8_t out[ASTRO_PTP_RECORD_BYTES]);

enum {
    ASTRO_EXPOSURE_IDLE = 0,
    ASTRO_EXPOSURE_RUNNING,
    ASTRO_EXPOSURE_DONE,
    ASTRO_EXPOSURE_CANCELLED,
    ASTRO_EXPOSURE_FAILED
};

/* What the camera is doing, for a companion running a session. Same 96-byte size. */
typedef struct {
    int busy;                 /* a foreground action is queued or running */
    int running_action;       /* astro_action */
    int liveview_ready;       /* 1x photo Live View */
    int exposure_state;       /* ASTRO_EXPOSURE_* */
    uint32_t exposure_number; /* increases with every exposure started over USB */
    uint32_t exposure_ms;
    int photo_folder; /* DCIM folder and file number after the last exposure */
    int photo_file;
    int match_running;
    int match_matched; /* within tolerance on consecutive frames */
    int match_rotation_valid;
    uint32_t match_separation; /* 1e-4 degree */
    int32_t match_rotation_ddeg;
    uint32_t solution_sequence;
    int32_t target_panel; /* panel number of the last SET_TARGET, -1 none */
    char camera[ASTRO_PTP_CAMERA_BYTES];
} astro_ptp_status;

void astro_ptp_pack_status(const astro_ptp_status *status, uint8_t out[ASTRO_PTP_RECORD_BYTES]);

/* North's on-screen direction at the image centre, clockwise from up, 0.1 degree (-1800..1800).
 * Returns 0 when the transform is unusable. */
int astro_ptp_north_from_up(const astro_wcs *wcs, int width, int height, int32_t *ddeg);

#endif
