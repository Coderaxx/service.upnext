# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Reads the chapter list of a Matroska (.mkv/.mk3d/.webm) video when playback starts

Kodi only tells the name of the chapter that is playing right now, not the full list with start times.
To know before the credits whether a video has a credits chapter, and where it starts, the chapters are
read from the file itself. Only the few small elements needed are read, the video data is skipped.
"""

import os
import struct

from sidecar import UNSUPPORTED
from utils import log as ulog

EXTENSIONS = ('.mkv', '.mk3d', '.mka', '.webm')

# Matroska element IDs (with their length marker bits)
EBML = 0x1A45DFA3
SEGMENT = 0x18538067
SEEK_HEAD = 0x114D9B74
SEEK = 0x4DBB
SEEK_ID = 0x53AB
SEEK_POSITION = 0x53AC
CHAPTERS = 0x1043A770
EDITION_ENTRY = 0x45B9
EDITION_FLAG_HIDDEN = 0x45BD
EDITION_FLAG_DEFAULT = 0x45DB
EDITION_FLAG_ORDERED = 0x45DD
CHAPTER_ATOM = 0xB6
CHAPTER_TIME_START = 0x91
CHAPTER_FLAG_HIDDEN = 0x98
CHAPTER_FLAG_ENABLED = 0x4598
CHAPTER_DISPLAY = 0x80
CHAP_STRING = 0x85

MAX_CHAPTERS_SIZE = 1024 * 1024  # bytes, a chapter list is tiny, anything bigger is a broken file
MAX_TOP_LEVEL_ELEMENTS = 4096  # When there is no SeekHead entry, give up scanning after this many elements
UNKNOWN_SIZE = -1


def log(msg, level=1):
    ulog(msg, name='Chapters', level=level)


class ChapterError(Exception):
    """The file is not a Matroska file that can be read"""


class _Reader:
    """Reads from an xbmcvfs.File, or any file object with seek() and read()/readBytes()"""

    def __init__(self, handle):
        self.handle = handle

    def seek(self, position):
        self.handle.seek(position, os.SEEK_SET)

    def read(self, size):
        if hasattr(self.handle, 'readBytes'):
            data = self.handle.readBytes(size)
        else:
            data = self.handle.read(size)
        data = bytes(data)
        if len(data) < size:
            raise ChapterError('unexpected end of file')
        return data


def _vint(reader, keep_marker):
    """Read an EBML variable length integer: an element ID (keep_marker) or an element size"""
    first = reader.read(1)[0]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        length += 1
        mask >>= 1
    if length > 8:
        raise ChapterError('invalid EBML number')
    value = first if keep_marker else first & (mask - 1)
    all_ones = value == mask - 1
    for byte in reader.read(length - 1):
        value = (value << 8) | byte
        all_ones = all_ones and byte == 0xFF
    if not keep_marker and all_ones:
        return UNKNOWN_SIZE, length
    return value, length


def _element_header(reader):
    """ID, data size and header length of the element at the current position"""
    element_id, id_length = _vint(reader, keep_marker=True)
    size, size_length = _vint(reader, keep_marker=False)
    return element_id, size, id_length + size_length


def _children(data):
    """(ID, data) of every child element in a master element that was read completely"""
    position = 0
    while position < len(data):
        reader = _BytesReader(data, position)
        element_id, size, header = _element_header(reader)
        if size == UNKNOWN_SIZE or position + header + size > len(data):
            raise ChapterError('broken element')
        start = position + header
        yield element_id, data[start:start + size]
        position = start + size


class _BytesReader(_Reader):
    """_Reader over bytes in memory"""

    def __init__(self, data, position=0):  # pylint: disable=super-init-not-called
        self.data = data
        self.position = position

    def seek(self, position):
        self.position = position

    def read(self, size):
        if self.position + size > len(self.data):
            raise ChapterError('unexpected end of element')
        chunk = self.data[self.position:self.position + size]
        self.position += size
        return chunk


def _uint(data):
    return int.from_bytes(data, 'big') if data else 0


def _parse_atom(atom):
    """(start in seconds, name) of a ChapterAtom, None when it has no start or is not shown"""
    start = None
    name = ''
    shown = True
    for child_id, child in _children(atom):
        if child_id == CHAPTER_TIME_START:
            start = _uint(child) / 1e9  # Chapter times are always in nanoseconds
        elif child_id == CHAPTER_FLAG_HIDDEN and _uint(child):
            shown = False
        elif child_id == CHAPTER_FLAG_ENABLED and not _uint(child):
            shown = False
        elif child_id == CHAPTER_DISPLAY and not name:
            name = next((display.decode('utf-8', 'replace').strip()
                         for display_id, display in _children(child) if display_id == CHAP_STRING), '')
    if start is None or not shown:
        return None
    return start, name


def _parse_chapters(data):
    """List of (start in seconds, name) of the default edition, None when its times can't be used"""
    editions = []
    for element_id, edition in _children(data):
        if element_id != EDITION_ENTRY:
            continue
        flags = {'default': False, 'hidden': False, 'ordered': False}
        atoms = []
        for child_id, child in _children(edition):
            if child_id == EDITION_FLAG_DEFAULT:
                flags['default'] = bool(_uint(child))
            elif child_id == EDITION_FLAG_HIDDEN:
                flags['hidden'] = bool(_uint(child))
            elif child_id == EDITION_FLAG_ORDERED:
                flags['ordered'] = bool(_uint(child))
            elif child_id == CHAPTER_ATOM:
                atoms.append(child)
        editions.append((flags, atoms))
    if not editions:
        return []

    # The edition a player uses: the default one, otherwise the first one
    flags, atoms = next((edition for edition in editions if edition[0]['default']), editions[0])
    if flags['ordered']:
        # Ordered chapters can play parts of other files, their times are not times in this video
        return None

    chapters = [chapter for chapter in map(_parse_atom, atoms) if chapter]
    chapters.sort(key=lambda chapter: chapter[0])
    return chapters


