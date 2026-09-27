"""Reuse eSpeak inside one sequential worker without changing its phonemes."""
import os


class ReusableTokenizer:
    def __init__(self, original):
        self.original = original
        self.language = None
        self.backend = None

    def __getattr__(self, name):
        return getattr(self.original, name)

    def phonemize(self, text, lang='en-us', norm=True):
        from phonemizer.backend import EspeakBackend
        from phonemizer.separator import default_separator
        from phonemizer.utils import list2str, str2list

        if norm:
            text = self.original.normalize_text(text)
        if self.backend is None or self.language != lang:
            self.backend = EspeakBackend(lang, preserve_punctuation=True, with_stress=True)
            self.language = lang
        # Match phonemizer.phonemize's string-input/default-option processing.
        lines = [line.strip(os.linesep) for line in str2list(text)]
        lines = [line for line in lines if line.strip()]
        result = self.backend.phonemize(lines, separator=default_separator, strip=False, njobs=1) if lines else []
        phonemes = list2str(result)
        return ''.join(p for p in phonemes if p in self.original.vocab).strip()


def reuse_phonemizer(kokoro):
    tokenizer = getattr(kokoro, 'tokenizer', None)
    if tokenizer is not None:
        kokoro.tokenizer = ReusableTokenizer(tokenizer)
