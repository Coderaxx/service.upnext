# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Reads <video file>.skipcredits.json, a sidecar file that says when the credits of an episode start

Example: "The Rookie - S01E01.mkv" is accompanied by "The Rookie - S01E01.skipcredits.json":

    {"credits": {"start": 2399.5, "end": 2700.0}}

Only credits.start is used. Anything else in the file (intro, skiptro, ...) is ignored.
"""

import json
import math
import os
import re

import xbmcvfs
from utils import log as ulog

SUFFIX = '.skipcredits.json'
MAX_FILE_SIZE = 1024 * 1024  # bytes, a sidecar file is tiny, anything bigger is not one
# The credits can't start in the first half of an episode, a value like that is a detection mistake
MIN_START_FRACTION = 0.5
# Not real files next to a video, or no way to look next to them
UNSUPPORTED = ('plugin://', 'stack://', 'pvr://', 'videodb://', 'musicdb://', 'upnp://', 'script://',
               'rtmp://', 'rtsp://', 'rtp://', 'udp://', 'bluray://', 'dvd://', 'udf://', 'iso9660://', 'cdda://')


def log(msg, level=1):
    ulog(msg, name='SkipCredits', level=level)


def sidecar_path(media_path):
    """Path of the sidecar file that belongs to a video file, or None when it can't have one"""
    if not media_path or media_path.startswith(UNSUPPORTED):
        return None
    if media_path.startswith(('http://', 'https://')) and ('?' in media_path or '#' in media_path):
        return None  # A streaming URL, not a file next to which something can be stored

    base, extension = os.path.splitext(media_path)
    # Only drop something that really is a file extension ("Episode 1. Pilot" has none)
    if not re.fullmatch(r'\.[A-Za-z0-9]{1,5}', extension):
        base = media_path
    return base + SUFFIX


def _number(value):
    """A finite number from a JSON value, otherwise None"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def parse_credits_start(text, total_time=None):
    """Return (credits start in seconds, None) for valid data, otherwise (None, why it was rejected)"""
    try:
        data = json.loads(text.lstrip('\ufeff'))
    except ValueError as error:
        return None, 'not valid JSON (%s)' % error
    if not isinstance(data, dict):
        return None, 'not a JSON object'

    credits_data = data.get('credits')
    if credits_data is None:
        return None, 'has no "credits" section'
    if not isinstance(credits_data, dict):
        return None, '"credits" is not an object'

    start = _number(credits_data.get('start'))
    if start is None or start < 0:
        return None, '"credits.start" is missing or not a positive number'

    if credits_data.get('end') is not None:
        end = _number(credits_data.get('end'))
        if end is None or end < start:
            return None, '"credits.end" is not a number after "credits.start"'

    if total_time:
        if start >= total_time:
            return None, 'credits start at %.1f, after the end of the video (%.1f)' % (start, total_time)
        if start < total_time * MIN_START_FRACTION:
            return None, 'credits start at %.1f, in the first half of the video (%.1f)' % (start, total_time)
    return start, None


def load_credits_start(media_path, total_time=None):
    """When the credits start according to the sidecar file of this video, or None to use another method"""
    path = sidecar_path(media_path)
    if path is None:
        return None

    try:
        if not xbmcvfs.exists(path):
            log('No sidecar file at %s' % path, 2)
            return None
        sidecar_file = xbmcvfs.File(path)
        try:
            if sidecar_file.size() > MAX_FILE_SIZE:
                log('Ignoring %s, it is too big to be a sidecar file' % path)
                return None
            content = sidecar_file.read()
        finally:
            sidecar_file.close()
        if isinstance(content, (bytes, bytearray)):
            content = bytes(content).decode('utf-8', 'replace')
    except Exception as error:  # pylint: disable=broad-exception-caught
        # A share that can't be read must never stop the add-on
        log('Could not read %s: %s' % (path, error))
        return None

    start, problem = parse_credits_start(content, total_time)
    if problem:
        log('Ignoring %s, %s' % (path, problem))
        return None
    log('Found %s, the credits start at %.1f secs' % (path, start))
    return start
