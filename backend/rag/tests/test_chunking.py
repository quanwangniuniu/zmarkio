"""
Unit tests for rag.chunking.chunk_text — pure function, no DB.

Worked example used throughout: chunk_size=10, overlap=3 -> step=7, against
the 25-character text 'ABCDEFGHIJKLMNOPQRSTUVWXY' (A=index 0 .. Y=index 24).

    start=0  end=10  text[0:10]   -> 'ABCDEFGHIJ'   (indices 0-9)
    start=7  end=17  text[7:17]   -> 'HIJKLMNOPQ'   (indices 7-16)
    start=14 end=24  text[14:24]  -> 'OPQRSTUVWX'   (indices 14-23)
    start=21 end=31  text[21:31]  -> 'VWXY'         (indices 21-24, tail, len 4 < 10)

Each step advances by 7 (= chunk_size - overlap), so consecutive chunks
share exactly 3 overlapping characters (e.g. chunk 1 ends '...HIJ', chunk 2
starts 'HIJ...').
"""
import pytest
from django.test import override_settings

from rag.chunking import chunk_text

CHUNK_SETTINGS = {'RAG_CHUNK_SIZE': 10, 'RAG_CHUNK_OVERLAP': 3}
WORKED_EXAMPLE_TEXT = 'ABCDEFGHIJKLMNOPQRSTUVWXY'  # 25 chars
WORKED_EXAMPLE_CHUNKS = ['ABCDEFGHIJ', 'HIJKLMNOPQ', 'OPQRSTUVWX', 'VWXY']


@pytest.mark.unit
def test_empty_text_returns_no_chunks():
    with override_settings(**CHUNK_SETTINGS):
        assert chunk_text('') == []


@pytest.mark.unit
def test_whitespace_only_text_returns_no_chunks():
    with override_settings(**CHUNK_SETTINGS):
        assert chunk_text('   \n\t  ') == []


@pytest.mark.unit
def test_text_shorter_than_chunk_size_returns_one_chunk():
    with override_settings(**CHUNK_SETTINGS):
        assert chunk_text('ABC') == ['ABC']


@pytest.mark.unit
def test_text_exactly_chunk_size_returns_one_chunk():
    text = 'ABCDEFGHIJ'  # exactly 10 chars
    with override_settings(**CHUNK_SETTINGS):
        assert chunk_text(text) == [text]


@pytest.mark.unit
def test_longer_text_produces_expected_overlapping_chunks():
    with override_settings(**CHUNK_SETTINGS):
        assert chunk_text(WORKED_EXAMPLE_TEXT) == WORKED_EXAMPLE_CHUNKS


@pytest.mark.unit
def test_tail_chunk_is_preserved_even_if_shorter_than_chunk_size():
    with override_settings(**CHUNK_SETTINGS):
        chunks = chunk_text(WORKED_EXAMPLE_TEXT)
    assert chunks[-1] == 'VWXY'
    assert len(chunks[-1]) < CHUNK_SETTINGS['RAG_CHUNK_SIZE']


@pytest.mark.unit
def test_overlap_boundaries_are_correct():
    overlap = CHUNK_SETTINGS['RAG_CHUNK_OVERLAP']
    with override_settings(**CHUNK_SETTINGS):
        chunks = chunk_text(WORKED_EXAMPLE_TEXT)
    for previous_chunk, next_chunk in zip(chunks, chunks[1:]):
        assert previous_chunk[-overlap:] == next_chunk[:overlap]


@pytest.mark.unit
def test_zero_overlap_produces_contiguous_non_overlapping_chunks():
    # chunk_size=5, overlap=0 -> step=5 (no overlap at all, pure tiling)
    # 'ABCDEFGHIJK' (11 chars): [0:5]='ABCDE' [5:10]='FGHIJ' [10:15]='K' (tail)
    with override_settings(RAG_CHUNK_SIZE=5, RAG_CHUNK_OVERLAP=0):
        assert chunk_text('ABCDEFGHIJK') == ['ABCDE', 'FGHIJ', 'K']


@pytest.mark.unit
def test_same_input_and_config_always_produces_identical_output():
    text = WORKED_EXAMPLE_TEXT * 3
    with override_settings(**CHUNK_SETTINGS):
        first = chunk_text(text)
        second = chunk_text(text)
    assert first == second
