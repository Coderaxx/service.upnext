# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Behaviour shared by the Up Next and Still Watching popups"""

from datetime import datetime, timedelta
from math import ceil
from time import time
from xbmc import executebuiltin, sleep
from xbmcgui import getCurrentWindowDialogId, WindowXMLDialog
from utils import get_global_setting, is_armv7, localize_date, localize_time, log as ulog

ACTION_PLAYER_STOP = 13
ACTION_NAV_BACK = 92
RING_STEPS = 30  # media/nextupicons has a countdown ring image for 00 to 30
# Kodi's "Show information for unwatched items" (videolibrary.showunwatchedplots) options
SHOW_UNWATCHED_MOVIE_PLOT = 0
SHOW_UNWATCHED_EPISODE_PLOT = 1
SHOW_UNWATCHED_EPISODE_THUMB = 2
# Where each popup image comes from, best first: the show's own art, then art an add-on sent
# without the tvshow. prefix, then the season's
ART_SOURCES = {
    'fanart': ('tvshow.fanart', 'fanart', 'season.fanart'),
    'landscape': ('tvshow.landscape', 'landscape', 'season.landscape'),
    'clearart': ('tvshow.clearart', 'clearart'),
    'clearlogo': ('tvshow.clearlogo', 'clearlogo'),
    'poster': ('tvshow.poster', 'poster', 'season.poster'),
}

NO_DIALOG = 9999  # What Kodi reports as the active dialog when none is open
CLOSE_ANIMATION = 0.5  # seconds, a little more than the 400 ms close animation of the skins
CLOSE_TIMEOUT = 1.0  # seconds to wait for a dialog to go away before forcing it

# Popups that were closed recently. The service loop checks them a moment later, in case closing raced
# with something and left the window on screen over the next episode
_RECENTLY_CLOSED = []
CHECK_AGAIN_AFTER = 2  # seconds


def log(msg):
    ulog(msg, name='Popup')


def close_leftover_popups():
    """Make sure popups that were closed a while ago are really gone, as a safety net"""
    now = time()
    for popup in list(_RECENTLY_CLOSED):
        if now - popup.closed_at >= CHECK_AGAIN_AFTER:
            _RECENTLY_CLOSED.remove(popup)
            if popup.is_still_open():
                log('%s was still on screen, forcing it closed' % popup.__class__.__name__)
                popup.force_close()


