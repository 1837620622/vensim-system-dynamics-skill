"""保留编码、换行、续行和未解释记录的 MDL 文档读写。"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
import re
import tempfile

SKETCH_MARKER = b"\\\\\\---///"


def separate_output(output: Path, inputs) -> None:
    for source in inputs:
        if output.resolve() == source.resolve() or (
            output.exists() and source.exists() and output.samefile(source)
        ):
            raise ValueError(f"输出不能覆盖输入: {output}")


def atomic_write(path: Path, data: bytes) -> None:
    """同目录写入后替换，避免异常留下半个模型；不覆盖输入由调用方检查。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vensim-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        # Windows 不允许删除仍然打开的临时文件，清理必须在 with 结束后执行。
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclasses.dataclass
class MdlDocument:
    raw: bytes
    encoding: str

    @classmethod
    def read(cls, path: Path) -> MdlDocument:
        raw = path.read_bytes()
        # Vensim 可能在 UTF-8 字节中间插入续行。编码探测使用展开后的副本。
        probe = re.sub(rb"\\\r?\n[ \t]*", b"", raw)
        for encoding in ("utf-8", "gb18030", "cp1252", "latin-1"):
            try:
                probe.decode(encoding)
                return cls(raw, encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法识别模型编码: {path}")

    @property
    def text(self) -> str:
        # surrogateescape 只用于原样往返，不能把字节中间的续行变成新字符。
        return self.raw.decode(self.encoding, errors="surrogateescape")

    @property
    def equation_bytes(self) -> bytes:
        return self.raw.split(SKETCH_MARKER, 1)[0]

    @property
    def semantic_text(self) -> str:
        equation, marker, sketch = self.raw.partition(SKETCH_MARKER)
        equation = re.sub(rb"\\\r?\n[ \t]*", b"", equation)
        return (equation + marker + sketch).decode(self.encoding).lstrip("\ufeff")

    def encode(self, lines: list[str]) -> bytes:
        data = "".join(lines).encode(self.encoding, errors="surrogateescape")
        if data.split(SKETCH_MARKER, 1)[0] != self.equation_bytes:
            raise ValueError("布局修改了方程区，已拒绝写入")
        return data
