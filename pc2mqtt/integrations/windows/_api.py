"""Lazy Win32 function binding, shared without loading unrelated DLLs."""

import ctypes as C


class NativeAPI:
    def __init__(self):
        self._functions = {}
        self._reasons = {}

    def bind(self, library, name, restype, argtypes=()):
        key = library, name
        if key not in self._functions:
            function = getattr(C.WinDLL(library, use_last_error=True), name)
            function.restype = restype
            function.argtypes = list(argtypes)
            self._functions[key] = function
        return self._functions[key]

    def supported_features(self):
        features = set()
        for key in self.features:
            try:
                self.prepare(key)
            except (AttributeError, OSError) as exc:
                self._reasons[key] = str(exc)
            else:
                features.add(key)
                self._reasons.pop(key, None)
        return features

    def unsupported_reason(self, key):
        return self._reasons.get(key, 'required Windows API is unavailable')
