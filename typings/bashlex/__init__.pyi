from bashlex import ast as ast
from bashlex import errors as errors

def parse(
    s: str,
    strictmode: bool = ...,
    expansionlimit: int | None = ...,
    convertpos: bool = ...,
) -> list[ast.node]: ...
