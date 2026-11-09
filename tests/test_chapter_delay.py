# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Where the credits come from, and the delay after a credits chapter starts"""
# pylint: disable=protected-access

import monitor

CHAPTERS = [(0.0, 'Intro'), (60.0, 'Part 1'), (1200.0, 'Part 2'), (2300.0, 'End Credits')]


class FakeApi:  # pylint: disable=too-few-public-methods
    def notification_time(self, total_time=None):  # pylint: disable=unused-argument
        return 30


def make_monitor(monkeypatch, *, delay=10, countdown=10, chapters=None, sidecar=None, playing=None):
    """A monitor where the file has these chapters (None: not readable) and sidecar credits start"""
    mon = monitor.UpNextMonitor.__new__(monitor.UpNextMonitor)
    mon._chapter_seen = None
    mon._credits_file = None
    mon._credits = None
    mon.api = FakeApi()
    playing = playing if playing is not None else {'name': None}
    lookups = []
    monkeypatch.setattr(monitor, 'get_setting_int', lambda key, default=None: delay)
    monkeypatch.setattr(monitor, 'get_setting', lambda key, default=None: 'credits, outro')
    monkeypatch.setattr(monitor, 'get_setting_bool', lambda key: True)
    monkeypatch.setattr(monitor, 'countdown_time', lambda total_time=None: countdown)
    monkeypatch.setattr(monitor, 'load_chapters', lambda path: lookups.append(path) or chapters)
    monkeypatch.setattr(monitor, 'load_credits_start', lambda path, total_time=None: sidecar)
    monkeypatch.setattr(monitor, 'credits_chapter', lambda play_time=None, total_time=None: playing['name'])
    monkeypatch.setattr(mon, 'log', lambda msg, level=1: None, raising=False)
    return mon, lookups


def test_credits_chapter_comes_first(monkeypatch):
    mon, _ = make_monitor(monkeypatch, delay=0, chapters=CHAPTERS, sidecar=2000.0)
    assert mon.credits_trigger('ep.mkv', 2100, 2400) is None  # The sidecar file is ignored
    assert mon.credits_trigger('ep.mkv', 2299, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2300, 2400)


def test_credits_chapter_ignores_the_times_in_the_settings(monkeypatch):
    chapters = [(0.0, 'Intro'), (60.0, 'Part 1'), (1200.0, 'Part 2'), (2390.0, 'Credits')]
    mon, _ = make_monitor(monkeypatch, delay=0, countdown=0, chapters=chapters)
    assert mon.credits_trigger('ep.mkv', 2380, 2400) is None  # Settings would show it 30 secs before the end
    assert mon.credits_trigger('ep.mkv', 2390, 2400)


def test_chapters_are_looked_up_once_per_file(monkeypatch):
    mon, lookups = make_monitor(monkeypatch, chapters=CHAPTERS)
    for play_time in (10, 11, 12):
        mon.credits_trigger('ep.mkv', play_time, 2400)
    mon.credits_trigger('ep2.mkv', 10, 2400)
    assert lookups == ['ep.mkv', 'ep2.mkv']


def test_delay_after_known_chapter_start(monkeypatch):
    mon, _ = make_monitor(monkeypatch, delay=10, chapters=CHAPTERS)
    assert mon.credits_trigger('ep.mkv', 2305, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2310, 2400)


def test_sidecar_when_no_credits_chapter(monkeypatch):
    chapters = [(0.0, 'Intro'), (60.0, 'Part 1'), (1200.0, 'Part 2')]
    mon, _ = make_monitor(monkeypatch, chapters=chapters, sidecar=2000.0)
    assert mon.credits_trigger('ep.mkv', 1999, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2000, 2400)
    assert mon._credits[0] == 'sidecar'


def test_opening_credits_are_not_the_credits(monkeypatch):
    chapters = [(0.0, 'Opening Credits'), (90.0, 'Part 1'), (1200.0, 'Part 2'), (2300.0, 'Part 3')]
    mon, _ = make_monitor(monkeypatch, chapters=chapters, sidecar=2000.0)
    assert mon.credits_trigger('ep.mkv', 2000, 2400)
    assert mon._credits[0] == 'sidecar'


def test_settings_when_neither_exists(monkeypatch):
    mon, _ = make_monitor(monkeypatch, chapters=[], sidecar=None, playing={'name': 'Credits'})
    # The chapter list was read and has no credits: the playing chapter name is not used either
    assert mon.credits_trigger('ep.mkv', 2300, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2370, 2400)


def test_unreadable_chapters_wait_after_playing_chapter(monkeypatch):
    mon, _ = make_monitor(monkeypatch, delay=10, chapters=None, playing={'name': 'Credits'})
    assert mon.credits_trigger('ep.mp4', 2000, 2400) is None
    assert mon.credits_trigger('ep.mp4', 2005, 2400) is None
    assert mon.credits_trigger('ep.mp4', 2010, 2400)


def test_unreadable_chapters_delay_restarts_after_leaving_chapter(monkeypatch):
    playing = {'name': 'Credits'}
    mon, _ = make_monitor(monkeypatch, delay=10, chapters=None, playing=playing)
    assert mon.credits_trigger('ep.mp4', 2000, 2400) is None
    playing['name'] = None  # Seeked back out of the credits
    assert mon.credits_trigger('ep.mp4', 1500, 2400) is None
    playing['name'] = 'Credits'
    assert mon.credits_trigger('ep.mp4', 2001, 2400) is None
    assert mon.credits_trigger('ep.mp4', 2011, 2400)


def test_delay_leaves_time_for_the_countdown(monkeypatch):
    chapters = [(0.0, 'Intro'), (60.0, 'Part 1'), (1200.0, 'Part 2'), (2370.0, 'Credits')]
    mon, _ = make_monitor(monkeypatch, delay=60, countdown=10, chapters=chapters)
    assert mon.credits_trigger('ep.mkv', 2389, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2390, 2400)
