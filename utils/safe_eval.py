"""
安全数学表达式求值器
使用 AST 白名单机制替代危险的 eval()
"""

import ast
import math
from functools import lru_cache
from typing import Any

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")


class SafeMathEvaluator(ast.NodeVisitor):
    """
    安全的数学表达式求值器

    使用 AST 白名单机制，只允许安全的数学操作：
    - 二元运算: +, -, *, /, //, %, **
    - 一元运算: +x, -x
    - 函数: sqrt, sin, cos, tan, exp, log, log10, abs, ceil, floor
    - 常量: pi, e
    """

    # 白名单：允许的函数
    ALLOWED_FUNCTIONS = {
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "exp": math.exp,
        "log": math.log,
        "log10": math.log10,
        "abs": abs,
        "ceil": math.ceil,
        "floor": math.floor,
    }

    # 白名单：允许的常量
    ALLOWED_CONSTANTS = {
        "pi": math.pi,
        "e": math.e,
    }

    # 白名单：允许的二元运算符
    ALLOWED_BINARY_OPS = {
        ast.Add: lambda x, y: x + y,
        ast.Sub: lambda x, y: x - y,
        ast.Mult: lambda x, y: x * y,
        ast.Div: lambda x, y: x / y,
        ast.FloorDiv: lambda x, y: x // y,
        ast.Mod: lambda x, y: x % y,
        ast.Pow: lambda x, y: x**y,
    }

    # 白名单：允许的一元运算符
    ALLOWED_UNARY_OPS = {
        ast.UAdd: lambda x: +x,
        ast.USub: lambda x: -x,
    }

    def __init__(self, *, max_nodes: int = 100, max_depth: int = 20):
        self._max_nodes = max_nodes
        self._max_depth = max_depth
        self._node_count = 0
        self._depth = 0

    def visit(self, node: ast.AST) -> Any:
        self._node_count += 1
        if self._node_count > self._max_nodes:
            raise ValueError("Expression is too complex")
        self._depth += 1
        try:
            if self._depth > self._max_depth:
                raise ValueError("Expression nesting is too deep")
            return super().visit(node)
        finally:
            self._depth -= 1

    def visit_Expression(self, node: ast.Expression) -> float:
        """访问表达式根节点"""
        logger.debug("[SafeEval] [ENTRY] visit_Expression")
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> float:
        """访问常量节点（数字）"""
        if isinstance(node.value, (int, float)):
            # 超大 int（如 999**999）转 float 会抛 OverflowError，先检查位数
            if isinstance(node.value, int) and node.value.bit_length() > 256:
                raise ValueError("Numeric constant too large")
            logger.debug(f"[SafeEval] [PROCESS] Constant value={node.value}")
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_Num(self, node: ast.Num) -> float:
        """访问数字节点（Python 3.7 兼容）"""
        logger.debug(f"[SafeEval] [PROCESS] Num value={node.n}")
        return float(node.n)

    def visit_Name(self, node: ast.Name) -> float:
        """访问变量名节点（常量）"""
        name = node.id
        if name in self.ALLOWED_CONSTANTS:
            value = self.ALLOWED_CONSTANTS[name]
            logger.debug(f"[SafeEval] [PROCESS] Name constant={name} value={value}")
            return value
        raise ValueError(f"Unsupported variable: {name}")

    def visit_BinOp(self, node: ast.BinOp) -> float:
        """访问二元运算节点"""
        op_type = type(node.op)
        if op_type not in self.ALLOWED_BINARY_OPS:
            raise ValueError(f"Unsupported binary operator: {op_type.__name__}")

        if isinstance(node.op, ast.Pow):
            right_val = self.visit(node.right)
            if right_val > 1000:
                raise ValueError(f"Power exponent too large: {right_val} > 1000")
            left_val = self.visit(node.left)
            if abs(left_val) > 1000 and right_val > 10:
                raise ValueError(f"Power operation too large: {left_val}**{right_val}")
            result = left_val**right_val
            if not math.isfinite(result) or abs(result) > 1e15:
                raise ValueError("Power result too large")
            logger.debug(
                f"[SafeEval] [PROCESS] BinOp op=Pow left={left_val} right={right_val} result={result}"
            )
            return result

        left = self.visit(node.left)
        right = self.visit(node.right)
        op_func = self.ALLOWED_BINARY_OPS[op_type]
        result = op_func(left, right)
        logger.debug(
            f"[SafeEval] [PROCESS] BinOp op={op_type.__name__} left={left} right={right} result={result}"
        )
        return result

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        """访问一元运算节点"""
        op_type = type(node.op)
        if op_type not in self.ALLOWED_UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")

        operand = self.visit(node.operand)
        op_func = self.ALLOWED_UNARY_OPS[op_type]
        result = op_func(operand)
        logger.debug(
            f"[SafeEval] [PROCESS] UnaryOp op={op_type.__name__} operand={operand} result={result}"
        )
        return result

    def visit_Call(self, node: ast.Call) -> float:
        """访问函数调用节点"""
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")

        func_name = node.func.id
        if func_name not in self.ALLOWED_FUNCTIONS:
            raise ValueError(f"Unsupported function: {func_name}")

        # 求值所有参数
        args = [self.visit(arg) for arg in node.args]
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed")

        # 调用白名单函数
        func = self.ALLOWED_FUNCTIONS[func_name]
        result = func(*args)
        logger.debug(
            f"[SafeEval] [PROCESS] Call func={func_name} args={args} result={result}"
        )
        return result

    def generic_visit(self, node: ast.AST) -> Any:
        """拒绝所有未明确允许的节点类型"""
        node_type = type(node).__name__
        logger.warning(f"[SafeEval] [ERROR] Blocked unsafe node type={node_type}")
        raise ValueError(f"Unsupported AST node type: {node_type}")