def _find_chapters_position(reader, segment_start, segment_end):
    """File position of the Chapters element, None when the file has none, ChapterError when unknown"""
    position = segment_start
    for _ in range(MAX_TOP_LEVEL_ELEMENTS):
        if segment_end is not None and position >= segment_end:
            return None
        reader.seek(position)
        try:
            element_id, size, header = _element_header(reader)
        except ChapterError:
            return None  # Reached the end of the file
        if element_id == CHAPTERS:
            return position
        if element_id == SEEK_HEAD and 0 <= size <= MAX_CHAPTERS_SIZE:
            for seek_id, seek in _children(reader.read(size)):
                if seek_id != SEEK:
                    continue
                entry = dict(_children(seek))
                if _uint(entry.get(SEEK_ID)) == CHAPTERS and SEEK_POSITION in entry:
                    return segment_start + _uint(entry[SEEK_POSITION])
        if size == UNKNOWN_SIZE:
            raise ChapterError('element of unknown size, the rest of the file can not be skipped')
        # Skip anything else, video data (Cluster) included, without reading it
        position += header + size
    raise ChapterError('gave up looking for chapters after %d elements' % MAX_TOP_LEVEL_ELEMENTS)


def read_chapters(handle):
    """List of (start in seconds, name) from an open Matroska file, None when it can't be read"""
    reader = _Reader(handle)
    reader.seek(0)
    element_id, size, header = _element_header(reader)
    if element_id != EBML or size == UNKNOWN_SIZE:
        raise ChapterError('not a Matroska file')
    segment_position = header + size
    reader.seek(segment_position)
    element_id, size, header = _element_header(reader)
    if element_id != SEGMENT:
        raise ChapterError('no Matroska segment')
    segment_start = segment_position + header  # SeekHead positions count from here
    segment_end = None if size == UNKNOWN_SIZE else segment_start + size

    position = _find_chapters_position(reader, segment_start, segment_end)
    if position is None:
        return []
    reader.seek(position)
    element_id, size, header = _element_header(reader)
    if element_id != CHAPTERS or not 0 <= size <= MAX_CHAPTERS_SIZE:
        raise ChapterError('broken Chapters element')
    return _parse_chapters(reader.read(size))


def chapters_path_supported(media_path):
    """Whether chapters can be read from this file"""
    if not media_path or media_path.startswith(UNSUPPORTED):
        return False
    if media_path.startswith(('http://', 'https://')) and ('?' in media_path or '#' in media_path):
        return False
    return os.path.splitext(media_path)[1].lower() in EXTENSIONS


def load_chapters(media_path):
    """List of (start in seconds, name) of a video file, or None when its chapters could not be read

    An empty list means the file was read and has no chapters.
    """
    if not chapters_path_supported(media_path):
        return None
    import xbmcvfs  # pylint: disable=import-outside-toplevel
    try:
        handle = xbmcvfs.File(media_path)
        try:
            chapters = read_chapters(handle)
        finally:
            handle.close()
    except (ChapterError, IndexError, struct.error) as error:
        log('Could not read the chapters of %s: %s' % (media_path, error))
        return None
    except Exception as error:  # pylint: disable=broad-exception-caught
        # A share that can't be read must never stop the add-on
        log('Could not open %s to read its chapters: %s' % (media_path, error))
        return None
    if chapters is None:
        log('%s uses ordered chapters, their times are not used' % media_path)
    else:
        log('Read %d chapters from %s' % (len(chapters), media_path), 2)
    return chapters


def find_credits_start(chapters, keywords, total_time):
    """(start in seconds, name) of the credits chapter, or None when there is none

    Opening credits are also called "credits": only a chapter in the second half of the video,
    that is not the first chapter, and not in the first half of the chapters, counts.
    """
    if not chapters or len(chapters) < 2 or not total_time:
        return None
    keywords = [keyword.strip().lower() for keyword in keywords if keyword and keyword.strip()]
    for index, (start, name) in enumerate(chapters):
        if index == 0 or index + 1 < len(chapters) / 2:
            continue
        if not total_time * 0.5 <= start < total_time:
            continue
        if any(keyword in name.lower() for keyword in keywords):
            return start, name
    return None
