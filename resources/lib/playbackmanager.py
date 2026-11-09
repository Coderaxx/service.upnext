# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)

from math import ceil
from time import time
from xbmc import getCondVisibility, sleep
from api import Api, countdown_time, credits_autoplay_deadline
from demo import DemoOverlay
from player import playback_serial, UpNextPlayer
from playitem import PlayItem
from state import State
from stillwatching import StillWatching
from upnext import UpNext
from utils import addon_path, calculate_progress_steps, clear_property, event, get_setting_bool, get_setting_int, log as ulog, set_property


class PlaybackManager:
    _shared_state = {}
    fullscreen_still_watching = False  # Set from the settings every time the popup is launched

    def __init__(self):
        self.__dict__ = self._shared_state
        self.api = Api()
        self.play_item = PlayItem()
        self.state = State()
        self.player = UpNextPlayer()
        self.demo = DemoOverlay(12005)

    def log(self, msg, level=2):
        ulog(msg, name=self.__class__.__name__, level=level)

    def handle_demo(self):
        if get_setting_bool('enableDemoMode'):
            self.log('Up Next DEMO mode enabled, skipping automatically to the end', 0)
            self.demo.show()
            try:
                total_time = self.player.getTotalTime()
                self.player.seekTime(total_time - 15)
            except RuntimeError as exc:
                self.log('Failed to seekTime(): %s' % exc, 0)
        else:
            self.demo.hide()

    def launch_up_next(self):
        """Show the popup and act on it. Returns True if other playback replaced the episode while the popup was open"""
        enable_playlist = get_setting_bool('enablePlaylist')
        episode, source = self.play_item.get_next()
        self.log('Playlist setting: %s' % enable_playlist)
        if source == 'playlist' and not enable_playlist:
            self.log('Playlist integration disabled', 2)
            return False
        if not episode:
            # No episode get out of here
            self.log('Error: no episode could be found to play next...exiting', 1)
            return False
        self.log('episode details %s' % episode, 2)
        try:
            playing_file = self.player.getPlayingFile()
        except RuntimeError:
            self.log('No active playback for the next-episode popup', 2)
            return False
        play_next, keep_playing = self.launch_popup(episode, source)
        if not play_next:
            try:
                current_file = self.player.getPlayingFile()
            except RuntimeError:
                current_file = None
            if current_file != playing_file:
                self.log('Playback changed while the popup was open; skipping stale cleanup', 2)
                # Another file is playing now, its tracking must stay enabled
                return current_file is not None
        self.state.playing_next = play_next

        # Dequeue and stop playback if not playing next file
        if not play_next and self.state.queued:
            self.state.queued = self.api.dequeue_next_item()
        if not keep_playing:
            self.log('Stopping playback', 2)
            self.player.stop()

        self.api.reset_addon_data()
        return False

    def launch_popup(self, episode, source=None):
        episode_id = episode.get('episodeid')
        no_play_count = episode.get('playcount') is None or episode.get('playcount') == 0
        include_play_count = True if self.state.include_watched else no_play_count
        if not include_play_count or self.state.current_episode_id == episode_id:
            # play_next = False
            # keep_playing = True
            # return play_next, keep_playing
            # Don't play next file, but keep playing current file
            return False, True

        # Add next file to playlist if existing playlist is not being used
        if source != 'playlist':
            queued = self.state.queued = self.api.queue_next_item(episode)
        else:
            queued = False

        # We have a next up episode choose mode
        # "Are you still watching?" is the original pop-up, unless the full-screen version is switched on
        fullscreen = self.fullscreen_still_watching = get_setting_bool('stillWatchingFullscreen')
        still_watching_skin = 'script-upnext-stillwatching%s%s.xml'
        if get_setting_int('simpleMode') == 0:
            next_up_page = UpNext('script-upnext-upnext-simple.xml', addon_path(), 'default', '1080i')
            still_watching_page = StillWatching(still_watching_skin % ('-fullscreen' if fullscreen else '', '-simple'),
                                                addon_path(), 'default', '1080i')
        else:
            next_up_page = UpNext('script-upnext-upnext.xml', addon_path(), 'default', '1080i')
            still_watching_page = StillWatching(still_watching_skin % ('-fullscreen' if fullscreen else '', ''),
                                                addon_path(), 'default', '1080i')

        self.popup_aborted = False
        showing_next_up_page, showing_still_watching_page = self._show_popup(episode, next_up_page, still_watching_page)
        should_play_default, should_play_non_default = self.extract_play_info(next_up_page,
                                                                              showing_next_up_page,
                                                                              showing_still_watching_page,
                                                                              still_watching_page)
        if self.popup_aborted:
            # Another file started while the popup was open: it has been closed, leave playback alone
            self.log('popup closed because playback changed, nothing to play or stop', 2)
            return False, True
        if self.fullscreen_still_watching and showing_still_watching_page and still_watching_page.is_still_watching():
            # Confirmed, so un-pause playback before starting the next episode
            self.resume_player()
        if not self.state.track:
            self.log('exit launch_popup early due to disabled tracking', 2)
            # play_next = False
            # keep_playing = showing_next_up_page
            # return play_next, keep_playing
            # Don't play next file
            # Stop if Still Watching? popup was shown to prevent unwanted playback when using FF or skip
            return False, showing_next_up_page

        play_item_option_1 = (should_play_default and self.state.play_mode == 0)
        play_item_option_2 = (should_play_non_default and self.state.play_mode == 1)
        if not play_item_option_1 and not play_item_option_2:
            if self.fullscreen_still_watching and showing_still_watching_page:
                # Nobody confirmed "Are you still watching?" (timed out, Exit or Back): don't play next and
                # don't resume the paused video, stop it
                self.log('still watching not confirmed, stopping playback', 2)
                return False, False
            if next_up_page.is_dismissed() if showing_next_up_page else still_watching_page.is_dismissed():
                # Back dismisses the prompt without stopping the current video.
                return False, True
            # play_next = False
            # keep_playing = next_up_page.is_cancel() if showing_next_up_page else still_watching_page.is_cancel()
            # keep_playing = keep_playing and not get_setting_bool('stopAfterClose')
            # return play_next, keep_playing
            # Don't play next file, and stop current file if no playback option selected
            return False, (
                (next_up_page.is_cancel() if showing_next_up_page else still_watching_page.is_cancel())
                and not get_setting_bool('stopAfterClose')
            )

        self.log('playing media episode', 2)
        # Signal to trakt previous episode watched
        event(message='NEXTUPWATCHEDSIGNAL', data={'episodeid': self.state.current_episode_id}, encoding='base64')
        if source == 'playlist' or queued:
            # Play playlist media
            if should_play_non_default or next_up_page.is_auto_advance():
                # Early credits expiry must also advance an already queued item.
                self.player.playnext()
        elif self.api.has_addon_data():
            # Play add-on media
            self.api.play_addon_item()
        else:
            # Play local media
            self.api.play_kodi_item(episode)

        # play_next = True
        # keep_playing = True
        # return play_next, keep_playing
        # Play next file, and keep playing current file
        return True, True

    def _show_popup(self, episode, next_up_page, still_watching_page):
        """Show the popup and wait for it, closing it again if anything goes wrong while it is open"""
        try:
            return self.show_popup_and_wait(episode, next_up_page, still_watching_page)
        except Exception:
            # Don't leave a frozen popup on screen, the service loop logs the error and carries on
            next_up_page.close()
            still_watching_page.close()
            clear_property('service.upnext.dialog')
            raise

    def pause_player(self):
        """Pause playback unless it already is paused"""
        if not getCondVisibility('Player.Paused'):
            self.player.pause()  # Toggles

    def resume_player(self):
        """Resume playback if it is paused"""
        if getCondVisibility('Player.Paused'):
            self.player.pause()  # Toggles

    def show_still_watching_and_wait(self, still_watching_page, playing_file):
        """Netflix style: pause, ask "Are you still watching?" and give up when nobody answers.

        Returns False when playback stopped or changed while the page was open (the page is closed then)
        """
        timeout = get_setting_int('stillWatchingTimeout')
        if not timeout or timeout < 1:
            timeout = 30
        still_watching_page.set_initial_remaining(timeout)
        self.pause_player()
        still_watching_page.show()
        set_property('service.upnext.dialog', 'true')

        serial = playback_serial()
        start_time = time()
        observed_paused = False
        while not still_watching_page.is_still_watching() and not still_watching_page.is_cancel():
            if self._playback_position(playing_file) is None or playback_serial() != serial:
                self.log('Playback stopped or changed while the popup was open, closing it', 2)
                self.popup_aborted = True
                still_watching_page.close()
                return False

            remaining = timeout - (time() - start_time)
            if remaining <= 0:
                self.log('no answer to still watching within %s seconds' % timeout, 2)
                break

            if getCondVisibility('Player.Paused'):
                observed_paused = True
            elif observed_paused:
                # Playback was resumed by the user (e.g. with the play button), counts as an answer
                self.log('playback resumed by user, still watching confirmed', 2)
                still_watching_page.confirm()
                break

            still_watching_page.update_progress_control(remaining=int(ceil(remaining)),
                                                        progress=100.0 * remaining / timeout)
            sleep(100)
        return True

    def show_popup_and_wait(self, episode, next_up_page, still_watching_page):
        try:
            play_time = self.player.getTime()
            total_time = self.player.getTotalTime()
            playing_file = self.player.getPlayingFile()
        except RuntimeError:
            self.log('exit early because player is no longer running', 2)
            return False, False
        serial = playback_serial()
        initial_total_time = total_time
        last_play_time = play_time
        progress_step_size = calculate_progress_steps(total_time - play_time)
        autoplay_deadline = (credits_autoplay_deadline(self.api.has_addon_data(), play_time, total_time)
                            if self.state.play_mode == 0 else None)
        if autoplay_deadline is None and self.state.play_mode == 0:
            # No credits offset from another add-on: use the countdown set for this episode length,
            # unless the episode ends before the countdown would
            countdown = countdown_time(total_time)
            if countdown and countdown < total_time - play_time - 1:
                autoplay_deadline = play_time + countdown
        autoplay_duration = autoplay_deadline - play_time if autoplay_deadline is not None else 0
        countdown_left = autoplay_duration
        next_up_page.set_item(episode)
        next_up_page.set_progress_step_size(progress_step_size)
        still_watching_page.set_item(episode)
        still_watching_page.set_progress_step_size(progress_step_size)
        played_in_a_row_number = get_setting_int('playedInARow')
        self.log('played in a row settings %s' % played_in_a_row_number, 2)
        self.log('played in a row %s' % self.state.played_in_a_row, 2)
        showing_next_up_page = False
        showing_still_watching_page = False
        if not played_in_a_row_number or int(self.state.played_in_a_row) < int(played_in_a_row_number):
            self.log('showing next up page as played in a row is %s' % self.state.played_in_a_row, 2)
            next_up_page.set_credits_countdown(int(ceil(autoplay_duration)) if autoplay_deadline is not None else None)
            next_up_page.show()
            set_property('service.upnext.dialog', 'true')
            showing_next_up_page = True
        else:
            self.log('showing still watching page as played in a row %s' % self.state.played_in_a_row, 2)
            if self.fullscreen_still_watching:
                # Full screen: playback is paused and stopped when nobody answers
                showing_still_watching_page = self.show_still_watching_and_wait(still_watching_page, playing_file)
                return showing_next_up_page, showing_still_watching_page
            # Original pop-up: shown over the playing video, counting down to the end of the episode
            still_watching_page.show()
            set_property('service.upnext.dialog', 'true')
            showing_still_watching_page = True
        while (self.player.isPlaying() and (total_time - play_time > 1)
               and not next_up_page.is_cancel() and not next_up_page.is_watch_now() and not next_up_page.is_auto_advance()
               and not still_watching_page.is_still_watching() and not still_watching_page.is_cancel()):
            position = self._playback_position(playing_file, initial_total_time)
            if position is None or playback_serial() != serial:
                self.log('Playback stopped or changed while the popup was open, closing it', 2)
                self.popup_aborted = True
                if showing_next_up_page:
                    next_up_page.close()
                    showing_next_up_page = False
                if showing_still_watching_page:
                    still_watching_page.close()
                    showing_still_watching_page = False
                break
            last_play_time, play_time, total_time = play_time, position[0], position[1]

            remaining = total_time - play_time
            if showing_next_up_page and autoplay_deadline is not None:
                # The countdown follows the playback position, so it stands still while paused by itself.
                # Never show more than the countdown that was started, and don't depend on the pause flag
                # (a stale flag would freeze the countdown and leave the popup open until the episode ends)
                if play_time < last_play_time - 2:
                    # The viewer rewound: carry on counting down from where it was instead of starting over
                    autoplay_deadline = play_time + countdown_left
                remaining = max(0, min(remaining, autoplay_deadline - play_time, autoplay_duration))
                countdown_left = remaining
                self._update_credits_progress(next_up_page, remaining, autoplay_duration)
            elif not self.state.pause:
                if showing_next_up_page:
                    next_up_page.update_progress_control(remaining=remaining, runtime=episode.get('runtime'))
                elif showing_still_watching_page:
                    still_watching_page.update_progress_control(remaining=remaining, runtime=episode.get('runtime'))
            sleep(100)
        return showing_next_up_page, showing_still_watching_page

    def _playback_position(self, playing_file, total_time=None):
        """Return (play_time, total_time) while the popup's own file is playing, otherwise None"""
        try:
            # Times and the credits deadline only apply to the file the popup was opened for
            if self.player.getPlayingFile() != playing_file:
                return None
            position = self.player.getTime(), self.player.getTotalTime()
            # The file name and the clock are separate reads: make sure the file did not change in between
            if self.player.getPlayingFile() != playing_file:
                return None
        except RuntimeError:
            return None
        if total_time is not None and abs(position[1] - total_time) > 3:
            # A different length means the clock belongs to another file, whatever the file name says
            return None
        return position

    @staticmethod
    def _update_credits_progress(page, remaining, duration):
        progress = 100 * remaining / duration if duration > 0 else 0
        page.set_credits_countdown(int(ceil(remaining)))
        page.update_progress_control(remaining=int(ceil(remaining)), runtime=page.item.get('runtime'), progress=progress)
        if remaining <= 0:
            # Keep autoplay distinct from Watch Now so Still Watching counts survive.
            page.set_auto_advance()

    def extract_play_info(self, next_up_page, showing_next_up_page, showing_still_watching_page, still_watching_page):
        if showing_next_up_page:
            next_up_page.close()
            should_play_default = not next_up_page.is_cancel()
            should_play_non_default = next_up_page.is_watch_now()
        elif showing_still_watching_page:
            still_watching_page.close()
            should_play_default = still_watching_page.is_still_watching()
            should_play_non_default = still_watching_page.is_still_watching()
        else:
            # FIXME: This is a workaround until we handle this better (see comments in #142)
            # The popup was already closed because playback stopped or changed
            clear_property('service.upnext.dialog')
            return False, False

        if next_up_page.is_watch_now() or still_watching_page.is_still_watching():
            self.state.played_in_a_row = 1
        else:
            self.state.played_in_a_row += 1
        clear_property('service.upnext.dialog')
        return should_play_default, should_play_non_default
