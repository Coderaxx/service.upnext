# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)

from copy import deepcopy
from traceback import format_exc
from xbmc import Monitor
from api import Api, credits_chapter
from playbackmanager import PlaybackManager
from player import UpNextPlayer
from popup import close_leftover_popups
from sidecar import load_credits_start
from skip import Skipper
from utils import clear_property, decode_json, get_property, get_setting_bool, log as ulog


class UpNextMonitor(Monitor):
    """Service monitor for Kodi"""

    def __init__(self):
        """Constructor for Monitor"""
        super().__init__()
        self.player = UpNextPlayer()
        self.api = Api()
        self.playback_manager = PlaybackManager()
        self.skipper = Skipper(self.player)
        self._sleep_state = None
        self._wake_retries = 0
        self._playback_state = None
        self._waiting_for = None
        self._sidecar_file = None
        self._sidecar_start = None

    def log(self, msg, level=1):
        """Log wrapper"""
        ulog(msg, name=self.__class__.__name__, level=level)

    def run(self):  # pylint: disable=too-many-branches
        """Main service loop"""
        self.log('Service started', 0)

        while not self.abortRequested():
            # check every 1 sec
            if self.waitForAbort(1):
                # Abort was requested while waiting. We should exit
                break

            close_leftover_popups()
            self.restore_after_wake()
            if not self.player.is_tracking():
                self._waiting_for = None
                self._sidecar_file = None
                self.skipper.reset()
                continue

            if bool(get_property('PseudoTVRunning') == 'True'):
                self.player.disable_tracking()
                self.playback_manager.demo.hide()
                continue

            if get_setting_bool('disableNextUp'):
                # Next Up is disabled
                self.player.disable_tracking()
                self.playback_manager.demo.hide()
                continue

            if self.player.isExternalPlayer():
                self.log('Up Next tracking stopped, external player detected', 2)
                self.player.disable_tracking()
                self.playback_manager.demo.hide()
                continue

            last_file = self.player.get_last_file()
            try:
                current_file = self.player.getPlayingFile()
            except RuntimeError:
                self.wait_for_player('player.getPlayingFile()')
                continue

            if (current_file.startswith((
                    'bluray://', 'dvd://', 'udf://', 'iso9660://', 'cdda://'))
                    or current_file.endswith((
                        '.bdmv', '.iso', '.ifo'))):
                self.log('Up Next tracking stopped, Blu-ray/DVD/CD playing', 2)
                self.player.disable_tracking()
                self.playback_manager.demo.hide()
                continue

            self.remember_playback(current_file)
            if last_file and last_file == current_file:
                # Already processed this playback before
                continue

            try:
                total_time = self.player.getTotalTime()
            except RuntimeError:
                self.wait_for_player('player.getTotalTime()')
                continue

            if total_time == 0:
                self.wait_for_player('playback duration')
                continue

            try:
                play_time = self.player.getTime()
            except RuntimeError:
                self.wait_for_player('player.getTime()')
                continue

            self._waiting_for = None
            try:
                self.skipper.update(self.api.data, play_time)
            except Exception:  # pylint: disable=broad-exception-caught
                # A bad skip_segments payload must not stop the service or the popup
                self.log('Skip button failed:\n%s' % format_exc(), 0)
            reason = self.credits_trigger(current_file, play_time, total_time)
            if not reason:
                # Media hasn't reach notification time yet, waiting a bit longer...
                continue

            self.player.set_last_file(current_file)
            self.log('Show notification as %s' % reason, 2)
            # The popup holds this loop until it closes, so the Skip button
            # could no longer follow playback: close it first.
            self.skipper.hide()
            try:
                replaced = self.playback_manager.launch_up_next()
            except Exception:  # pylint: disable=broad-exception-caught
                # One bad episode or add-on payload must not stop the service for the rest of the session
                self.log('Up Next failed for %s:\n%s' % (current_file, format_exc()), 0)
                clear_property('service.upnext.dialog')
                replaced = False
            if replaced:
                # Another file replaced this episode while the popup was open, keep tracking it
                self.log('Playback was replaced while the popup was open, tracking continues', 2)
            else:
                self.log('Up Next style autoplay succeeded', 2)
                self.player.disable_tracking()
            self.remember_playback()

        self.log('Service stopped', 0)

    def credits_trigger(self, current_file, play_time, total_time):
        """Why the popup should be shown now, or None when it is not time yet

        In order of priority: the sidecar file of the video, a credits chapter, the times in the settings.
        When the video has a valid sidecar file, neither the chapters nor the times in the settings are used.
        """
        credits_start = self.credits_start_from_file(current_file, total_time)
        if credits_start is not None:
            if play_time < credits_start:
                return None
            return 'skipcredits.json says the credits start at %d secs, episode ends in %d secs' % (
                credits_start, total_time - play_time)

        chapter = credits_chapter(play_time=play_time, total_time=total_time)
        if chapter:
            return 'chapter "%s" started, episode ends in %d secs' % (chapter, total_time - play_time)

        notification_time = self.api.notification_time(total_time=total_time)
        if total_time - play_time > notification_time:
            return None
        return 'episode (of length %d secs) ends in %d secs' % (total_time, notification_time)

    def credits_start_from_file(self, current_file, total_time):
        """When the credits start according to the sidecar file, looked up once for every playback"""
        if self._sidecar_file != current_file:
            self._sidecar_file = current_file
            self._sidecar_start = None
            if get_setting_bool('useSkipCreditsFile'):
                self._sidecar_start = load_credits_start(current_file, total_time)
        return self._sidecar_start

    def wait_for_player(self, reason):
        if self._waiting_for != reason:
            self.log('Up Next waiting for %s' % reason, 2)
            self.playback_manager.demo.hide()
            self._waiting_for = reason

    def clear_sleep_state(self):
        self._sleep_state = None
        self._wake_retries = 0

    def remember_playback(self, filename=None):
        if filename is None:
            try:
                filename = self.player.getPlayingFile()
            except RuntimeError:
                return
        if (not self._playback_state
                or self._playback_state['filename'] != filename
                or self._playback_state['data'] != self.api.data
                or self._playback_state['encoding'] != self.api.encoding):
            self._playback_state = {'filename': filename, 'data': deepcopy(self.api.data), 'encoding': self.api.encoding}
        self._playback_state.update(last_file=self.player.get_last_file(), tracking=self.player.is_tracking(),
                                    played_in_a_row=self.player.state.played_in_a_row,
                                    current_tv_show_id=self.player.state.current_tv_show_id)

    def remember_for_sleep(self):
        # The queued sleep callback may run after the native player has stopped.
        # System.OnSleep must precede Player.OnStop, which otherwise discards the
        # snapshot so ordinary stops cannot restore stale playback metadata.
        self.clear_sleep_state()
        self._sleep_state = deepcopy(self._playback_state)

    def restore_after_wake(self):
        if not self._sleep_state:
            return
        if not self._wake_retries:
            return
        self._wake_retries -= 1
        try:
            filename = self.player.getPlayingFile()
            ready = bool(filename) and self.player.getTotalTime() > 0
        except RuntimeError:
            ready = False
            filename = None
        if filename and filename != self._sleep_state['filename']:
            self.clear_sleep_state()
            return
        if not ready:
            if not self._wake_retries:
                self.clear_sleep_state()
            return
        saved = self._sleep_state
        if saved['data'] and not self.api.has_addon_data():
            self.api.addon_data_received(saved['data'], encoding=saved['encoding'])
        self.player.state.played_in_a_row = saved['played_in_a_row']
        self.player.state.current_tv_show_id = saved['current_tv_show_id']
        self.player.set_last_file(saved['last_file'])
        if saved['tracking'] or saved['data']:
            self.player.enable_tracking()
        self.clear_sleep_state()
        self.log('Restored playback tracking after wake', 2)

    def onNotification(self, sender, method, data):  # pylint: disable=invalid-name
        """Notification event handler for accepting data from add-ons"""
        if method == 'System.OnSleep':
            self.remember_for_sleep()
            return
        if method == 'System.OnWake':
            self._wake_retries = 30 if self._sleep_state else 0
            return
        if method == 'Player.OnStop':
            self._waiting_for = None
            self.skipper.reset()
            if not self._sleep_state:
                self._playback_state = None
            return
        if not method.endswith('upnext_data'):  # Method looks like Other.upnext_data
            return

        decoded_data, encoding = decode_json(data)
        if decoded_data is None:
            self.log('Received data from sender %s is not JSON: %s' % (sender, data), 2)
            return

        self.clear_sleep_state()
        self._playback_state = None
        self._waiting_for = None
        self.skipper.reset()
        self.playback_manager.handle_demo()
        decoded_data.update(id='%s_play_action' % sender.replace('.SIGNAL', ''))
        self.api.addon_data_received(decoded_data, encoding=encoding)
        self.player.enable_tracking()
        self.player.reset_queue()
        self.remember_playback()
