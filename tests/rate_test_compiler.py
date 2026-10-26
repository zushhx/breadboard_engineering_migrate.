"""Select a native host compiler; keep the pinned Windows Zig fallback."""
import os
from pathlib import Path
import shutil


def compiler_command(root):
    native = shutil.which('g++') or shutil.which('clang++')
    if native:
        return [native]
    zig = Path(root)/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
    if os.name == 'nt' and zig.is_file():
        return [str(zig), 'c++']
    raise RuntimeError('A native g++/clang++ compiler or pinned Windows Zig 0.13.0 is required')


def binary_path(folder, name):
    return Path(folder)/(name + ('.exe' if os.name == 'nt' else ''))
