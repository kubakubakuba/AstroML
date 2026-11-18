/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * The handler runs in Canon's PTP task, not on the worker. It never reads the worker's live
 * result: the worker publishes a packed snapshot under a sequence counter (odd while writing),
 * and the handler retries a torn read a few times before answering "busy".
 */
#include <dryos.h>
#include <ptp.h>
#include <string.h>
#include "core/ptp_pack.h"
#include "camera.h"
#include "capture.h"
#include "core/frame_match.h"
#include "expose.h"
#include "frame_match.h"
#include "location.h"
#include "solve_actions.h"
#include "state.h"
#include "usb.h"
#include "worker.h"

#define READ_ATTEMPTS 3
#define PTP_RC_BUSY   0x2019 /* PTP "Device Busy" */

static struct {
    volatile uint32_t sequence; /* odd while the worker writes */
    uint32_t solutions;         /* counts published solutions, sent to the companion */
    uint8_t packed[ASTRO_PTP_RECORD_BYTES];
    volatile int alive;
} usb;

static void copy_camera_name(char *out)
{
    snprintf(out, ASTRO_PTP_CAMERA_BYTES, "%s", astro_camera() ? astro_camera()->model : "");
}

void astro_usb_publish(const astro_solve_result *result)
{
    astro_ptp_record record;

    memset(&record, 0, sizeof(record));
    record.kind = ASTRO_PTP_KIND_SOLUTION;
    record.valid = result->valid;
    record.source = result->source;
    record.ra = result->sky.ra;
    record.dec = result->sky.dec;
    record.fov_cdeg = result->sky.fov_cdeg;
    record.matched = result->sky.matched;
    record.elapsed_ms = result->elapsed_ms;
    record.solved_utc = astro_now_utc();
    record.sequence = ++usb.solutions;
    record.wcs = result->sky.wcs;
    astro_ptp_north_from_up(&result->sky.wcs, ASTRO_IMAGE_WIDTH, ASTRO_IMAGE_HEIGHT,
                            &record.north_ddeg);
    copy_camera_name(record.camera);

    usb.sequence++;
    astro_ptp_pack(&record, usb.packed);
    usb.sequence++;
}

/* Copies the latest snapshot. Returns 0 if the worker kept writing during every attempt. */
static int read_snapshot(uint8_t out[ASTRO_PTP_RECORD_BYTES])
{
    for (int attempt = 0; attempt < READ_ATTEMPTS; attempt++) {
        uint32_t before = usb.sequence;
        if (before & 1) {
            msleep(1);
            continue;
        }
        memcpy(out, usb.packed, ASTRO_PTP_RECORD_BYTES);
        if (usb.sequence == before) {
            return 1;
        }
    }
    return 0;
}

static void pack_reference(uint8_t out[ASTRO_PTP_RECORD_BYTES])
{
    astro_ptp_record record;

    memset(&record, 0, sizeof(record));
    record.kind = ASTRO_PTP_KIND_REFERENCE;
    record.valid = astro_cfg_ref_valid;
    record.source = astro_cfg_ref_source;
    record.fov_cdeg = astro_cfg_ref_fov;
    record.wcs.ra0 = (uint32_t)astro_cfg_ref_ra0;
    record.wcs.dec0 = astro_cfg_ref_dec0;
    record.wcs.m_q24[0] = astro_cfg_ref_m0;
    record.wcs.m_q24[1] = astro_cfg_ref_m1;
    record.wcs.m_q24[2] = astro_cfg_ref_m2;
    record.wcs.m_q24[3] = astro_cfg_ref_m3;
    record.wcs.t_q16[0] = astro_cfg_ref_t0;
    record.wcs.t_q16[1] = astro_cfg_ref_t1;
    if (record.valid) {
        astro_wcs_unproject(&record.wcs, ASTRO_IMAGE_WIDTH * 8, ASTRO_IMAGE_HEIGHT * 8, &record.ra,
                            &record.dec);
        astro_ptp_north_from_up(&record.wcs, ASTRO_IMAGE_WIDTH, ASTRO_IMAGE_HEIGHT,
                                &record.north_ddeg);
    }
    copy_camera_name(record.camera);
    astro_ptp_pack(&record, out);
}

