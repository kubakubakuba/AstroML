/* SPDX-License-Identifier: GPL-2.0-only */
/* USB (PTP) access for a companion computer: last solution, reference, start a solve. */
#ifndef ASTRO_USB_H
#define ASTRO_USB_H

#include "solve.h"

/* Registers the vendor operation with Canon's PTP stack. Call once from module init. */
void astro_usb_register(void);

/* After this the handler answers with an error: the module is going away. */
void astro_usb_shutdown(void);

/* Worker task: makes `result` the solution a companion reads next. */
void astro_usb_publish(const astro_solve_result *result);

#endif
