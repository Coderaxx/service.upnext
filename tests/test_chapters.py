# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
"""Reading the chapter list of a Matroska file"""

import io

import pytest
import chapters

CLUSTER = 0x1F43B675
INFO = 0x1549A966
CHAPTERS_ID = b'\x10\x43\xa7\x70'


def element(element_id, data=b'', unknown_size=False):
    """A Matroska element: ID bytes, 8 byte size, data"""
    id_bytes = element_id.to_bytes((element_id.bit_length() + 7) // 8, 'big')
    size = b'\x01\xff\xff\xff\xff\xff\xff\xff' if unknown_size else (0x01 << 56 | len(data)).to_bytes(8, 'big')
    return id_bytes + size + data


def uint(element_id, value):
    return element(element_id, value.to_bytes(8, 'big'))


def atom(start, name, hidden=False):
    data = uint(chapters.CHAPTER_TIME_START, int(start * 1e9))
    if hidden:
        data += uint(chapters.CHAPTER_FLAG_HIDDEN, 1)
    data += element(chapters.CHAPTER_DISPLAY, element(chapters.CHAP_STRING, name.encode('utf-8')))
    return element(chapters.CHAPTER_ATOM, data)


def edition(*atoms, default=False, ordered=False):
    data = b''
    if default:
        data += uint(chapters.EDITION_FLAG_DEFAULT, 1)
    if ordered:
        data += uint(chapters.EDITION_FLAG_ORDERED, 1)
    return element(chapters.EDITION_ENTRY, data + b''.join(atoms))


def seek_head(position):
    return element(chapters.SEEK_HEAD, element(chapters.SEEK, element(chapters.SEEK_ID, CHAPTERS_ID)
                                               + uint(chapters.SEEK_POSITION, position)))


def mkv(chapters_element=b'', before=True, with_seek_head=False, unknown_cluster=False):
    """A minimal Matroska file, with the chapters before or after the video data"""
    header = element(chapters.EBML, element(0x4282, b'matroska'))
    cluster = element(CLUSTER, b'\x00' * 5000, unknown_size=unknown_cluster)
    body_before = element(INFO, b'\x00' * 10) + (chapters_element if before else b'')
    body_after = b'' if before else chapters_element
    if with_seek_head:
        position = len(seek_head(0)) + len(body_before) + len(cluster)
        body_before = seek_head(position) + body_before
    segment = element(chapters.SEGMENT, body_before + cluster + body_after)
    return io.BytesIO(header + segment)


CHAPTERS_ELEMENT = element(chapters.CHAPTERS, edition(
    atom(0, 'Intro'), atom(60, 'Part 1'), atom(1200.5, 'Part 2'), atom(2300, 'End Credits')))


def test_reads_chapters_before_the_video_data():
    assert chapters.read_chapters(mkv(CHAPTERS_ELEMENT)) == [
        (0.0, 'Intro'), (60.0, 'Part 1'), (1200.5, 'Part 2'), (2300.0, 'End Credits')]


def test_reads_chapters_after_the_video_data():
    assert len(chapters.read_chapters(mkv(CHAPTERS_ELEMENT, before=False))) == 4


def test_follows_the_seek_head():
    assert len(chapters.read_chapters(mkv(CHAPTERS_ELEMENT, before=False, with_seek_head=True))) == 4


def test_no_chapters():
    assert chapters.read_chapters(mkv()) == []


def test_unknown_size_video_data_is_not_a_file_without_chapters():
    with pytest.raises(chapters.ChapterError):
        chapters.read_chapters(mkv(CHAPTERS_ELEMENT, before=False, unknown_cluster=True))


def test_default_edition_and_hidden_chapters():
    data = element(chapters.CHAPTERS, edition(atom(0, 'Other')) + edition(
        atom(0, 'Intro'), atom(100, 'Secret', hidden=True), atom(2000, 'Credits'), default=True))
    assert chapters.read_chapters(mkv(data)) == [(0.0, 'Intro'), (2000.0, 'Credits')]


def test_ordered_chapters_are_not_used():
    data = element(chapters.CHAPTERS, edition(atom(0, 'Intro'), atom(2000, 'Credits'), ordered=True))
    assert chapters.read_chapters(mkv(data)) is None


def test_not_matroska():
    with pytest.raises(chapters.ChapterError):
        chapters.read_chapters(io.BytesIO(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 100))


@pytest.mark.parametrize('chapter_list, expected', [
    ([(0, 'Intro'), (60, 'Part 1'), (1200, 'Part 2'), (2300, 'End Credits')], (2300, 'End Credits')),
    ([(0, 'Intro'), (60, 'Part 1'), (1200, 'Part 2'), (2300, 'Outro')], (2300, 'Outro')),
    ([(0, 'Opening Credits'), (60, 'Part 1'), (1200, 'Part 2')], None),
    ([(0, 'Intro'), (60, 'Credits'), (1200, 'Part 2'), (2300, 'Part 3')], None),
    ([(0, 'Credits')], None),
    ([], None),
])
def test_find_credits_start(chapter_list, expected):
    assert chapters.find_credits_start(chapter_list, ['credits', ' outro ', ''], 2400) == expected


@pytest.mark.parametrize('path, supported', [
    ('/media/Show/S01E01.mkv', True),
    ('smb://nas/Show/S01E01.MKV', True),
    ('/media/Show/S01E01.mp4', False),
    ('plugin://plugin.video.x/play?id=1', False),
    ('https://example.com/a.mkv?token=1', False),
])
def test_chapters_path_supported(path, supported):
    assert chapters.chapters_path_supported(path) == supported
