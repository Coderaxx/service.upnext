# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)

from math import ceil
from popup import ACTION_NAV_BACK, Popup
from utils import get_setting_bool

ACTION_PAUSE = 12
ACTION_PLAYER_PLAY = 79
ACTION_PLAYER_PLAYPAUSE = 229


class StillWatching(Popup):
    """The "Are you still watching?" pop-up, optionally as a full screen question while playback is paused"""
    stillwatching = False
    initial_remaining = None

    def onInit(self):  # pylint: disable=invalid-name
        super().onInit()
        if self.initial_remaining is not None:
            # Make sure the countdown text is correct from the very first frame
            self.setProperty('remaining', '%02d' % ceil(self.initial_remaining))

    def set_initial_remaining(self, remaining):
        self.initial_remaining = remaining

    def set_still_watching(self, stillwatching):
        self.stillwatching = stillwatching

    def is_still_watching(self):
        return self.stillwatching

    def confirm(self):
        self.set_still_watching(True)

    def onAction(self, action):  # pylint: disable=invalid-name
        if not get_setting_bool('stillWatchingFullscreen'):
            # The original pop-up keeps the original behaviour
            super().onAction(action)
        elif action in (ACTION_PLAYER_PLAY, ACTION_PLAYER_PLAYPAUSE, ACTION_PAUSE):
            # Pressing play on the remote means "yes, I'm still watching"
            self.confirm()
            self.close()
        elif action == ACTION_NAV_BACK:
            # Back means "no": unlike the Up Next popup it must not leave the paused video behind
            self.set_cancel(True)
            self.close()
        else:
            super().onAction(action)
