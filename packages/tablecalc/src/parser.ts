/**
 * A parser for Tableau table-calculation formulas. It produces a plain syntax tree; nothing is ever
 * evaluated as JavaScript. Supported: literals ('text', "text", numbers, TRUE, FALSE, NULL), fields
 * ([Sales], with ]] escaping a bracket), function calls, + - * / % ^, = == != <> < <= > >=,
 * AND OR NOT, IF/ELSEIF/ELSE/END, CASE/WHEN/ELSE/END, and // or /* *\/ comments.
 * Precedence follows Tableau: negation, ^, * / %, + -, comparisons, NOT, AND, OR.
 */

import { TableCalcError, type Scalar } from "./types.js";

export type Expr =
  | { kind: "literal"; value: Scalar }
  | { kind: "field"; name: string }
  | { kind: "call"; name: string; args: Expr[] }
  | { kind: "unary"; op: "-" | "NOT"; arg: Expr }
  | { kind: "binary"; op: BinaryOp; left: Expr; right: Expr }
  | { kind: "if"; branches: [Expr, Expr][]; otherwise: Expr | null }
  | { kind: "case"; subject: Expr; whens: [Expr, Expr][]; otherwise: Expr | null };

export type BinaryOp = "+" | "-" | "*" | "/" | "%" | "^" | "=" | "!=" | "<" | "<=" | ">" | ">=" | "AND" | "OR";

type Token =
  | { t: "num"; v: number; pos: number }
  | { t: "str"; v: string; pos: number }
  | { t: "field"; v: string; pos: number }
  | { t: "ident"; v: string; pos: number }
  | { t: "op"; v: string; pos: number }
  | { t: "eof"; pos: number };

const KEYWORDS = new Set(["IF", "THEN", "ELSEIF", "ELSE", "END", "CASE", "WHEN", "AND", "OR", "NOT", "TRUE", "FALSE", "NULL"]);

const MAX_LENGTH = 20000;
const MAX_DEPTH = 200;

function tokenize(src: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  const fail = (msg: string, pos: number): never => {
    throw new TableCalcError(`${msg} at position ${pos + 1} in table calculation "${src}"`);
  };
  while (i < src.length) {
    const c = src[i] ?? "";
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (c === "/" && src[i + 1] === "/") {
      while (i < src.length && src[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && src[i + 1] === "*") {
      const end = src.indexOf("*/", i + 2);
      if (end < 0) fail("unterminated comment", i);
      i = end + 2;
      continue;
    }
    const start = i;
    if (c === "[") {
      let name = "";
      i++;
      for (;;) {
        if (i >= src.length) fail("unterminated field name", start);
        const d = src[i];
        if (d === "]") {
          if (src[i + 1] === "]") {
            name += "]";
            i += 2;
            continue;
          }
          i++;
          break;
        }
        name += d;
        i++;
      }
      if (src[i] === ".") fail("qualified field names such as [Parameters].[x] are not supported", i);
      tokens.push({ t: "field", v: name, pos: start });
      continue;
    }
    if (c === "'" || c === '"') {
      let text = "";
      i++;
      for (;;) {
        if (i >= src.length) fail("unterminated string", start);
        const d = src[i];
        if (d === c) {
          if (src[i + 1] === c) {
            text += c;
            i += 2;
            continue;
          }
          i++;
          break;
        }
        text += d;
        i++;
      }
      tokens.push({ t: "str", v: text, pos: start });
      continue;
    }
    const num = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(src.slice(i));
    if (num) {
      tokens.push({ t: "num", v: Number(num[0]), pos: start });
      i += num[0].length;
      continue;
    }
    const ident = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i));
    if (ident) {
      tokens.push({ t: "ident", v: ident[0].toUpperCase(), pos: start });
      i += ident[0].length;
      continue;
    }
    const op = /^(==|!=|<>|<=|>=|[-+*/%^=<>(),])/.exec(src.slice(i));
    if (op) {
      tokens.push({ t: "op", v: op[0], pos: start });
      i += op[0].length;
      continue;
    }
    if (c === "#") fail("date literals are not supported", i);
    fail(`unexpected character "${c}"`, i);
  }
  tokens.push({ t: "eof", pos: src.length });
  return tokens;
}

