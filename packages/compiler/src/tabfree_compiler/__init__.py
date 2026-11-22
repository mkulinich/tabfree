"""TabFree compiler: Tableau's calculation language and semantic queries to Athena, Redshift and Trino SQL."""

from tabfree_compiler.calc import CalcSyntaxError, classify, parse
from tabfree_compiler.model import CompileError, SemanticModel, UnsupportedError
from tabfree_compiler.sql import (
    DIALECTS,
    CompiledQuery,
    OutputColumn,
    Session,
    compile_expression,
    compile_query,
)

__version__ = "0.0.1"

__all__ = [
    "DIALECTS",
    "CalcSyntaxError",
    "CompileError",
    "CompiledQuery",
    "OutputColumn",
    "SemanticModel",
    "Session",
    "UnsupportedError",
    "classify",
    "compile_expression",
    "compile_query",
    "parse",
]
