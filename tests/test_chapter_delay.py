# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Waiting a configurable delay after a credits chapter starts before showing the popup"""

import monitor


def make_monitor(monkeypatch, delay, countdown=10):
    mon = monitor.UpNextMonitor.__new__(monitor.UpNextMonitor)
    mon._chapter_seen = None  # pylint: disable=protected-access
    mon._sidecar_file = None  # pylint: disable=protected-access
    mon._sidecar_start = None  # pylint: disable=protected-access
    monkeypatch.setattr(monitor, 'get_setting_int', lambda key, default=None: delay)
    monkeypatch.setattr(monitor, 'countdown_time', lambda total_time=None: countdown)
    monkeypatch.setattr(monitor, 'get_setting_bool', lambda key: False)
    monkeypatch.setattr(mon, 'log', lambda msg, level=1: None, raising=False)
    return mon


def test_waits_for_delay_after_chapter_starts(monkeypatch):
    mon = make_monitor(monkeypatch, delay=10)
    monkeypatch.setattr(monitor, 'credits_chapter', lambda play_time=None, total_time=None: 'Credits')
    assert mon.credits_trigger('ep.mkv', 2000, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2005, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2010, 2400)


def test_no_delay_triggers_at_once(monkeypatch):
    mon = make_monitor(monkeypatch, delay=0)
    monkeypatch.setattr(monitor, 'credits_chapter', lambda play_time=None, total_time=None: 'Credits')
    assert mon.credits_trigger('ep.mkv', 2000, 2400)


def test_delay_restarts_after_leaving_chapter(monkeypatch):
    mon = make_monitor(monkeypatch, delay=10)
    chapter = {'name': 'Credits'}
    monkeypatch.setattr(monitor, 'credits_chapter', lambda play_time=None, total_time=None: chapter['name'])
    monkeypatch.setattr(mon, 'api', type('Api', (), {'notification_time': lambda self, total_time=None: 0})(),
                        raising=False)
    assert mon.credits_trigger('ep.mkv', 2000, 2400) is None
    chapter['name'] = None  # Seeked back out of the credits
    assert mon.credits_trigger('ep.mkv', 1500, 2400) is None
    chapter['name'] = 'Credits'
    assert mon.credits_trigger('ep.mkv', 2001, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2011, 2400)


def test_delay_leaves_time_for_the_countdown(monkeypatch):
    mon = make_monitor(monkeypatch, delay=60, countdown=10)
    monkeypatch.setattr(monitor, 'credits_chapter', lambda play_time=None, total_time=None: 'Credits')
    assert mon.credits_trigger('ep.mkv', 2370, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2389, 2400) is None
    assert mon.credits_trigger('ep.mkv', 2390, 2400)
