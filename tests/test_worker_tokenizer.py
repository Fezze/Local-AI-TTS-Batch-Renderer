from pathlib import Path

from local_tts_renderer.worker_tokenizer import ReusableTokenizer


def test_reused_espeak_produces_identical_phonemes():
    from kokoro_onnx.tokenizer import Tokenizer

    original = Tokenizer()
    cached = ReusableTokenizer(original)
    samples = [
        'A short sentence. Another one!',
        'Mr. Smith paid $12.50 for twenty-three books.',
        '  First paragraph.\n\nSecond paragraph?  ',
        '“Hello,” she said. Café, naïve, déjà vu.',
        '', '  \n ',
    ]
    expected = [original.phonemize(text) for text in samples]
    for _ in range(3):
        assert [cached.phonemize(text) for text in samples] == expected
    backend = cached.backend
    maps = Path('/proc/self/maps')
    def native_copies():
        return set() if not maps.exists() else {
            line.split()[-1] if not line.endswith('(deleted)') else line.split()[-2]
            for line in maps.read_text().splitlines() if 'libespeak' in line
        }
    before = native_copies()
    for _ in range(12):
        cached.phonemize(samples[0])
    assert native_copies() == before
    assert cached.backend is backend
    assert cached.tokenize(expected[0]) == original.tokenize(expected[0])
    assert cached.phonemize(samples[0], lang='en-gb') == original.phonemize(samples[0], lang='en-gb')
