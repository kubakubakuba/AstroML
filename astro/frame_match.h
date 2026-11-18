/* SPDX-License-Identifier: GPL-2.0-only */
/* Frame match: return to the position and rotation of an earlier, solved photo. */
#ifndef ASTRO_FRAME_MATCH_GLUE_H
#define ASTRO_FRAME_MATCH_GLUE_H

#include "core/ptp_pack.h"
#include "solve.h"

typedef enum {
    ASTRO_REFERENCE_LIVEVIEW = 0,
    ASTRO_REFERENCE_GALLERY,
    ASTRO_REFERENCE_FILE,
    ASTRO_REFERENCE_COMPANION /* a planned panel sent over USB */
} astro_reference_source;

/* Stores a solution as the reference (saved with the ML settings). Returns 0 if unsolved. */
int astro_frame_set_reference(const astro_solve_result *result, astro_reference_source source);

/* Stores a planned pointing as the reference; `panel` is shown in the guidance. */
void astro_frame_set_reference_wcs(const astro_wcs *wcs, int fov_cdeg, int panel);

/* Live match fields of the status record (any task). */
void astro_frame_match_status(astro_ptp_status *status);

/* Menu actions: the most recent solve becomes the reference; or forget it. */
void astro_frame_use_last_solve(void);
void astro_frame_clear_reference(void);

/* "05h41m -1.2 (gallery)", or "none". */
void astro_frame_reference_text(char *out, int size);

/* Worker action: re-solves Live View and guides back to the reference until Q. */
void astro_frame_match_run(void);

#endif
