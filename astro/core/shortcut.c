/* SPDX-License-Identifier: GPL-2.0-only */
/* Focus shortcut on a camera's spare button, only in photo Live View. */
#include "shortcut.h"

astro_shortcut_action astro_shortcut_decide(int raw_key, int configured_key,
                                            const astro_shortcut_state *state)
{
    if (configured_key < 0 || raw_key != configured_key || !state->enabled ||
        !state->photo_liveview || state->menu_open || state->busy) {
        return ASTRO_SHORTCUT_PASS;
    }
    return state->electronic_af ? ASTRO_SHORTCUT_AUTOFOCUS : ASTRO_SHORTCUT_TOGGLE_READOUT;
}
