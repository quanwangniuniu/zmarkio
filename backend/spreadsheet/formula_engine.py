from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
import logging
from typing import List, Optional, Tuple

from .models import Cell, ComputedCellType, Sheet, SheetColumn, SheetRow, CellValueType

logger = logging.getLogger(__name__)


class FormulaError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class FormulaResult:
    computed_type: str
    computed_number: Optional[Decimal] = None
    computed_string: Optional[str] = None
    error_code: Optional[str] = None


@dataclass
class Token:
    type: str
    value: str


@dataclass
class Value:
    kind: str
    number: Optional[Decimal] = None
    string: Optional[str] = None
    boolean: Optional[bool] = None
    error_code: Optional[str] = None


def _value_number(value: Decimal) -> Value:
    return Value(kind='number', number=value)


def _value_string(value: str) -> Value:
    return Value(kind='string', string=value)


def _value_boolean(value: bool) -> Value:
    return Value(kind='boolean', boolean=value)


def _value_empty() -> Value:
    return Value(kind='empty')


def _value_error(code: str) -> Value:
    return Value(kind='error', error_code=code)


def _extract_currency_symbol(raw_input: str) -> Optional[str]:
    raw = raw_input.strip()
    if not raw:
        return None
    symbols = {'$', '¥', '€', '£'}
    if raw[0] == '-' and len(raw) > 1 and raw[1] in symbols:
        return raw[1]
    if raw[0] in symbols:
        return raw[0]
    return None


def _parse_currency_number(raw_input: Optional[str]) -> Optional[Decimal]:
    if not raw_input:
        return None
    raw = raw_input.strip()
    if not raw:
        return None
    negative = False
    if raw.startswith('-'):
        negative = True
        raw = raw[1:].lstrip()
    symbol = _extract_currency_symbol(raw)
    if not symbol:
        return None
    if raw.startswith(symbol):
        raw = raw[len(symbol):]
    else:
        return None
    normalized = raw.replace(',', '').strip()
    if not normalized:
        return None
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return -value if negative else value


def _active_formula_position_exists(
    sheet: Sheet,
    cache_attribute: str,
    position: int,
    model,
) -> bool:
    """Use a recalculation-scoped row/column cache when one is installed."""
    positions = getattr(sheet, cache_attribute, None)
    if positions is not None:
        return position in positions
    return model.objects.filter(
        sheet=sheet,
        position=position,
        is_deleted=False,
    ).exists()


def _formula_cell_at(sheet: Sheet, row_index: int, column_index: int):
    """Resolve one cell through the recalculation cache, falling back to ORM."""
    cache = getattr(sheet, '_formula_cell_cache', None)
    key = (row_index, column_index)
    if cache is not None and key in cache:
        return cache[key]
    cell = Cell.objects.filter(
        sheet=sheet,
        row__position=row_index,
        column__position=column_index,
        row__is_deleted=False,
        column__is_deleted=False,
        is_deleted=False,
    ).select_related('row', 'column').first()
    if cache is not None:
        cache[key] = cell
    return cell


def _detect_formula_currency_symbol(sheet: Sheet, raw_input: str) -> Optional[str]:
    references = extract_references(raw_input)
    if not references:
        return None
    symbols = set()
    for ref in references:
        try:
            row_index, col_index = reference_to_indexes(ref)
        except FormulaError:
            continue
        cell = _formula_cell_at(sheet, row_index, col_index)
        if cell is None or not cell.raw_input:
            continue
        symbol = _extract_currency_symbol(cell.raw_input)
        if symbol:
            symbols.add(symbol)
            if len(symbols) > 1:
                raise FormulaError("#VALUE!")
    if not symbols:
        return None
    return symbols.pop()


def _format_currency_string(symbol: Optional[str], value: Optional[Decimal]) -> Optional[str]:
    if not symbol or value is None:
        return None
    normalized = format(value.normalize(), 'f')
    if '.' in normalized:
        normalized = normalized.rstrip('0').rstrip('.')
    return f"{symbol}{normalized}"


def evaluate_formula(raw_input: str, sheet: Sheet, udfs: Optional[dict] = None) -> FormulaResult:
    expression = raw_input[1:] if raw_input.startswith('=') else raw_input
    if not expression.strip():
        return FormulaResult(computed_type=ComputedCellType.ERROR, error_code="#REF!")

    try:
        tokens = _tokenize(expression)
        parser = _Parser(tokens, sheet, udfs=udfs)
        result = parser.parse_comparison()
        if parser.has_more_tokens():
            raise FormulaError("#REF!")
        currency_symbol = _detect_formula_currency_symbol(sheet, raw_input)
        if result.kind == 'error':
            return FormulaResult(computed_type=ComputedCellType.ERROR, error_code=result.error_code or "#VALUE!")
        if result.kind == 'number':
            return FormulaResult(
                computed_type=ComputedCellType.NUMBER,
                computed_number=result.number,
                computed_string=_format_currency_string(currency_symbol, result.number)
            )
        if result.kind == 'string':
            return FormulaResult(computed_type=ComputedCellType.STRING, computed_string=result.string)
        if result.kind == 'boolean':
            return FormulaResult(
                computed_type=ComputedCellType.BOOLEAN,
                computed_string='TRUE' if result.boolean else 'FALSE'
            )
        if result.kind == 'empty':
            return FormulaResult(computed_type=ComputedCellType.EMPTY)
        return FormulaResult(computed_type=ComputedCellType.ERROR, error_code="#VALUE!")
    except FormulaError as exc:
        return FormulaResult(computed_type=ComputedCellType.ERROR, error_code=exc.code)
    except Exception:
        return FormulaResult(computed_type=ComputedCellType.ERROR, error_code="#VALUE!")