/* Built on request: every field is a single word written whole by its owner. */
static void pack_status(uint8_t out[ASTRO_PTP_RECORD_BYTES])
{
    astro_ptp_status status;

    memset(&status, 0, sizeof(status));
    status.busy = astro_busy();
    status.running_action = astro_running_action();
    status.liveview_ready = astro_liveview_ready();
    status.solution_sequence = usb.solutions;
    astro_expose_status(&status);
    astro_frame_match_status(&status);
    copy_camera_name(status.camera);
    astro_ptp_pack_status(&status, out);
}

/* Normal camera images have a positive determinant; keep the parity of the last solve. */
static int last_solve_mirrored(void)
{
    const astro_solve_result *last = astro_last_solution();
    const int32_t *m = last->sky.wcs.m_q24;

    return last->valid && (int64_t)m[0] * m[3] - (int64_t)m[1] * m[2] < 0;
}

/* SET_TARGET: params as documented in core/ptp_pack.h. Returns 1 if accepted. */
static int set_target(uint32_t ra, uint32_t dec, uint32_t north_and_fov, uint32_t panel)
{
    astro_wcs wcs;
    int32_t north = (int16_t)(north_and_fov & 0xFFFF);
    int32_t fov = (int32_t)(north_and_fov >> 16);
    int matching = astro_running_action() == ASTRO_ACTION_FRAME_MATCH;

    if ((astro_busy() && !matching) ||
        !astro_wcs_from_pointing(ra, (int32_t)dec, north, fov, last_solve_mirrored(),
                                 ASTRO_IMAGE_WIDTH, ASTRO_IMAGE_HEIGHT, &wcs)) {
        return 0;
    }
    astro_frame_set_reference_wcs(&wcs, fov, (int)panel);
    /* A running match picks the new reference up on its next frame. */
    return matching || astro_request(ASTRO_ACTION_FRAME_MATCH);
}

/* Argument layout of ML's PTP_HANDLER (src/ptp.h). */
static int handle_ptp(void *priv, struct ptp_context *context, uint32_t opcode, uint32_t session,
                      uint32_t transaction, uint32_t param1, uint32_t param2, uint32_t param3,
                      uint32_t param4, uint32_t param5)
{
    struct ptp_msg msg = {
        .id = PTP_RC_OK, .session = session, .transaction = transaction, .param_count = 0};
    uint8_t record[ASTRO_PTP_RECORD_BYTES];
    int send = 0;

    if (!usb.alive) {
        msg.id = PTP_RC_GeneralError;
    } else if (param1 == ASTRO_PTP_VERSION) {
        msg.param_count = 2;
        msg.param[0] = ASTRO_PTP_LAYOUT;
        msg.param[1] = ASTRO_PTP_API;
    } else if (param1 == ASTRO_PTP_SOLUTION) {
        send = read_snapshot(record);
        msg.id = send ? PTP_RC_OK : PTP_RC_BUSY;
    } else if (param1 == ASTRO_PTP_REFERENCE) {
        pack_reference(record);
        send = 1;
    } else if (param1 == ASTRO_PTP_SOLVE) {
        msg.param_count = 1;
        msg.param[0] = (uint32_t)astro_request(ASTRO_ACTION_SOLVE_LIVEVIEW);
    } else if (param1 == ASTRO_PTP_STATUS) {
        pack_status(record);
        send = 1;
    } else if (param1 == ASTRO_PTP_EXPOSE) {
        msg.param_count = 1;
        msg.param[0] = (uint32_t)astro_expose_request(param2);
    } else if (param1 == ASTRO_PTP_SET_TARGET) {
        msg.param_count = 1;
        msg.param[0] = (uint32_t)set_target(param2, param3, param4, param5);
    } else if (param1 == ASTRO_PTP_CANCEL) {
        astro_cancel();
    } else {
        msg.id = PTP_RC_ParameterNotSupported;
    }

    /* A data phase, when there is one, always comes before the response. */
    if (send && context->send_data(context->handle, record, ASTRO_PTP_RECORD_BYTES,
                                   ASTRO_PTP_RECORD_BYTES, 0, 0, 0)) {
        msg.id = PTP_RC_GeneralError;
    }
    context->send_resp(context->handle, &msg);
    return 0;
}

void astro_usb_register(void)
{
    astro_ptp_record empty;

    memset(&empty, 0, sizeof(empty));
    empty.kind = ASTRO_PTP_KIND_SOLUTION;
    copy_camera_name(empty.camera);
    astro_ptp_pack(&empty, usb.packed);
    usb.alive = 1;
    ptp_register_handler(ASTRO_PTP_OPCODE, (void *)handle_ptp, 0);
}

void astro_usb_shutdown(void)
{
    usb.alive = 0;
}