class Popup(WindowXMLDialog):
    item = None
    cancel = False
    dismissed = False
    progress_step_size = 0
    current_progress_percent = 100
    progress_control = None
    shown_at = None
    closed_at = None
    opened = False
    dialog_id = None
    previous_dialog_id = None
    countdown_duration = None
    playback_dialog_id = None

    def __init__(self, *args, **kwargs):
        self.action_exitkeys_id = [10, 13]
        self.progress_control = None
        if is_armv7():
            WindowXMLDialog.__init__(self)
        else:
            WindowXMLDialog.__init__(self, *args, **kwargs)

    def onInit(self):  # pylint: disable=invalid-name
        self.opened = True
        # Remember which dialog is ours so it can be checked later that it really closed
        current_dialog_id = self.playback_dialog_id = getCurrentWindowDialogId()
        if current_dialog_id not in (NO_DIALOG, self.previous_dialog_id):
            self.dialog_id = current_dialog_id
        self.set_info()
        self.prepare_progress_control()

    def show(self):
        self.shown_at = time()
        self.closed_at = None
        self.previous_dialog_id = getCurrentWindowDialogId()
        if self in _RECENTLY_CLOSED:
            _RECENTLY_CLOSED.remove(self)
        super().show()

    def close(self):
        if self.shown_at is not None and not self.opened:
            # Closing a window that has not finished opening can leave it on screen, give it a moment
            deadline = time() + 1.5
            while not self.opened and time() < deadline:
                sleep(50)
        super().close()
        if self.shown_at is None:
            return
        if self.closed_at is None:
            self.closed_at = time()
            if self not in _RECENTLY_CLOSED:
                _RECENTLY_CLOSED.append(self)
        # Don't return until the popup is really gone, so the next episode never starts under a half closed
        # popup (the skin animates the closing for a moment, and that has to finish first)
        self.wait_until_closed()

    def is_still_open(self):
        return self.dialog_id is not None and getCurrentWindowDialogId() == self.dialog_id

    def wait_until_closed(self):
        if self.dialog_id is None:
            # Can't tell which dialog is ours: give the closing animation time to finish
            wait = CLOSE_ANIMATION - (time() - self.closed_at)
            if wait > 0:
                sleep(int(wait * 1000))
            return
        deadline = time() + CLOSE_TIMEOUT
        while self.is_still_open() and time() < deadline:
            sleep(50)
        if self.is_still_open():
            log('%s did not close, forcing it closed' % self.__class__.__name__)
            self.force_close()

    def force_close(self):
        """Close the popup without animation, and hide it in case it still lingers"""
        try:
            self.setProperty('closing', 'true')  # The skins hide a popup with this property set
        except RuntimeError:
            pass
        executebuiltin('Dialog.Close(%s,true)' % self.dialog_id)
        deadline = time() + CLOSE_TIMEOUT / 2
        while self.is_still_open() and time() < deadline:
            sleep(50)

    def set_info(self):
        if self.item is None:
            return
        episode_info = '{}x{}.'.format(self.item.get('season', ''), self.item.get('episode', ''))
        if self.item.get('rating') is None:
            rating = ''
        else:
            rating = str(round(float(self.item.get('rating')), 1))

        show_plot, show_thumb = spoilers_allowed(self.item)
        art = popup_art(self.item.get('art') or {}, show_thumb)
        for art_type in ('fanart', 'landscape', 'clearart', 'clearlogo', 'poster', 'thumb'):
            self.setProperty(art_type, art[art_type])
        self.setProperty('plot', self.item.get('plot', '') if show_plot else '')
        self.setProperty('tvshowtitle', self.item.get('showtitle', ''))
        self.setProperty('title', self.item.get('title', ''))
        self.setProperty('season', str(self.item.get('season', '')))
        self.setProperty('episode', str(self.item.get('episode', '')))
        self.setProperty('seasonepisode', episode_info)
        self.setProperty('year', localize_date(self.item.get('firstaired', '')))
        self.setProperty('rating', rating)
        self.setProperty('playcount', str(self.item.get('playcount', 0)))
        self.setProperty('runtime', str(self.item.get('runtime', '')))

    def prepare_progress_control(self):
        try:
            self.progress_control = self.getControl(3014)
        except RuntimeError:  # Occurs when skin does not include progress control
            pass
        else:
            self.progress_control.setPercent(self.current_progress_percent)  # pylint: disable=no-member,useless-suppression

    def set_item(self, item):
        self.item = item

    def set_progress_step_size(self, progress_step_size):
        self.progress_step_size = progress_step_size

    def update_progress_control(self, remaining=None, runtime=None, progress=None):
        self.current_progress_percent = (self.current_progress_percent - self.progress_step_size
                                         if progress is None else max(0, min(100, progress)))
        try:
            self.progress_control = self.getControl(3014)
        except RuntimeError:  # Occurs when skin does not include progress control
            pass
        else:
            self.progress_control.setPercent(self.current_progress_percent)  # pylint: disable=no-member,useless-suppression

        if remaining is not None:
            self.set_remaining(remaining)
        if runtime:
            self.setProperty('endtime', localize_time(datetime.now() + timedelta(seconds=runtime)))

    def set_remaining(self, remaining):
        """Show the time left before the popup acts, in seconds and as m:ss for popups opened earlier"""
        remaining = max(0, int(remaining))
        if self.countdown_duration is None:
            self.countdown_duration = remaining
        self.setProperty('remaining', '%02d' % remaining)
        self.setProperty('remaining_time', format_remaining(remaining))
        self.setProperty('remaining_icon', '%02d' % ring_step(remaining, self.countdown_duration))

    def set_cancel(self, cancel):
        self.cancel = cancel

    def is_cancel(self):
        return self.cancel

    def is_dismissed(self):
        return self.dismissed

    def confirm(self):
        """Handle the popup's main button (3012): Watch now, or Still watching"""
        raise NotImplementedError

    def onFocus(self, controlId):  # pylint: disable=invalid-name
        pass

    def doAction(self):  # pylint: disable=invalid-name
        pass

    def closeDialog(self):  # pylint: disable=invalid-name
        self.close()

    def onClick(self, controlId):  # pylint: disable=invalid-name
        if controlId == 3012:  # Watch now / Still watching
            self.confirm()
            self.close()
        elif controlId == 3013:  # Close / Stop
            self.set_cancel(True)
            # The playback manager owns Stop after the dialog has closed.
            self.close()

    def onAction(self, action):  # pylint: disable=invalid-name
        if action == ACTION_PLAYER_STOP:
            self.close()
        elif action == ACTION_NAV_BACK:
            self.dismissed = True
            self.set_cancel(True)
            self.close()


def spoilers_allowed(item):
    """Return whether the plot and the thumbnail of the next item may be shown, following Kodi's
       "Show information for unwatched items" setting for items not watched yet"""
    # Library lookups and add-ons give episodes; playlists can also hold movies and plain files
    media_type = item.get('type') or 'episode'
    if item.get('playcount') or media_type not in ('episode', 'movie'):
        return True, True
    shown = get_global_setting('videolibrary.showunwatchedplots')
    if not isinstance(shown, list):  # Not available: Kodi shows everything by default
        return True, True
    if media_type == 'movie':
        # Kodi only hides the thumbnails of episodes
        return SHOW_UNWATCHED_MOVIE_PLOT in shown, True
    return SHOW_UNWATCHED_EPISODE_PLOT in shown, SHOW_UNWATCHED_EPISODE_THUMB in shown


def popup_art(art, show_thumb=True):
    """Return the popup's images, each from the first art type the item has"""
    images = {
        art_type: next((art[source] for source in sources if art.get(source)), '')
        for art_type, sources in ART_SOURCES.items()
    }
    if show_thumb:
        images['thumb'] = art.get('thumb', '')
    else:
        # Show art in place of the episode's own picture, like Kodi does for hidden thumbnails
        images['thumb'] = images['landscape'] or images['fanart']
    return images


def format_remaining(seconds):
    """Return seconds as m:ss, or h:mm:ss from an hour"""
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, seconds)
    return '%d:%02d' % (minutes, seconds)


def ring_step(remaining, duration):
    """Return the countdown ring image for the time left: one per second in the last 30 seconds,
       or spread over the whole countdown when the popup opened earlier"""
    if not duration or duration <= RING_STEPS:
        return min(RING_STEPS, int(remaining))
    return min(RING_STEPS, int(ceil(RING_STEPS * remaining / duration)))
