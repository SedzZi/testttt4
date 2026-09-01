# PC'de pyjnius bulunmadigi icin Pylance icin sahil tip tanimi.
# Bu dosya APK'ya paketlenmez (source.include_exts = py,json,png).
from typing import Any, Callable


def autoclass(name: str) -> type: ...

def cast(jclass: type, obj: Any) -> Any: ...

def jbytearray(data: "bytes | bytearray | int") -> Any: ...

def java_method(*args: Any, **kwargs: Any) -> Callable[..., Any]: ...


class JavaClass:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...


class PythonJavaClass:
    java_implements: list[str]
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
