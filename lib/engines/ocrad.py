# encoding=UTF-8
# Copyright © 2010 Jakub Wilk <jwilk@jwilk.net>
#
# This package is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 dated June, 1991.
#
# This package is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.

import contextlib
import re

from .. import errors
from .. import ipc
from .. import text_zones
from .. import unicode_support

const = text_zones.const

from ..hocr import TEXT_DETAILS_LINE, TEXT_DETAILS_WORD, TEXT_DETAILS_CHARACTER

_default_language = 'eng'
_language_pattern = re.compile('^[a-z]{3}$')

def get_languages():
    result = [_default_language]
    try:
        ocrad = ipc.Subprocess(['ocrad', '--charset=help'],
            stdout=ipc.PIPE,
            stderr=ipc.PIPE,
        )
    except OSError:
        raise errors.UnknownLanguageList
    try:
        line = ocrad.stderr.read()
        charsets = set(line.split()[1:])
        if 'iso-8859-9' in charsets:
            result += 'tur',
    finally:
        try:
            ocrad.wait()
        except ipc.CalledProcessError:
            pass
        else:
            raise errors.UnknownLanguageList
    return result

def has_language(language):
    if not _language_pattern.match(language):
        raise errors.InvalidLanguageId(language)
    return language in get_languages()

@contextlib.contextmanager
def recognize(pbm_file, language, details=None):
    charset = 'iso-8859-15'
    if language == 'tur':
        charset = 'iso-8859-9'
    worker = ipc.Subprocess(
        ['ocrad', '--charset', charset, '--format=utf8', '-x', '-', pbm_file.name],
        stdout=ipc.PIPE,
    )
    try:
        yield worker.stdout
    finally:
        worker.wait()

class ExtractSettings(object):

    def __init__(self, rotation=0, details=TEXT_DETAILS_WORD, uax29=None, page_size=None):
        self.rotation = rotation
        self.details = details
        if uax29 is not None:
            icu = unicode_support.get_icu()
            if uax29 is True:
                uax29 = icu.Locale()
            else:
                uax29 = icu.Locale(uax29)
        self.uax29 = uax29
        self.page_size = page_size

_character_re = re.compile(r"^[0-9]+, '('|[^']*)'[0-9]+")

def scan(stream, settings):
    for line in stream:
        if line.startswith('#'):
            continue
        if line.startswith('source '):
            continue
        if line.startswith('total text blocks '):
            n, = line.split()[3:]
            n = int(n)
            bbox = text_zones.BBox(*((0, 0) + settings.page_size))
            children = filter(None, (scan(stream, settings) for i in xrange(n)))
            zone = text_zones.Zone(const.TEXT_ZONE_PAGE, bbox, children)
            zone.rotate(settings.rotation)
            return zone
        if line.startswith('text block '):
            n, x, y, w, h = map(int, line.split()[2:])
            bbox = text_zones.BBox(x, y, x + w, y + h)
            [children] = [scan(stream, settings) for i in xrange(n)]
            return text_zones.Zone(const.TEXT_ZONE_REGION, bbox, children)
        if line.startswith('lines '):
            n, = line.split()[1:]
            n = int(n)
            return filter(None, (scan(stream, settings) for i in xrange(n)))
        if line.startswith('line '):
            _, _, _, n, _, _ = line.split()
            n = int(n)
            children = filter(None, (scan(stream, settings) for i in xrange(n)))
            if not children:
                return None
            bbox = text_zones.BBox()
            for child in children:
                bbox.update(child.bbox)
            text = ''.join(child[0] for child in children)
            if settings.details > const.TEXT_ZONE_WORD:
                # One zone per line
                children = [text]
            else:
                # One zone per word
                if len(text) != len(children):
                    raise errors.MalformedOcrOutput("number of characters (%d) doesn't match text length (%d)" % (len(children), len(text)))
                words = []
                break_iterator = unicode_support.word_break_iterator(text, locale=settings.uax29)
                i = 0
                for j in break_iterator:
                    subtext = text[i:j]
                    if subtext.isspace():
                        i = j
                        continue
                    word_bbox = text_zones.BBox()
                    for k in xrange(i, j):
                        word_bbox.update(children[k].bbox)
                    last_word = text_zones.Zone(type=const.TEXT_ZONE_WORD, bbox=word_bbox)
                    words += last_word,
                    if settings.details > const.TEXT_ZONE_CHARACTER:
                        last_word += subtext,
                    else:
                        last_word += [
                            text_zones.Zone(type=const.TEXT_ZONE_CHARACTER, bbox=(x0, y0, x1, y1), children=[ch])
                            for k in xrange(i, j)
                            for (x0, y0, x1, y1), ch in [(children[k].bbox, text[k])]
                        ]
                    i = j
                children = words
            return text_zones.Zone(const.TEXT_ZONE_LINE, bbox, children)
        if line[0].isdigit():
            coords, line = line.split('; ', 1)
            x, y, w, h = map(int, coords.split())
            bbox = text_zones.BBox(x, y, x + w, y + h)
            m = _character_re.match(line)
            if not m:
                raise errors.MalformedOcrOutput('bad character description: %r' % line)
            return text_zones.Zone(const.TEXT_ZONE_CHARACTER, bbox, m.groups())
        raise errors.MalformedOcrOutput('unexpected line: %r' % line)
    raise errors.MalformedOcrOutput('unexpected line at EOF: %r' % line)

class Engine(object):

    name = 'ocrad'
    image_format = 'ppm'
    output_format = 'orf'

    def __init__(self):
        try:
            get_languages()
        except errors.UnknownLanguageList:
            raise errors.EngineNotFound(self.name)

    @staticmethod
    def get_default_language():
        return _default_language

    has_language = staticmethod(has_language)
    list_languages = staticmethod(get_languages)
    recognize = staticmethod(recognize)

    @staticmethod
    def extract_text(stream, **kwargs):
        settings = ExtractSettings(**kwargs)
        scan_result = scan(stream, settings)
        return [scan_result.sexpr]

# vim:ts=4 sw=4 et