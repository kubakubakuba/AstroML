/* SPDX-License-Identifier: GPL-2.0-only */
/* Bulb exposures started by a companion over USB. */
#ifndef ASTRO_EXPOSE_H
#define ASTRO_EXPOSE_H

#include <stdint.h>
#include "core/ptp_pack.h"

/* Any task: queues a bulb exposure of `ms`. Returns 0 if out of range or the worker is busy. */
int astro_expose_request(uint32_t ms);

/* Worker: takes the queued exposure (blocks until it ends). */
void astro_expose_run(void);

/* Exposure fields of the status record. */
void astro_expose_status(astro_ptp_status *status);

#endif