def _tokenize(expression: str) -> List[Token]:
    tokens: List[Token] = []
    index = 0
    length = len(expression)

    while index < length:
        char = expression[index]
        if char.isspace():
            index += 1
            continue

        if char == '"':
            index += 1
            start = index
            while index < length and expression[index] != '"':
                index += 1
            if index >= length:
                raise FormulaError("#REF!")
            tokens.append(Token('STRING', expression[start:index]))
            index += 1
            continue

        if char in '<>=':
            if char == '<' and index + 1 < length and expression[index + 1] == '>':
                tokens.append(Token('COMPARE', '<>'))
                index += 2
                continue
            if char == '<' and index + 1 < length and expression[index + 1] == '=':
                tokens.append(Token('COMPARE', '<='))
                index += 2
                continue
            if char == '>' and index + 1 < length and expression[index + 1] == '=':
                tokens.append(Token('COMPARE', '>='))
                index += 2
                continue
            tokens.append(Token('COMPARE', char))
            index += 1
            continue

        if char in '+-*/()':
            token_type = 'LPAREN' if char == '(' else 'RPAREN' if char == ')' else 'OP'
            tokens.append(Token(token_type, char))
            index += 1
            continue

        if char == ',':
            tokens.append(Token('COMMA', char))
            index += 1
            continue

        if char == ':':
            tokens.append(Token('COLON', char))
            index += 1
            continue

        if char.isdigit() or char == '.':
            start = index
            index += 1
            while index < length and (expression[index].isdigit() or expression[index] == '.'):
                index += 1
            tokens.append(Token('NUMBER', expression[start:index]))
            continue

        if char.isalpha():
            start = index
            index += 1
            while index < length and expression[index].isalpha():
                index += 1
            letters = expression[start:index]
            if index >= length or not expression[index].isdigit():
                tokens.append(Token('IDENT', letters))
                continue
            digits_start = index
            while index < length and expression[index].isdigit():
                index += 1
            digits = expression[digits_start:index]
            tokens.append(Token('REF', f"{letters}{digits}"))
            continue

        raise FormulaError("#REF!")

    return tokens