def safe_eval_math(expr: str) -> float:
    """
    安全地求值数学表达式

    Args:
        expr: 数学表达式字符串，如 "2 + 3 * sqrt(16)"

    Returns:
        float: 计算结果，失败时返回 float('nan')

    Examples:
        >>> safe_eval_math("2 + 3")
        5.0
        >>> safe_eval_math("sqrt(16)")
        4.0
        >>> safe_eval_math("sin(pi / 2)")
        1.0
        >>> safe_eval_math("__import__('os').system('ls')")
        nan
    """
    if not isinstance(expr, str) or len(expr) > 500:
        return float("nan")

    logger.debug(f"[SafeEval] [ENTRY] safe_eval_math expr={expr[:50]}")

    try:
        # 解析表达式为 AST
        tree = ast.parse(expr, mode="eval")
        logger.debug("[SafeEval] [PROCESS] AST parsed successfully")

        # 使用白名单求值器
        evaluator = SafeMathEvaluator()
        result = evaluator.visit(tree)

        logger.debug(f"[SafeEval] [EXIT] safe_eval_math result={result}")
        return float(result)

    except Exception as e:
        logger.warning(
            f"[SafeEval] [ERROR] Evaluation failed expr={expr[:50]} error={type(e).__name__}: {e}"
        )
        return float("nan")


@lru_cache(maxsize=128)
def compile_math_expression(
    expression: str, variables: tuple[str, ...] = (), *, trig_degrees: bool = False
):
    """Build an immutable numeric callable from a validated expression tree.

    Args:
        expression: Arithmetic expression with at most 500 characters.
        variables: Positional variable names accepted by the callable.
        trig_degrees: Use PGF's degree convention for trigonometric functions.

    Returns:
        A callable returning a float, or NaN at undefined sample points.

    Raises:
        ValueError: The expression contains unsupported syntax or exceeds limits.
    """
    if not isinstance(expression, str) or len(expression) > 500:
        raise ValueError("Expression is too long")
    # TikZ/PGF writes powers as `^`. In Python `^` is bitwise XOR, which parses
    # cleanly and then silently computes the wrong value, so translating it here
    # rather than at each call site keeps a caller that forgets from producing
    # quietly incorrect curves.
    source = expression.strip().replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid arithmetic expression") from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Expression is too complex")
    functions = dict(SafeMathEvaluator.ALLOWED_FUNCTIONS)
    functions.update(
        {
            "ln": math.log,
            "min": min,
            "max": max,
            "deg": math.degrees,
            "rad": math.radians,
            "sinh": math.sinh,
            "cosh": math.cosh,
            "tanh": math.tanh,
        }
    )
    if trig_degrees:
        functions.update(
            {
                "sin": lambda x: math.sin(math.radians(x)),
                "cos": lambda x: math.cos(math.radians(x)),
                "tan": lambda x: math.tan(math.radians(x)),
                "asin": lambda x: math.degrees(math.asin(x)),
                "acos": lambda x: math.degrees(math.acos(x)),
                "atan": lambda x: math.degrees(math.atan(x)),
                "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
                "log": math.log10,
            }
        )

    def power(left, right):
        if abs(right) > 1000 or (abs(left) > 1000 and right > 10):
            raise ValueError("Power operation exceeds limits")
        value = left**right
        if (
            not isinstance(value, (float, int))
            or not math.isfinite(value)
            or abs(value) > 1e15
        ):
            raise ValueError("Power result exceeds limits")
        return value

    def build(node, depth=0):
        if depth > 20:
            raise ValueError("Expression nesting is too deep")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if isinstance(node.value, int) and node.value.bit_length() > 256:
                raise ValueError("Numeric constant is too large")
            value = float(node.value)
            if not math.isfinite(value):
                raise ValueError("Numeric constant is not finite")
            return lambda values: value
        if isinstance(node, ast.Name):
            if node.id in variables:
                index = variables.index(node.id)
                return lambda values: values[index]
            if node.id in SafeMathEvaluator.ALLOWED_CONSTANTS:
                value = SafeMathEvaluator.ALLOWED_CONSTANTS[node.id]
                return lambda values: value
            raise ValueError(f"Unsupported variable: {node.id}")
        if (
            isinstance(node, ast.BinOp)
            and type(node.op) in SafeMathEvaluator.ALLOWED_BINARY_OPS
        ):
            left, right = build(node.left, depth + 1), build(node.right, depth + 1)
            operation = (
                power
                if isinstance(node.op, ast.Pow)
                else SafeMathEvaluator.ALLOWED_BINARY_OPS[type(node.op)]
            )
            return lambda values: operation(left(values), right(values))
        if (
            isinstance(node, ast.UnaryOp)
            and type(node.op) in SafeMathEvaluator.ALLOWED_UNARY_OPS
        ):
            operand = build(node.operand, depth + 1)
            operation = SafeMathEvaluator.ALLOWED_UNARY_OPS[type(node.op)]
            return lambda values: operation(operand(values))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
            and not node.keywords
        ):
            function = functions[node.func.id]
            arguments = tuple(build(arg, depth + 1) for arg in node.args)
            if not arguments or len(arguments) > 8:
                raise ValueError("Unsupported function arity")
            return lambda values: function(
                *(argument(values) for argument in arguments)
            )
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    calculate = build(tree.body)

    def evaluate(*values):
        try:
            result = float(calculate(values))
            return result if math.isfinite(result) else math.nan
        except (ArithmeticError, ValueError, TypeError, IndexError):
            return math.nan

    return evaluate
