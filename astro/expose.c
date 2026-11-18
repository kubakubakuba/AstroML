/* SPDX-License-Identifier: GPL-2.0-only */
/* ML's bulb_take_pic() blocks for the whole exposure, so it runs on the worker; the USB task
 * only queues the request and later reads the outcome. */
#include <dryos.h>
#include <shoot.h>
#include "expose.h"
#include "state.h"
#include "worker.h"

#define MIN_EXPOSURE_MS 1000
#define MAX_EXPOSURE_MS (30 * 60 * 1000)

static struct {
    volatile uint32_t requested_ms;
    volatile uint32_t ms;
    volatile uint32_t number;
    volatile int state;
    volatile int folder;
    volatile int file;
} exposure;

int astro_expose_request(uint32_t ms)
{
    if (ms < MIN_EXPOSURE_MS || ms > MAX_EXPOSURE_MS) {
        return 0;
    }
    exposure.requested_ms = ms;
    return astro_request(ASTRO_ACTION_EXPOSE);
}

void astro_expose_run(void)
{
    exposure.ms = exposure.requested_ms;
    exposure.number++;
    exposure.state = ASTRO_EXPOSURE_RUNNING;
    astro_set_status("Exposing %d s (companion)", (int)(exposure.ms / 1000));

    int cancelled = bulb_take_pic((int)exposure.ms);

    struct card_info *card = get_shooting_card();
    exposure.folder = card->folder_number;
    exposure.file = card->file_number;
    exposure.state = cancelled ? ASTRO_EXPOSURE_CANCELLED : ASTRO_EXPOSURE_DONE;
    astro_set_status("Exposure %s: #%03d-%04d", cancelled ? "cancelled" : "done", exposure.folder,
                     exposure.file);
}

void astro_expose_status(astro_ptp_status *status)
{
    status->exposure_state = exposure.state;
    status->exposure_number = exposure.number;
    status->exposure_ms = exposure.ms;
    status->photo_folder = exposure.folder;
    status->photo_file = exposure.file;
}