/** Parses a Tableau formula into a syntax tree, or throws TableCalcError. */
export function parseExpression(src: string): Expr {
  if (src.length > MAX_LENGTH) throw new TableCalcError(`table calculation is longer than ${MAX_LENGTH} characters`);
  const tokens = tokenize(src);
  let p = 0;
  let depth = 0;
  const peek = (): Token => tokens[p] ?? { t: "eof", pos: src.length };
  const fail = (msg: string, tok: Token = peek()): never => {
    throw new TableCalcError(`${msg} at position ${tok.pos + 1} in table calculation "${src}"`);
  };
  const isOp = (v: string): boolean => {
    const tok = peek();
    return tok.t === "op" && tok.v === v;
  };
  const isKw = (v: string): boolean => {
    const tok = peek();
    return tok.t === "ident" && tok.v === v;
  };
  const expectOp = (v: string): void => {
    if (!isOp(v)) fail(`expected "${v}"`);
    p++;
  };
  const expectKw = (v: string): void => {
    if (!isKw(v)) fail(`expected ${v}`);
    p++;
  };
  const describe = (tok: Token): string =>
    tok.t === "eof" ? "end of formula" : tok.t === "field" ? `[${tok.v}]` : `"${String(tok.v)}"`;

  const nested = <T>(fn: () => T): T => {
    if (++depth > MAX_DEPTH) fail("formula is nested too deeply");
    try {
      return fn();
    } finally {
      depth--;
    }
  };

  const parseOr = (): Expr =>
    nested(() => {
      let left = parseAnd();
      while (isKw("OR")) {
        p++;
        left = { kind: "binary", op: "OR", left, right: parseAnd() };
      }
      return left;
    });
  const parseAnd = (): Expr => {
    let left = parseNot();
    while (isKw("AND")) {
      p++;
      left = { kind: "binary", op: "AND", left, right: parseNot() };
    }
    return left;
  };
  const parseNot = (): Expr => {
    if (isKw("NOT")) {
      p++;
      return nested(() => ({ kind: "unary", op: "NOT", arg: parseNot() }));
    }
    return parseCompare();
  };
  const COMPARE: Record<string, BinaryOp> = { "=": "=", "==": "=", "!=": "!=", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=" };
  const parseCompare = (): Expr => {
    const left = parseAdd();
    const tok = peek();
    if (tok.t === "op" && COMPARE[tok.v]) {
      p++;
      return { kind: "binary", op: COMPARE[tok.v] ?? "=", left, right: parseAdd() };
    }
    return left;
  };
  const parseAdd = (): Expr => {
    let left = parseMul();
    while (isOp("+") || isOp("-")) {
      const op = (peek() as { v: string }).v as "+" | "-";
      p++;
      left = { kind: "binary", op, left, right: parseMul() };
    }
    return left;
  };
  const parseMul = (): Expr => {
    let left = parsePow();
    while (isOp("*") || isOp("/") || isOp("%")) {
      const op = (peek() as { v: string }).v as "*" | "/" | "%";
      p++;
      left = { kind: "binary", op, left, right: parsePow() };
    }
    return left;
  };
  const parsePow = (): Expr => {
    const left = parseNeg();
    if (isOp("^")) {
      p++;
      return nested(() => ({ kind: "binary", op: "^", left, right: parsePow() }));
    }
    return left;
  };
  const parseNeg = (): Expr => {
    if (isOp("-")) {
      p++;
      return nested(() => ({ kind: "unary", op: "-", arg: parseNeg() }));
    }
    if (isOp("+")) {
      p++;
      return nested(() => parseNeg());
    }
    return parsePrimary();
  };
  const parsePrimary = (): Expr => {
    const tok = peek();
    switch (tok.t) {
      case "num":
        p++;
        return { kind: "literal", value: tok.v };
      case "str":
        p++;
        return { kind: "literal", value: tok.v };
      case "field":
        p++;
        return { kind: "field", name: tok.v };
      case "op":
        if (tok.v === "(") {
          p++;
          const inner = parseOr();
          expectOp(")");
          return inner;
        }
        return fail(`unexpected ${describe(tok)}`);
      case "ident": {
        if (tok.v === "TRUE" || tok.v === "FALSE") {
          p++;
          return { kind: "literal", value: tok.v === "TRUE" };
        }
        if (tok.v === "NULL") {
          p++;
          return { kind: "literal", value: null };
        }
        if (tok.v === "IF") return parseIf();
        if (tok.v === "CASE") return parseCase();
        if (KEYWORDS.has(tok.v)) return fail(`unexpected ${tok.v}`);
        p++;
        if (!isOp("(")) return fail(`expected "(" after ${tok.v}`);
        p++;
        const args: Expr[] = [];
        if (!isOp(")")) {
          for (;;) {
            args.push(parseOr());
            if (isOp(",")) {
              p++;
              continue;
            }
            break;
          }
        }
        expectOp(")");
        return { kind: "call", name: tok.v, args };
      }
      default:
        return fail(`unexpected ${describe(tok)}`);
    }
  };
  const parseIf = (): Expr =>
    nested(() => {
      expectKw("IF");
      const branches: [Expr, Expr][] = [];
      const cond = parseOr();
      expectKw("THEN");
      branches.push([cond, parseOr()]);
      let otherwise: Expr | null = null;
      for (;;) {
        if (isKw("ELSEIF")) {
          p++;
          const c = parseOr();
          expectKw("THEN");
          branches.push([c, parseOr()]);
          continue;
        }
        if (isKw("ELSE")) {
          p++;
          otherwise = parseOr();
        }
        break;
      }
      expectKw("END");
      return { kind: "if", branches, otherwise };
    });
  const parseCase = (): Expr =>
    nested(() => {
      expectKw("CASE");
      const subject = parseOr();
      const whens: [Expr, Expr][] = [];
      while (isKw("WHEN")) {
        p++;
        const v = parseOr();
        expectKw("THEN");
        whens.push([v, parseOr()]);
      }
      if (!whens.length) fail("expected WHEN");
      let otherwise: Expr | null = null;
      if (isKw("ELSE")) {
        p++;
        otherwise = parseOr();
      }
      expectKw("END");
      return { kind: "case", subject, whens, otherwise };
    });

  const tree = parseOr();
  if (peek().t !== "eof") fail(`unexpected ${describe(peek())}`);
  return tree;
}