class _Parser:
    def __init__(self, tokens: List[Token], sheet: Sheet, udfs: Optional[dict] = None) -> None:
        self.tokens = tokens
        self.sheet = sheet
        self.udfs = udfs or {}
        self.index = 0

    def has_more_tokens(self) -> bool:
        return self.index < len(self.tokens)

    def _current_token(self) -> Optional[Token]:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _peek(self, offset: int = 1) -> Optional[Token]:
        idx = self.index + offset
        if idx >= len(self.tokens):
            return None
        return self.tokens[idx]

    def _consume(self, expected_type: Optional[str] = None) -> Token:
        token = self._current_token()
        if token is None:
            raise FormulaError("#REF!")
        if expected_type and token.type != expected_type:
            raise FormulaError("#REF!")
        self.index += 1
        return token

    def parse_comparison(self, evaluate: bool = True) -> Value:
        left = self._parse_comparison_operand(evaluate)
        token = self._current_token()
        if token is not None and token.type == 'COMPARE':
            op = token.value
            self._consume('COMPARE')
            right = self._parse_comparison_operand(evaluate)
            if not evaluate:
                return _value_empty()
            return _compare_values(left, right, op)
        if not evaluate:
            return _value_empty()
        return left

    def _parse_comparison_operand(self, evaluate: bool = True) -> Value:
        token = self._current_token()
        if token is None:
            raise FormulaError("#REF!")
        if token.type == 'IDENT' and token.value.lower() in ('true', 'false'):
            self._consume('IDENT')
            return _value_boolean(token.value.lower() == 'true') if evaluate else _value_empty()
        if token.type == 'IDENT' and token.value.lower() == 'if':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_if_value() if evaluate else self._parse_if_value_skip()
            return value if evaluate else _value_empty()
        if token.type == 'IDENT' and token.value.lower() == 'and':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_and_value() if evaluate else self._parse_and_value_skip()
            return value if evaluate else _value_empty()
        if token.type == 'IDENT' and token.value.lower() == 'or':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_or_value() if evaluate else self._parse_or_value_skip()
            return value if evaluate else _value_empty()
        if token.type == 'IDENT' and token.value.lower() == 'not':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_not_value() if evaluate else self._parse_not_value_skip()
            return value if evaluate else _value_empty()
        if token.type == 'IDENT' and token.value.lower() == 'vlookup':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_vlookup_value() if evaluate else self._parse_vlookup_value_skip()
            return value if evaluate else _value_empty()
        if token.type == 'STRING':
            self._consume('STRING')
            return _value_string(token.value) if evaluate else _value_empty()
        if token.type == 'REF':
            next_token = self._peek()
            if next_token is not None and next_token.type == 'COLON':
                raise FormulaError("#VALUE!")
            if next_token is not None and next_token.type == 'OP' and next_token.value in '+-*/':
                value = self.parse_expression(evaluate)
                return _value_number(value) if evaluate else _value_empty()
            ref_value = token.value
            self._consume('REF')
            if not evaluate:
                return _value_empty()
            return _resolve_reference_value(self.sheet, ref_value)
        value = self.parse_expression(evaluate)
        return _value_number(value) if evaluate else _value_empty()

    def parse_expression(self, evaluate: bool = True) -> Decimal:
        if not evaluate:
            self._consume_expression()
            return Decimal(0)
        value = self.parse_term()
        while True:
            token = self._current_token()
            if token is None or token.type != 'OP' or token.value not in '+-':
                break
            operator = token.value
            self._consume('OP')
            right = self.parse_term()
            value = value + right if operator == '+' else value - right
        return value

    def parse_term(self) -> Decimal:
        value = self.parse_factor()
        while True:
            token = self._current_token()
            if token is None or token.type != 'OP' or token.value not in '*/':
                break
            operator = token.value
            self._consume('OP')
            right = self.parse_factor()
            if operator == '*':
                value = value * right
            else:
                if right == 0:
                    raise FormulaError("#DIV/0!")
                value = value / right
        return value

    def _parse_udf_arguments(self, udf: dict) -> Decimal:
        func_name = udf["name"]
        if func_name in udf["expression"].upper():
            raise FormulaError("#REF!")

        args = []
        if self._current_token() and self._current_token().type != "RPAREN":
            while True:
                args.append(self.parse_expression())
                token = self._current_token()
                if token is None:
                    raise FormulaError("#VALUE!")
                if token.type == "COMMA":
                    self._consume("COMMA")
                    continue
                if token.type == "RPAREN":
                    self._consume("RPAREN")
                    break
                raise FormulaError("#VALUE!")
        else:
            self._consume("RPAREN")

        params = udf["params"]
        if len(args) != len(params):
            raise FormulaError("#VALUE!")

        expr = udf["expression"]
        for param, val in zip(params, args):
            expr = expr.replace(param, str(val))

        result = evaluate_formula("=" + expr, self.sheet, udfs=self.udfs)
        if result.computed_type == ComputedCellType.ERROR:
            raise FormulaError(result.error_code or "#VALUE!")
        if result.computed_number is not None:
            return result.computed_number
        raise FormulaError("#VALUE!")

    def parse_factor(self) -> Decimal:
        token = self._current_token()
        if token is None:
            raise FormulaError("#REF!")

        if token.type == 'IDENT' and token.value.lower() == 'sum':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_sum_arguments()

        if token.type == 'IDENT' and token.value.lower() == 'average':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_average_arguments()

        if token.type == 'IDENT' and token.value.lower() == 'count':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_count_arguments()

        if token.type == 'IDENT' and token.value.lower() == 'and':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_and_value()
            return _coerce_value_to_number(value)

        if token.type == 'IDENT' and token.value.lower() == 'or':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_or_value()
            return _coerce_value_to_number(value)

        if token.type == 'IDENT' and token.value.lower() == 'not':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_not_value()
            return _coerce_value_to_number(value)

        if token.type == 'IDENT' and token.value.lower() == 'if':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_if_value()
            return _coerce_value_to_number(value)

        if token.type == 'IDENT' and token.value.lower() == 'vlookup':
            self._consume('IDENT')
            self._consume('LPAREN')
            value = self._parse_vlookup_value()
            return _coerce_value_to_number(value)

        if token.type == 'IDENT' and token.value.lower() == 'abs':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_abs_arguments()

        if token.type == 'IDENT' and token.value.lower() == 'round':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_round_arguments()

        if token.type == 'IDENT' and token.value.lower() == 'floor':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_floor_ceiling_arguments('floor')

        if token.type == 'IDENT' and token.value.lower() == 'ceiling':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_floor_ceiling_arguments('ceiling')

        if token.type == 'IDENT' and token.value.lower() == 'min':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_min_max_arguments('min')

        if token.type == 'IDENT' and token.value.lower() == 'max':
            self._consume('IDENT')
            self._consume('LPAREN')
            return self._parse_min_max_arguments('max')

        if token.type == 'OP' and token.value in '+-':
            operator = token.value
            self._consume('OP')
            value = self.parse_factor()
            return value if operator == '+' else -value

        if token.type == 'NUMBER':
            self._consume('NUMBER')
            try:
                return Decimal(token.value)
            except InvalidOperation as exc:
                raise FormulaError("#REF!") from exc

        if token.type == 'REF':
            self._consume('REF')
            return _resolve_reference(self.sheet, token.value)

        if token.type == 'LPAREN':
            self._consume('LPAREN')
            value = self.parse_expression()
            if self._current_token() is None or self._current_token().type != 'RPAREN':
                raise FormulaError("#REF!")
            self._consume('RPAREN')
            return value

        if token.type == "IDENT":
            func_name = token.value.upper()
            udf = (self.udfs or {}).get(func_name)
            if udf is None:
                raise FormulaError("#REF!")
            self._consume("IDENT")
            self._consume("LPAREN")
            return self._parse_udf_arguments(udf)
            
        raise FormulaError("#REF!")

    def _consume_expression(self) -> None:
        self._consume_term()
        while True:
            token = self._current_token()
            if token is None or token.type != 'OP' or token.value not in '+-':
                break
            self._consume('OP')
            self._consume_term()

    def _consume_term(self) -> None:
        self._consume_factor()
        while True:
            token = self._current_token()
            if token is None or token.type != 'OP' or token.value not in '*/':
                break
            self._consume('OP')
            self._consume_factor()

    def _consume_factor(self) -> None:
        token = self._current_token()
        if token is None:
            raise FormulaError("#REF!")
        if token.type == 'OP' and token.value in '+-':
            self._consume('OP')
            self._consume_factor()
            return
        if token.type in ('NUMBER', 'REF', 'STRING'):
            self._consume(token.type)
            return
        if token.type == 'IDENT':
            self._consume('IDENT')
            if self._current_token() is None or self._current_token().type != 'LPAREN':
                raise FormulaError("#REF!")
            self._consume('LPAREN')
            self._consume_function_arguments()
            if self._current_token() is None or self._current_token().type != 'RPAREN':
                raise FormulaError("#REF!")
            self._consume('RPAREN')
            return
        if token.type == 'LPAREN':
            self._consume('LPAREN')
            self._consume_expression()
            if self._current_token() is None or self._current_token().type != 'RPAREN':
                raise FormulaError("#REF!")
            self._consume('RPAREN')
            return
        raise FormulaError("#REF!")

    def _consume_function_arguments(self) -> None:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            return
        while True:
            token = self._current_token()
            if token is None:
                raise FormulaError("#REF!")
            if token.type == 'REF' and self._peek() is not None and self._peek().type == 'COLON':
                self._consume('REF')
                self._consume('COLON')
                self._consume('REF')
            else:
                self.parse_comparison(evaluate=False)
            token = self._current_token()
            if token is None:
                raise FormulaError("#REF!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                continue
            break

    def _parse_sum_arguments(self) -> Decimal:
        if self._current_token() is None:
            raise FormulaError("#VALUE!")
        if self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        total = Decimal(0)
        while True:
            total += self._parse_function_argument_value()[0]
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                if self._current_token() is None or self._current_token().type == 'RPAREN':
                    raise FormulaError("#VALUE!")
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        return total

    def _parse_average_arguments(self) -> Decimal:
        if self._current_token() is None:
            raise FormulaError("#VALUE!")
        if self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        total = Decimal(0)
        count = 0
        while True:
            arg_sum, arg_count = self._parse_function_argument_value()
            total += arg_sum
            count += arg_count
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                if self._current_token() is None or self._current_token().type == 'RPAREN':
                    raise FormulaError("#VALUE!")
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        if count == 0:
            return Decimal(0)
        return total / Decimal(count)

    def _parse_count_arguments(self) -> Decimal:
        if self._current_token() is None:
            raise FormulaError("#VALUE!")
        if self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        total_count = 0
        while True:
            total_count += self._parse_count_argument_value()
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                if self._current_token() is None or self._current_token().type == 'RPAREN':
                    raise FormulaError("#VALUE!")
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        return Decimal(total_count)

    def _parse_if_value(self) -> Value:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        condition = self.parse_comparison()
        if self._current_token() is None or self._current_token().type != 'COMMA':
            raise FormulaError("#VALUE!")
        self._consume('COMMA')
        condition_truthy = _value_truthy(condition)
        if condition_truthy:
            true_value = self.parse_comparison()
            if self._current_token() is None or self._current_token().type != 'COMMA':
                raise FormulaError("#VALUE!")
            self._consume('COMMA')
            self.parse_comparison(evaluate=False)
            if self._current_token() is None or self._current_token().type != 'RPAREN':
                raise FormulaError("#VALUE!")
            self._consume('RPAREN')
            return true_value
        self.parse_comparison(evaluate=False)
        if self._current_token() is None or self._current_token().type != 'COMMA':
            raise FormulaError("#VALUE!")
        self._consume('COMMA')
        false_value = self.parse_comparison()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return false_value

    def _parse_if_value_skip(self) -> Value:
        self._consume_function_arguments()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_empty()

    def _parse_and_value(self) -> Value:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        first = True
        while True:
            value = self.parse_comparison()
            first = False
            if not _value_truthy(value):
                while self._current_token() is not None and self._current_token().type == 'COMMA':
                    self._consume('COMMA')
                    self.parse_comparison(evaluate=False)
                if self._current_token() is None or self._current_token().type != 'RPAREN':
                    raise FormulaError("#VALUE!")
                self._consume('RPAREN')
                return _value_boolean(False)
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        if first:
            raise FormulaError("#VALUE!")
        return _value_boolean(True)

    def _parse_or_value(self) -> Value:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        first = True
        while True:
            value = self.parse_comparison()
            first = False
            if _value_truthy(value):
                while self._current_token() is not None and self._current_token().type == 'COMMA':
                    self._consume('COMMA')
                    self.parse_comparison(evaluate=False)
                if self._current_token() is None or self._current_token().type != 'RPAREN':
                    raise FormulaError("#VALUE!")
                self._consume('RPAREN')
                return _value_boolean(True)
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        if first:
            raise FormulaError("#VALUE!")
        return _value_boolean(False)

    def _parse_not_value(self) -> Value:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        value = self.parse_comparison()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_boolean(not _value_truthy(value))

    def _parse_and_value_skip(self) -> Value:
        self._consume_function_arguments()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_empty()

    def _parse_or_value_skip(self) -> Value:
        self._consume_function_arguments()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_empty()

    def _parse_not_value_skip(self) -> Value:
        self._consume_function_arguments()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_empty()

    def _parse_vlookup_value(self) -> Value:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        search_key = self.parse_comparison()
        if self._current_token() is None or self._current_token().type != 'COMMA':
            raise FormulaError("#VALUE!")
        self._consume('COMMA')
        range_start_token = self._current_token()
        if range_start_token is None or range_start_token.type != 'REF':
            raise FormulaError("#VALUE!")
        start_ref = self._consume('REF').value
        if self._current_token() is None or self._current_token().type != 'COLON':
            raise FormulaError("#VALUE!")
        self._consume('COLON')
        if self._current_token() is None or self._current_token().type != 'REF':
            raise FormulaError("#VALUE!")
        end_ref = self._consume('REF').value
        if self._current_token() is None or self._current_token().type != 'COMMA':
            raise FormulaError("#VALUE!")
        self._consume('COMMA')
        index_value = self._parse_numeric_argument()
        logger.info("VLOOKUP index raw value=%s", index_value)
        try:
            if index_value != index_value.to_integral_value():
                return _value_error("#VALUE!")
            index_int = int(index_value)
        except (InvalidOperation, ValueError):
            return _value_error("#VALUE!")
        logger.info("VLOOKUP index_int=%s", index_int)
        is_sorted = False
        token = self._current_token()
        if token is not None and token.type == 'COMMA':
            self._consume('COMMA')
            is_sorted = _value_truthy(self.parse_comparison())
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        if is_sorted:
            return _value_error("#VALUE!")
        return _vlookup_value(self.sheet, search_key, start_ref, end_ref, index_int)

    def _parse_vlookup_value_skip(self) -> Value:
        self._consume_function_arguments()
        if self._current_token() is None or self._current_token().type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return _value_empty()

    def _parse_abs_arguments(self) -> Decimal:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        value = self._parse_numeric_argument()
        token = self._current_token()
        if token is None or token.type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        return abs(value)

    def _parse_round_arguments(self) -> Decimal:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        value = self._parse_numeric_argument()
        digits = Decimal(0)
        token = self._current_token()
        if token is not None and token.type == 'COMMA':
            self._consume('COMMA')
            digits = self._parse_numeric_argument()
        token = self._current_token()
        if token is None or token.type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        try:
            if digits != digits.to_integral_value():
                raise FormulaError("#VALUE!")
            digits_int = int(digits)
            quantizer = Decimal('1').scaleb(-digits_int)
            return value.quantize(quantizer, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            raise FormulaError("#VALUE!")

    def _parse_floor_ceiling_arguments(self, mode: str) -> Decimal:
        if self._current_token() is None or self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        value = self._parse_numeric_argument()
        significance = Decimal(1)
        token = self._current_token()
        if token is not None and token.type == 'COMMA':
            self._consume('COMMA')
            significance = self._parse_numeric_argument()
        token = self._current_token()
        if token is None or token.type != 'RPAREN':
            raise FormulaError("#VALUE!")
        self._consume('RPAREN')
        if significance == 0:
            raise FormulaError("#VALUE!")
        try:
            rounding = ROUND_FLOOR if mode == 'floor' else ROUND_CEILING
            quotient = (value / significance).to_integral_value(rounding=rounding)
            return quotient * significance
        except (InvalidOperation, ZeroDivisionError):
            raise FormulaError("#VALUE!")

    def _parse_min_max_arguments(self, mode: str) -> Decimal:
        if self._current_token() is None:
            raise FormulaError("#VALUE!")
        if self._current_token().type == 'RPAREN':
            raise FormulaError("#VALUE!")
        min_value: Optional[Decimal] = None
        max_value: Optional[Decimal] = None
        while True:
            arg_min, arg_max = self._parse_min_max_argument_value()
            if min_value is None or arg_min < min_value:
                min_value = arg_min
            if max_value is None or arg_max > max_value:
                max_value = arg_max
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
            if token.type == 'COMMA':
                self._consume('COMMA')
                if self._current_token() is None or self._current_token().type == 'RPAREN':
                    raise FormulaError("#VALUE!")
                continue
            if token.type == 'RPAREN':
                self._consume('RPAREN')
                break
            raise FormulaError("#VALUE!")
        if min_value is None or max_value is None:
            raise FormulaError("#VALUE!")
        return min_value if mode == 'min' else max_value

    def _parse_function_argument_value(self) -> Tuple[Decimal, int]:
        token = self._current_token()
        if token is None or token.type != 'REF':
            raise FormulaError("#VALUE!")
        start_ref = self._consume('REF').value
        token = self._current_token()
        if token is not None and token.type == 'COLON':
            self._consume('COLON')
            end_ref = self._consume('REF').value
            return _sum_and_count_range(self.sheet, start_ref, end_ref)
        return _resolve_reference(self.sheet, start_ref), 1

    def _parse_count_argument_value(self) -> int:
        token = self._current_token()
        if token is None:
            raise FormulaError("#VALUE!")
        if token.type == 'NUMBER':
            self._consume('NUMBER')
            return 1
        if token.type == 'REF':
            start_ref = self._consume('REF').value
            token = self._current_token()
            if token is not None and token.type == 'COLON':
                self._consume('COLON')
                end_ref = self._consume('REF').value
                return _count_range(self.sheet, start_ref, end_ref)
            return _count_single_ref(self.sheet, start_ref)
        raise FormulaError("#VALUE!")

    def _parse_numeric_argument(self) -> Decimal:
        token = self._current_token()
        if token is None:
            raise FormulaError("#VALUE!")
        sign = Decimal(1)
        if token.type == 'OP' and token.value in '+-':
            sign = Decimal(-1) if token.value == '-' else Decimal(1)
            self._consume('OP')
            token = self._current_token()
            if token is None:
                raise FormulaError("#VALUE!")
        if token.type == 'NUMBER':
            self._consume('NUMBER')
            try:
                return sign * Decimal(token.value)
            except InvalidOperation:
                raise FormulaError("#VALUE!")
        if token.type == 'REF':
            ref = self._consume('REF').value
            return sign * _resolve_reference(self.sheet, ref)
        raise FormulaError("#VALUE!")
    def _parse_min_max_argument_value(self) -> Tuple[Decimal, Decimal]:
        token = self._current_token()
        if token is None or token.type != 'REF':
            raise FormulaError("#VALUE!")
        start_ref = self._consume('REF').value
        token = self._current_token()
        if token is not None and token.type == 'COLON':
            self._consume('COLON')
            end_ref = self._consume('REF').value
            return _min_max_range(self.sheet, start_ref, end_ref)
        value = _resolve_reference(self.sheet, start_ref)
        return value, value


def _resolve_reference(sheet: Sheet, ref: str) -> Decimal:
    column_label, row_number = _split_reference(ref)
    column_index = _column_label_to_index(column_label)
    row_index = row_number - 1

    if row_index < 0 or column_index < 0:
        raise FormulaError("#REF!")

    if not _active_formula_position_exists(
        sheet,
        '_formula_active_row_positions',
        row_index,
        SheetRow,
    ):
        raise FormulaError("#REF!")

    if not _active_formula_position_exists(
        sheet,
        '_formula_active_column_positions',
        column_index,
        SheetColumn,
    ):
        raise FormulaError("#REF!")

    cell = _formula_cell_at(sheet, row_index, column_index)

    if cell is None:
        return Decimal(0)

    if cell.computed_type == ComputedCellType.NUMBER and cell.computed_number is not None:
        return cell.computed_number

    if cell.number_value is not None:
        return cell.number_value

    if cell.value_type == CellValueType.EMPTY or cell.computed_type == ComputedCellType.EMPTY:
        return Decimal(0)

    if (
        cell.value_type == CellValueType.FORMULA
        and cell.computed_type == ComputedCellType.EMPTY
        and cell.computed_number is None
    ):
        return Decimal(0)

    currency_number = (
        _parse_currency_number(cell.raw_input)
        or _parse_currency_number(cell.computed_string)
        or _parse_currency_number(cell.string_value)
    )
    if currency_number is not None:
        return currency_number

    raise FormulaError("#VALUE!")


def _resolve_reference_value(sheet: Sheet, ref: str) -> Value:
    column_label, row_number = _split_reference(ref)
    column_index = _column_label_to_index(column_label)
    row_index = row_number - 1

    if row_index < 0 or column_index < 0:
        return _value_error("#REF!")

    if not _active_formula_position_exists(
        sheet,
        '_formula_active_row_positions',
        row_index,
        SheetRow,
    ):
        return _value_error("#REF!")

    if not _active_formula_position_exists(
        sheet,
        '_formula_active_column_positions',
        column_index,
        SheetColumn,
    ):
        return _value_error("#REF!")

    cell = _formula_cell_at(sheet, row_index, column_index)

    if cell is None:
        return _value_empty()

    if cell.computed_type == ComputedCellType.ERROR:
        return _value_error(cell.error_code or "#VALUE!")

    if cell.value_type == CellValueType.BOOLEAN:
        return _value_boolean(bool(cell.boolean_value))

    if cell.computed_type == ComputedCellType.BOOLEAN:
        return _value_boolean((cell.computed_string or '').upper() == 'TRUE')

    if cell.computed_type == ComputedCellType.NUMBER and cell.computed_number is not None:
        return _value_number(cell.computed_number)

    if cell.number_value is not None:
        return _value_number(cell.number_value)

    if cell.computed_type == ComputedCellType.STRING:
        return _value_string(cell.computed_string or '')

    if cell.value_type == CellValueType.STRING:
        return _value_string(cell.string_value or '')

    if cell.value_type == CellValueType.EMPTY or cell.computed_type == ComputedCellType.EMPTY:
        return _value_empty()

    return _value_error("#VALUE!")


def _coerce_value_to_number(value: Value) -> Decimal:
    if value.kind == 'error':
        raise FormulaError(value.error_code or "#VALUE!")
    if value.kind == 'number':
        return value.number or Decimal(0)
    if value.kind == 'empty':
        return Decimal(0)
    raise FormulaError("#VALUE!")


def _value_truthy(value: Value) -> bool:
    if value.kind == 'error':
        raise FormulaError(value.error_code or "#VALUE!")
    if value.kind == 'boolean':
        return bool(value.boolean)
    if value.kind == 'number':
        return value.number is not None and value.number != 0
    if value.kind == 'empty':
        return False
    if value.kind == 'string':
        return bool(value.string)
    raise FormulaError("#VALUE!")


def _compare_values(left: Value, right: Value, op: str) -> Value:
    if left.kind == 'error':
        return left
    if right.kind == 'error':
        return right
    if left.kind == 'empty' and right.kind == 'empty':
        if op == '=':
            return _value_boolean(True)
        if op == '<>':
            return _value_boolean(False)
        return _value_error("#VALUE!")
    if left.kind == 'empty' or right.kind == 'empty':
        return _value_error("#VALUE!")
    if left.kind == 'number' and right.kind == 'number':
        return _value_boolean(_compare_numbers(left.number or Decimal(0), right.number or Decimal(0), op))
    if left.kind == 'string' and right.kind == 'string':
        if op == '=':
            return _value_boolean(left.string == right.string)
        if op == '<>':
            return _value_boolean(left.string != right.string)
        return _value_error("#VALUE!")
    if left.kind == 'boolean' and right.kind == 'boolean':
        if op == '=':
            return _value_boolean(left.boolean == right.boolean)
        if op == '<>':
            return _value_boolean(left.boolean != right.boolean)
        return _value_error("#VALUE!")
    return _value_error("#VALUE!")


def _compare_numbers(left: Decimal, right: Decimal, op: str) -> bool:
    if op == '=':
        return left == right
    if op == '<>':
        return left != right
    if op == '<':
        return left < right
    if op == '<=':
        return left <= right
    if op == '>':
        return left > right
    if op == '>=':
        return left >= right
    return False


def _value_from_cell_record(cell: dict) -> Value:
    computed_type = cell.get('computed_type')
    if computed_type == ComputedCellType.ERROR:
        return _value_error(cell.get('error_code') or "#VALUE!")
    if computed_type == ComputedCellType.BOOLEAN:
        return _value_boolean((cell.get('computed_string') or '').upper() == 'TRUE')
    if computed_type == ComputedCellType.NUMBER and cell.get('computed_number') is not None:
        return _value_number(cell['computed_number'])
    if cell.get('number_value') is not None:
        return _value_number(cell['number_value'])
    if computed_type == ComputedCellType.STRING:
        return _value_string(cell.get('computed_string') or '')
    value_type = cell.get('value_type')
    if value_type == CellValueType.STRING:
        return _value_string(cell.get('string_value') or '')
    if value_type == CellValueType.BOOLEAN:
        return _value_boolean(bool(cell.get('boolean_value')))
    if value_type == CellValueType.EMPTY or computed_type == ComputedCellType.EMPTY:
        return _value_empty()
    return _value_error("#VALUE!")


def _values_for_range(sheet: Sheet, row_start: int, row_end: int, col_start: int, col_end: int) -> dict:
    cells = Cell.objects.filter(
        sheet=sheet,
        row__position__gte=row_start,
        row__position__lte=row_end,
        column__position__gte=col_start,
        column__position__lte=col_end,
        row__is_deleted=False,
        column__is_deleted=False,
        is_deleted=False
    ).values(
        'row__position',
        'column__position',
        'value_type',
        'string_value',
        'number_value',
        'boolean_value',
        'computed_type',
        'computed_number',
        'computed_string',
        'error_code'
    )
    return {(cell['row__position'], cell['column__position']): cell for cell in cells}


def _values_match(search_key: Value, candidate: Value) -> bool:
    if search_key.kind == 'empty':
        return candidate.kind == 'empty'
    if search_key.kind == 'number' and candidate.kind == 'number':
        return (search_key.number or Decimal(0)) == (candidate.number or Decimal(0))
    if search_key.kind == 'string' and candidate.kind == 'string':
        return (search_key.string or '') == (candidate.string or '')
    if search_key.kind == 'boolean' and candidate.kind == 'boolean':
        return bool(search_key.boolean) == bool(candidate.boolean)
    return False


def _vlookup_value(
    sheet: Sheet,
    search_key: Value,
    start_ref: str,
    end_ref: str,
    index: int
) -> Value:
    if search_key.kind == 'error':
        return search_key
    start_row, start_col = reference_to_indexes(start_ref)
    end_row, end_col = reference_to_indexes(end_ref)
    row_start = min(start_row, end_row)
    row_end = max(start_row, end_row)
    col_start = min(start_col, end_col)
    col_end = max(start_col, end_col)
    column_count = col_end - col_start + 1
    logger.info("VLOOKUP range start_col=%s end_col=%s num_cols=%s", col_start, col_end, column_count)
    logger.info("VLOOKUP range rows=%s cols=%s", row_end - row_start + 1, column_count)
    if index < 1 or index > column_count:
        return _value_error("#REF!")

    values = _values_for_range(sheet, row_start, row_end, col_start, col_end)
    for row in range(row_start, row_end + 1):
        key_cell = values.get((row, col_start))
        candidate = _value_empty() if key_cell is None else _value_from_cell_record(key_cell)
        if candidate.kind == 'error':
            continue
        if _values_match(search_key, candidate):
            target_col = col_start + index - 1
            target_cell = values.get((row, target_col))
            return _value_empty() if target_cell is None else _value_from_cell_record(target_cell)
    return _value_error("#N/A")


def _coerce_numeric_value(
    value_type: str,
    number_value: Optional[Decimal],
    computed_type: str,
    computed_number: Optional[Decimal],
    raw_input: Optional[str] = None,
    string_value: Optional[str] = None,
    computed_string: Optional[str] = None
) -> Decimal:
    if computed_type == ComputedCellType.NUMBER and computed_number is not None:
        return computed_number
    if number_value is not None:
        return number_value
    currency_number = (
        _parse_currency_number(raw_input)
        or _parse_currency_number(computed_string)
        or _parse_currency_number(string_value)
    )
    if currency_number is not None:
        return currency_number
    if value_type == CellValueType.EMPTY or computed_type == ComputedCellType.EMPTY:
        return Decimal(0)
    if value_type == CellValueType.FORMULA and computed_type == ComputedCellType.EMPTY and computed_number is None:
        return Decimal(0)
    raise FormulaError("#VALUE!")


def _sum_range(sheet: Sheet, start_ref: str, end_ref: str) -> Decimal:
    total, _count = _sum_and_count_range(sheet, start_ref, end_ref)
    return total


def _sum_and_count_range(sheet: Sheet, start_ref: str, end_ref: str) -> Tuple[Decimal, int]:
    start_row, start_col = reference_to_indexes(start_ref)
    end_row, end_col = reference_to_indexes(end_ref)
    row_start = min(start_row, end_row)
    row_end = max(start_row, end_row)
    col_start = min(start_col, end_col)
    col_end = max(start_col, end_col)

    total = Decimal(0)
    cells = Cell.objects.filter(
        sheet=sheet,
        row__position__gte=row_start,
        row__position__lte=row_end,
        column__position__gte=col_start,
        column__position__lte=col_end,
        row__is_deleted=False,
        column__is_deleted=False,
        is_deleted=False
    ).values('value_type', 'number_value', 'computed_type', 'computed_number', 'raw_input', 'string_value', 'computed_string')

    for cell in cells:
        total += _coerce_numeric_value(
            cell['value_type'],
            cell['number_value'],
            cell['computed_type'],
            cell['computed_number'],
            cell['raw_input'],
            cell['string_value'],
            cell['computed_string']
        )

    count = (row_end - row_start + 1) * (col_end - col_start + 1)
    return total, count


def _min_max_range(sheet: Sheet, start_ref: str, end_ref: str) -> Tuple[Decimal, Decimal]:
    start_row, start_col = reference_to_indexes(start_ref)
    end_row, end_col = reference_to_indexes(end_ref)
    row_start = min(start_row, end_row)
    row_end = max(start_row, end_row)
    col_start = min(start_col, end_col)
    col_end = max(start_col, end_col)

    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    seen_cells = 0

    cells = Cell.objects.filter(
        sheet=sheet,
        row__position__gte=row_start,
        row__position__lte=row_end,
        column__position__gte=col_start,
        column__position__lte=col_end,
        row__is_deleted=False,
        column__is_deleted=False,
        is_deleted=False
    ).values('value_type', 'number_value', 'computed_type', 'computed_number', 'raw_input', 'string_value', 'computed_string')

    for cell in cells:
        value = _coerce_numeric_value(
            cell['value_type'],
            cell['number_value'],
            cell['computed_type'],
            cell['computed_number'],
            cell['raw_input'],
            cell['string_value'],
            cell['computed_string']
        )
        seen_cells += 1
        if min_value is None or value < min_value:
            min_value = value
        if max_value is None or value > max_value:
            max_value = value

    total_cells = (row_end - row_start + 1) * (col_end - col_start + 1)
    missing_cells = total_cells - seen_cells
    if missing_cells > 0:
        zero = Decimal(0)
        if min_value is None:
            min_value = zero
            max_value = zero
        else:
            if zero < min_value:
                min_value = zero
            if zero > max_value:
                max_value = zero

    if min_value is None or max_value is None:
        zero = Decimal(0)
        return zero, zero

    return min_value, max_value


def _count_single_ref(sheet: Sheet, ref: str) -> int:
    row_index, col_index = reference_to_indexes(ref)
    if row_index < 0 or col_index < 0:
        raise FormulaError("#REF!")
    cell = _formula_cell_at(sheet, row_index, col_index)
    if cell is None:
        return 0
    return 1 if cell.computed_type == ComputedCellType.NUMBER else 0


def _count_range(sheet: Sheet, start_ref: str, end_ref: str) -> int:
    start_row, start_col = reference_to_indexes(start_ref)
    end_row, end_col = reference_to_indexes(end_ref)
    row_start = min(start_row, end_row)
    row_end = max(start_row, end_row)
    col_start = min(start_col, end_col)
    col_end = max(start_col, end_col)
    return Cell.objects.filter(
        sheet=sheet,
        row__position__gte=row_start,
        row__position__lte=row_end,
        column__position__gte=col_start,
        column__position__lte=col_end,
        row__is_deleted=False,
        column__is_deleted=False,
        computed_type=ComputedCellType.NUMBER,
        is_deleted=False
    ).count()


def _split_reference(ref: str) -> Tuple[str, int]:
    letters = []
    digits = []
    for char in ref:
        if char.isalpha() and not digits:
            letters.append(char)
        elif char.isdigit():
            digits.append(char)
        else:
            raise FormulaError("#REF!")
    if not letters or not digits:
        raise FormulaError("#REF!")
    return ''.join(letters), int(''.join(digits))


def _column_label_to_index(label: str) -> int:
    result = 0
    for char in label.upper():
        if not char.isalpha():
            raise FormulaError("#REF!")
        result = result * 26 + (ord(char) - ord('A') + 1)
    return result - 1


def _column_index_to_label(index: int) -> str:
    if index < 0:
        raise FormulaError("#REF!")
    result = ''
    remaining = index
    while remaining >= 0:
        result = chr(65 + (remaining % 26)) + result
        remaining = (remaining // 26) - 1
    return result


def extract_references(raw_input: str) -> List[str]:
    expression = raw_input[1:] if raw_input.startswith('=') else raw_input
    try:
        tokens = _tokenize(expression)
    except FormulaError:
        return []
    references: List[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == 'REF':
            if (
                index + 2 < len(tokens)
                and tokens[index + 1].type == 'COLON'
                and tokens[index + 2].type == 'REF'
            ):
                references.extend(_expand_range_refs(token.value, tokens[index + 2].value))
                index += 3
                continue
            references.append(token.value)
        index += 1
    return references


def reference_to_indexes(ref: str) -> Tuple[int, int]:
    column_label, row_number = _split_reference(ref)
    column_index = _column_label_to_index(column_label)
    row_index = row_number - 1
    if row_index < 0 or column_index < 0:
        raise FormulaError("#REF!")
    return row_index, column_index


def _expand_range_refs(start_ref: str, end_ref: str) -> List[str]:
    start_row, start_col = reference_to_indexes(start_ref)
    end_row, end_col = reference_to_indexes(end_ref)
    row_start = min(start_row, end_row)
    row_end = max(start_row, end_row)
    col_start = min(start_col, end_col)
    col_end = max(start_col, end_col)
    refs = []
    for row in range(row_start, row_end + 1):
        for col in range(col_start, col_end + 1):
            refs.append(f"{_column_index_to_label(col)}{row + 1}")
    return refs
